"""One paid query per durable idempotency key; reauthorize every returned fragment."""
import hashlib
import json

from app.core.config import get_settings
from app.core.db import read_connection, transaction
from app.core.errors import BusinessError
from app.migrate import vector_dimension
from app.models.embedding import BailianEmbedding, EmbeddingError
from app.retrieval.search import AUTHORIZED_FROM, search
from app.services.courses import lock_course
from app.services.documents import require_course_access


def decode(value, default):
    return json.loads(value) if isinstance(value, str) else (value if value is not None else default)


def access(cursor, user, course_id):
    lock_course(cursor, course_id)
    cursor.execute('SELECT id,role,status FROM users WHERE id=%s FOR UPDATE', (user['id'],))
    actor = cursor.fetchone()
    if not actor or actor['status'] != 'ACTIVE':
        raise BusinessError(401, 'AUTH_REQUIRED', '请重新登录')
    require_course_access(cursor, actor, course_id)


def expire(cursor, row):
    if row['status'] == 'PROCESSING':
        cursor.execute("UPDATE retrieval_requests SET status='FAILED',error_code='RETRIEVAL_TIMEOUT',completed_at=UTC_TIMESTAMP(6) WHERE id=%s AND deadline_at<=UTC_TIMESTAMP(6)", (row['id'],))
        if cursor.rowcount:
            row.update(status='FAILED', error_code='RETRIEVAL_TIMEOUT')


def present(cursor, row, user):
    expire(cursor, row)
    call = decode(row['model_call'], None)
    result = {'id': row['id'], 'course_id': row['course_id'], 'question': row['question'],
              'status': row['status'], 'error_code': row['error_code'], 'items': [],
              'query_tokens': call.get('total_tokens') if call else None,
              'usage_known': bool(call and call.get('total_tokens') is not None),
              'notice': '检索片段不等于回答依据充分，请核对原文；尚未生成答案。'}
    if row['status'] != 'SUCCEEDED':
        return result
    evidence = decode(row['evidence'], [])
    if not evidence:
        result['notice'] = '当前课程没有可用于此次检索的已向量化资料，请先上传并完成向量化。'
        return result
    cursor.execute('SELECT ch.id AS chunk_id,d.id AS document_id,d.title,d.kind,ch.content,ch.content_version,ch.page_number,ch.section_title,ch.chunk_index'
                   + AUTHORIZED_FROM + ' AND ch.id IN (' + ','.join(['%s'] * len(evidence)) + ')',
                   (user['id'], row['course_id'], row['embedding_profile'], *[item['chunk_id'] for item in evidence]))
    available = {item['chunk_id']: item for item in cursor.fetchall()}
    if any(item['chunk_id'] not in available or available[item['chunk_id']]['content_version'] != item['content_version'] for item in evidence):
        raise BusinessError(409, 'EVIDENCE_CHANGED', '检索资料已变化或不再可访问，请重新检索')
    result['items'] = [{**available[item['chunk_id']], 'rank': index, 'distance': item['distance']}
                       for index, item in enumerate(evidence, 1)]
    return result


def get_request(user, request_id):
    # Ownership before course lookup avoids exposing another user's request/course.
    with read_connection() as db, db.cursor() as cursor:
        cursor.execute('SELECT course_id FROM retrieval_requests WHERE id=%s AND user_id=%s', (request_id, user['id']))
        row = cursor.fetchone()
        if not row:
            raise BusinessError(404, 'RETRIEVAL_NOT_FOUND', '检索记录不存在')
    with transaction() as db, db.cursor() as cursor:
        access(cursor, user, row['course_id'])
        cursor.execute('SELECT * FROM retrieval_requests WHERE id=%s AND user_id=%s FOR UPDATE', (request_id, user['id']))
        return present(cursor, cursor.fetchone(), user)


