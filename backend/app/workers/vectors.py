"""Single-document cloud embedding with atomic publication and durable call audit."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from time import monotonic
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.db import read_connection
from app.migrate import vector_dimension
from app.models.embedding import BailianEmbedding, EmbeddingError, MAX_BATCH_SIZE, MAX_TEXT_CHARACTERS
from app.workers.documents import Heartbeat
from app.workers.queue import claim_job, fail_job, locked_job, owned, renew_job

logger = logging.getLogger(__name__)


def owner_eligible(cursor, document):
    cursor.execute("SELECT role,status FROM users WHERE id=%s", (document['owner_id'],))
    owner = cursor.fetchone()
    if not owner or owner['status'] != 'ACTIVE':
        return False
    if document['kind'] == 'NOTE' and owner['role'] == 'STUDENT':
        cursor.execute("SELECT id FROM enrollments WHERE course_id=%s AND student_id=%s AND status='ACTIVE'", (document['course_id'], document['owner_id']))
    elif document['kind'] == 'TEACHING' and owner['role'] == 'TEACHER':
        cursor.execute("SELECT id FROM course_teachers WHERE course_id=%s AND teacher_id=%s", (document['course_id'], document['owner_id']))
    else:
        return False
    return cursor.fetchone() is not None


def read_chunks(job):
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return None
        if not owner_eligible(cursor, document):
            raise EmbeddingError('EMBEDDING_ACCESS_REVOKED', '上传者的课程处理资格已失效')
        cursor.execute("SELECT id,chunk_index,content FROM document_chunks WHERE document_id=%s AND content_version=%s ORDER BY chunk_index", (job['document_id'], job['content_version']))
        return cursor.fetchall()


def history(current):
    value = current.get('model_calls')
    return json.loads(value) if isinstance(value, str) else (value or [])


def begin_call(job, *, batch, profile, batch_index):
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return None
        if not owner_eligible(cursor, document):
            raise EmbeddingError('EMBEDDING_ACCESS_REVOKED', '上传者的课程处理资格已失效')
        record = {'call_id': uuid4().hex, 'worker_token': job['worker_token'], 'attempt': job['attempt_count'],
                  'retry_round': current['retry_round'], 'batch_index': batch_index, 'profile': profile,
                  'started_at': datetime.now(timezone.utc).isoformat(), 'text_count': len(batch),
                  'input_characters': sum(len(row['content']) for row in batch), 'outcome': 'IN_FLIGHT',
                  'total_tokens': None, 'request_id': None}
        calls = history(current)
        calls.append(record)
        cursor.execute('UPDATE index_jobs SET model_calls=%s WHERE id=%s', (json.dumps(calls), job['id']))
        return record['call_id']


def finish_call(job, call_id, *, result=None, error_code=None):
    # Late audit completion may update its own call record after lease loss; never business state.
    with locked_job(job) as (cursor, current, document):
        if not current:
            return
        calls = history(current)
        for record in calls:
            if record['call_id'] == call_id and record['worker_token'] == job['worker_token']:
                record.update({'outcome': 'SUCCEEDED' if result else 'FAILED',
                               'completed_at': datetime.now(timezone.utc).isoformat(), 'error_code': error_code})
                if result:
                    record.update({'total_tokens': result.total_tokens, 'request_id': result.request_id,
                                   'elapsed_ms': result.elapsed_ms, 'requested_model': result.requested_model})
                cursor.execute('UPDATE index_jobs SET model_calls=%s WHERE id=%s', (json.dumps(calls), job['id']))
                return
        raise ValueError('Embedding call audit entry missing')


def snapshot(rows):
    return [(row['id'], row['chunk_index'], hashlib.sha256(row['content'].encode()).hexdigest()) for row in rows]


def publish_vectors(job, chunks, vectors, profile, dimensions):
    if not chunks or len(chunks) != len(vectors) or not profile or len(profile) > 255:
        raise ValueError('Invalid vector publication')
    for vector in vectors:
        if len(vector) != dimensions or any(type(x) not in (float, int) or not math.isfinite(x) or abs(x) > 3.4028234663852886e38 for x in vector):
            raise ValueError('Invalid vector publication')
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('Invalid vector publication')
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return False
        if not owner_eligible(cursor, document):
            raise EmbeddingError('EMBEDDING_ACCESS_REVOKED', '上传者的课程处理资格已失效')
        cursor.execute('SELECT id,chunk_index,content FROM document_chunks WHERE document_id=%s AND content_version=%s ORDER BY chunk_index FOR UPDATE', (job['document_id'], job['content_version']))
        if snapshot(cursor.fetchall()) != snapshot(chunks):
            raise ValueError('Parsed chunks changed before publication')
        cursor.execute('UPDATE document_chunks SET is_current=FALSE WHERE document_id=%s', (job['document_id'],))
        cursor.executemany('UPDATE document_chunks SET embedding=%s,embedding_profile=%s,is_current=TRUE WHERE id=%s AND document_id=%s AND content_version=%s',
                           [(json.dumps(list(vector), allow_nan=False), profile, row['id'], job['document_id'], job['content_version']) for row, vector in zip(chunks, vectors, strict=True)])
        # A slow batch of SQL updates must not publish on a lease that expired while holding locks.
        cursor.execute('SELECT lease_until>UTC_TIMESTAMP(6) AS valid FROM index_jobs WHERE id=%s', (job['id'],))
        if not cursor.fetchone()['valid']:
            raise EmbeddingError('EMBEDDING_LEASE_LOST', '发布时任务租约已失效')
        cursor.execute("UPDATE documents SET status='READY',error_code=NULL,error_detail=NULL WHERE id=%s", (job['document_id'],))
        cursor.execute("UPDATE index_jobs SET status='SUCCEEDED',worker_token=NULL,lease_until=NULL,error_code=NULL WHERE id=%s", (job['id'],))
        return True


def process_job(job, provider):
    settings = get_settings()
    with Heartbeat(job) as heartbeat:
        try:
            chunks = read_chunks(job)
            if chunks is None:
                return 'lease_lost'
            if not chunks or len(chunks) > settings.embedding_max_chunks_per_document:
                raise EmbeddingError('EMBEDDING_CHUNK_LIMIT', '片段数为空或超过本轮处理上限，请检查或拆分资料')
            if any(row['chunk_index'] != index or not row['content'].strip() or len(row['content']) > MAX_TEXT_CHARACTERS for index, row in enumerate(chunks)):
                raise EmbeddingError('EMBEDDING_INVALID_CHUNKS', '片段格式不符合当前模型接入范围')
            vectors = []
            profile = None
            for offset in range(0, len(chunks), MAX_BATCH_SIZE):
                if heartbeat.lost.is_set():
                    return 'lease_lost'
                if monotonic() - heartbeat.started >= settings.worker_task_timeout_seconds:
                    raise EmbeddingError('PROCESSING_TIMEOUT', '向量化处理超时')
                if not renew_job(job):
                    return 'lease_lost'
                batch = chunks[offset:offset + MAX_BATCH_SIZE]
                call_id = begin_call(job, batch=batch, profile=provider.profile, batch_index=offset // MAX_BATCH_SIZE)
                if call_id is None:
                    return 'lease_lost'
                try:
                    result = provider.embed([row['content'] for row in batch], kind='document')
                except EmbeddingError as error:
                    finish_call(job, call_id, error_code=error.code)
                    raise
                except Exception:
                    finish_call(job, call_id, error_code='EMBEDDING_FAILED')
                    raise
                finish_call(job, call_id, result=result)
                if len(result.vectors) != len(batch) or result.profile != provider.profile or (profile and profile != result.profile):
                    raise EmbeddingError('EMBEDDING_INVALID_RESPONSE', '向量配置或数量不匹配')
                profile = result.profile
                vectors.extend(result.vectors)
            if heartbeat.lost.is_set() or monotonic() - heartbeat.started >= settings.worker_task_timeout_seconds:
                raise EmbeddingError('PROCESSING_TIMEOUT', '向量化处理超时')
            return 'ready' if publish_vectors(job, chunks, vectors, profile, settings.embedding_dimensions) else 'lease_lost'
        except Exception as error:
            code = error.code if isinstance(error, EmbeddingError) else 'EMBEDDING_FAILED'
            logger.warning('Embedding job stopped: job=%s code=%s', job['id'], code)
            # Paid calls are never automatically retried after a handled failure.
            return fail_job(job, code, permanent=True) or 'lease_lost'


def run_once(*, document_id: int, provider):
    with read_connection() as connection, connection.cursor() as cursor:
        if vector_dimension(cursor) != get_settings().embedding_dimensions:
            raise EmbeddingError('EMBEDDING_SCHEMA_MISMATCH', '请先迁移向量列并核对模型维度')
    job = claim_job(document_id=document_id, stage='EMBEDDING')
    if job is None:
        return {'status': 'idle'}
    return {'job_id': job['id'], 'document_id': document_id, 'status': process_job(job, provider)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', type=int)
    parser.add_argument('--live', action='store_true', help='显式发送指定资料片段到百炼，可能计费；仅处理这一份资料')
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'status': 'not_run', 'network_calls': 0, 'instruction': '需要 --live --document-id 指定一份资料'}, ensure_ascii=False))
        return 0
    if args.document_id is None or args.document_id < 1:
        parser.error('--live requires a positive --document-id')
    try:
        result = run_once(document_id=args.document_id, provider=BailianEmbedding(get_settings()))
    except (EmbeddingError, ValidationError) as error:
        result = {'status': 'failed', 'code': error.code if isinstance(error, EmbeddingError) else 'INVALID_LOCAL_CONFIG'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'idle', 'ready'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
