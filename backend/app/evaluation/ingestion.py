"""Explicit live smoke for upload -> parse -> vectors -> permission-filtered search."""
import argparse
from datetime import datetime, timezone
import json
from uuid import uuid4

from app.core.config import ROOT, get_settings
from app.core.db import connect, read_connection
from app.models.embedding import BailianEmbedding
from app.retrieval.search import search
from app.services import courses, documents
from app.workers.documents import run_once as parse_once
from app.workers.vectors import run_once as embed_once

TEXT = '# 事务原子性\n\n事务内的操作要么全部成功，要么全部回滚。转账时，应将扣款和入账放在同一事务中，中途失败时一起回滚，避免只扣钱不加钱。'
QUESTION = '为什么转账不能只扣钱，不加钱？程序中途出错怎么办？'


def demo_user(username):
    with read_connection() as db, db.cursor() as cursor:
        cursor.execute("SELECT id,role FROM users WHERE username=%s AND status='ACTIVE'", (username,))
        row = cursor.fetchone()
        if not row:
            raise ValueError('Active local demo account required')
        return row


def cleanup(course_id, code):
    connection = connect(root=True)
    keys = []
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT code FROM courses WHERE id=%s FOR UPDATE', (course_id,))
            row = cursor.fetchone()
            if not row or row['code'] != code or not code.startswith('LIVE-INGEST-'):
                raise ValueError('Refusing cleanup of an unrelated course')
            cursor.execute('SELECT storage_key FROM documents WHERE course_id=%s', (course_id,))
            keys = [row['storage_key'] for row in cursor.fetchall()]
            cursor.execute('DELETE ch FROM document_chunks ch JOIN documents d ON d.id=ch.document_id WHERE d.course_id=%s', (course_id,))
            cursor.execute('DELETE j FROM index_jobs j JOIN documents d ON d.id=j.document_id WHERE d.course_id=%s', (course_id,))
            for table in ('documents', 'enrollments', 'course_teachers', 'courses'):
                cursor.execute(f"DELETE FROM {table} WHERE {'id' if table == 'courses' else 'course_id'}=%s", (course_id,))
        connection.commit()
    finally:
        connection.close()
    # Only unlink this run's uploaded synthetic fixture after DB cleanup is acknowledged.
    for key in keys:
        documents.storage_path(key).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='最多两次短文本模型调用；创建并清理自己的临时业务课程，不处理历史文件')
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({'status': 'not_run', 'network_calls': 0}))
        return 0
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'source': 'real_bailian_api',
              'scope': 'Service-level upload, parsing, publication and SQL search; no HTTP login, answer generation or quality benchmark.',
              'input_provenance': 'Fixed self-authored transaction note and question; no existing uploads.',
              'calls': [], 'actual_billed_amount': None, 'fixture_cleaned': False, 'passed': False}
    course_id = None
    code = 'LIVE-INGEST-' + uuid4().hex
    try:
        provider = BailianEmbedding(get_settings())
        teacher = demo_user('teacher1')
        student = demo_user('student1')
        other = demo_user('student2')
        payload = {'code': code, 'name': '向量入库连通验证', 'description': 'Self-authored temporary fixture',
                   'capacity': 1, 'enrollment_status': 'OPEN'}
        course_id = courses.create_course(teacher, payload)['id']
        courses.change_enrollment(student, course_id, True)
        courses.update_course(teacher, course_id, {**payload, 'enrollment_status': 'CLOSED'})
        document_id = documents.save_document(student, course_id, '事务自编验证笔记', 'synthetic-note.md', TEXT.encode(), uuid4().hex)['id']
        report['parse_status'] = parse_once(document_id=document_id)['status']
        report['embedding_status'] = embed_once(document_id=document_id, provider=provider)['status']
        with read_connection() as db, db.cursor() as cursor:
            cursor.execute('SELECT model_calls FROM index_jobs WHERE document_id=%s AND content_version=1', (document_id,))
            calls = json.loads(cursor.fetchone()['model_calls'] or '[]')
            report['calls'] = [{k: c.get(k) for k in ('outcome', 'requested_model', 'total_tokens', 'request_id', 'elapsed_ms', 'profile')} for c in calls]
        if report['embedding_status'] != 'ready':
            raise ValueError('Document was not published')
        result = provider.embed([QUESTION], kind='query')
        report['calls'].append({'outcome': 'SUCCEEDED', 'requested_model': result.requested_model,
                                'total_tokens': result.total_tokens, 'request_id': result.request_id,
                                'elapsed_ms': result.elapsed_ms, 'profile': result.profile})
        with read_connection() as db, db.cursor() as cursor:
            params = {'course_id': course_id, 'profile': provider.profile,
                      'query_vector': result.vectors[0], 'dimensions': get_settings().embedding_dimensions}
            found = search(cursor, actor_id=student['id'], **params)
            hidden = search(cursor, actor_id=other['id'], **params)
        report['checks'] = {'ready': documents.get_document(student, document_id)['status'] == 'READY',
                            'searchable_preview': documents.chunk_preview(student, document_id)['searchable'],
                            'own_note_found': bool(found and found[0]['document_id'] == document_id),
                            'other_student_excluded': not hidden}
        report['retrieval_distances'] = [float(row['distance']) for row in found]
        report['reported_total_tokens'] = sum(c['total_tokens'] for c in report['calls']) if all(c['total_tokens'] is not None for c in report['calls']) else None
        report['passed'] = all(report['checks'].values())
    except Exception as error:
        report.update({'error_type': type(error).__name__, 'passed': False})
    finally:
        if course_id is not None:
            try:
                cleanup(course_id, code)
                report['fixture_cleaned'] = True
            except Exception as error:
                report.update({'cleanup_error_type': type(error).__name__, 'passed': False})
    target = ROOT / 'docs/verification' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-ingestion.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({**report, 'report_file': str(target)}, ensure_ascii=False, indent=2))
    return 0 if report['passed'] and report['fixture_cleaned'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