def submit(user, course_id, question, request_key):
    digest = hashlib.sha256(json.dumps([course_id, question], ensure_ascii=False).encode()).hexdigest()
    settings = get_settings()
    with transaction() as db, db.cursor() as cursor:
        access(cursor, user, course_id)
        cursor.execute('SELECT * FROM retrieval_requests WHERE user_id=%s AND request_key=%s FOR UPDATE', (user['id'], request_key))
        existing = cursor.fetchone()
        if existing:
            if existing['payload_hash'] != digest:
                raise BusinessError(409, 'IDEMPOTENCY_CONFLICT', '同一检索标识对应了不同问题，请重新提交')
            return present(cursor, existing, user)
        cursor.execute('SELECT COUNT(*) AS n FROM retrieval_requests WHERE user_id=%s AND created_at>TIMESTAMPADD(SECOND,-60,UTC_TIMESTAMP(6))', (user['id'],))
        if cursor.fetchone()['n'] >= settings.retrieval_requests_per_minute:
            raise BusinessError(429, 'RETRIEVAL_RATE_LIMIT', '检索过于频繁，请一分钟后再试')
        try:
            provider = BailianEmbedding(settings)
        except EmbeddingError:
            raise BusinessError(503, 'EMBEDDING_NOT_CONFIGURED', '检索模型尚未配置，请联系维护者') from None
        if vector_dimension(cursor) != settings.embedding_dimensions:
            raise BusinessError(503, 'EMBEDDING_SCHEMA_MISMATCH', '向量配置与数据库不匹配，请联系维护者')
        cursor.execute('SELECT ch.id' + AUTHORIZED_FROM + ' LIMIT 1', (user['id'], course_id, provider.profile))
        has_sources = cursor.fetchone() is not None
        call = {'outcome': 'IN_FLIGHT', 'total_tokens': None, 'profile': provider.profile} if has_sources else None
        cursor.execute("""INSERT INTO retrieval_requests(user_id,course_id,request_key,payload_hash,question,embedding_profile,status,evidence,model_call,deadline_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)))""",
                       (user['id'], course_id, request_key, digest, question, provider.profile,
                        'PROCESSING' if has_sources else 'SUCCEEDED', None if has_sources else '[]',
                        json.dumps(call) if call else None, settings.retrieval_deadline_seconds))
        request_id = cursor.lastrowid
    if not has_sources:
        return get_request(user, request_id)
    try:
        result = provider.embed([question], kind='query')
        if result.profile != provider.profile or len(result.vectors) != 1:
            raise EmbeddingError('EMBEDDING_INVALID_RESPONSE', '查询向量配置不匹配')
        call.update(outcome='SUCCEEDED', total_tokens=result.total_tokens, request_id=result.request_id,
                    elapsed_ms=result.elapsed_ms, requested_model=result.requested_model)
    except Exception as error:
        code = error.code if isinstance(error, EmbeddingError) else 'RETRIEVAL_FAILED'
        call.update(outcome='FAILED', error_code=code)
        with transaction() as db, db.cursor() as cursor:
            cursor.execute("UPDATE retrieval_requests SET model_call=%s,status='FAILED',error_code=%s,completed_at=UTC_TIMESTAMP(6) WHERE id=%s", (json.dumps(call), code, request_id))
        return get_request(user, request_id)
    # Call accounting survives later permission/version rejection; no private text in the audit.
    with transaction() as db, db.cursor() as cursor:
        cursor.execute('UPDATE retrieval_requests SET model_call=%s WHERE id=%s', (json.dumps(call), request_id))
    with transaction() as db, db.cursor() as cursor:
        access(cursor, user, course_id)
        cursor.execute('SELECT * FROM retrieval_requests WHERE id=%s FOR UPDATE', (request_id,))
        row = cursor.fetchone()
        expire(cursor, row)
        if row['status'] == 'PROCESSING':
            hits = search(cursor, actor_id=user['id'], course_id=course_id, profile=provider.profile,
                          query_vector=result.vectors[0], dimensions=settings.embedding_dimensions, top_k=2)
            evidence = [{'chunk_id': hit['chunk_id'], 'content_version': hit['content_version'], 'distance': float(hit['distance'])} for hit in hits]
            cursor.execute("UPDATE retrieval_requests SET evidence=%s,status='SUCCEEDED',completed_at=UTC_TIMESTAMP(6) WHERE id=%s AND status='PROCESSING' AND deadline_at>UTC_TIMESTAMP(6)", (json.dumps(evidence), request_id))
            if cursor.rowcount:
                row.update(status='SUCCEEDED', evidence=evidence, model_call=call)
            else:
                expire(cursor, row)
        return present(cursor, row, user)
