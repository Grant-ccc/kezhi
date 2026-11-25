"""Actual DB publication tests with clearly synthetic provider vectors; no paid calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import time

import pymysql
import pytest

from app.core.config import get_settings
from app.core.db import read_connection
from app.models.embedding import EmbeddingBatch, EmbeddingError
from app.retrieval.search import candidates, search
from app.services import courses, documents
from app.workers import vectors
from app.workers.queue import claim_job, fail_job, renew_job
from app.workers.documents import Heartbeat, run_once as parse_once
from test_documents import course, STUDENT, OTHER, client, login
from test_worker import create, task, expire

pytestmark = pytest.mark.integration


class SyntheticProvider:
    profile = 'test-double:publication:1024'

    def __init__(self, callback=None, fail_batch=None):
        self.calls = 0
        self.callback = callback
        self.fail_batch = fail_batch

    def embed(self, texts, *, kind):
        assert kind == 'document'
        self.calls += 1
        if self.callback: self.callback()
        if self.calls == self.fail_batch:
            raise EmbeddingError('EMBEDDING_TIMEOUT', 'Fixed test failure')
        return EmbeddingBatch(tuple((1.0,) + (0.0,) * 1023 for _ in texts), self.profile,
                              'not-a-real-model', 10, f'test-{self.calls}', 1)


def parsed(course_id, data=None):
    identifier = create(course_id) if data is None else create(course_id, data)
    assert parse_once(document_id=identifier)['status'] == 'parsed'
    return identifier


def rows(identifier):
    with read_connection() as db, db.cursor() as cursor:
        cursor.execute('SELECT id,is_current,embedding_profile,embedding IS NOT NULL AS has_vector FROM document_chunks WHERE document_id=%s ORDER BY id', (identifier,))
        return cursor.fetchall()


def test_publish_and_actual_permission_filtered_search(course, client):
    identifier = parsed(course)
    provider = SyntheticProvider()
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'ready'
    assert provider.calls == 1
    info = documents.get_document(STUDENT, identifier)
    assert info['status'] == 'READY' and info['index_stage'] == 'EMBEDDING'
    assert documents.chunk_preview(STUDENT, identifier)['searchable']
    assert task(identifier)['status'] == 'SUCCEEDED'
    audit = json.loads(task(identifier)['model_calls'])
    assert len(audit) == 1 and audit[0]['outcome'] == 'SUCCEEDED' and audit[0]['total_tokens'] == 10
    assert all(r['is_current'] and r['has_vector'] for r in rows(identifier))
    with read_connection() as db, db.cursor() as cursor:
        params = {'course_id': course, 'profile': provider.profile}
        assert candidates(cursor, actor_id=STUDENT['id'], **params)
        assert not candidates(cursor, actor_id=OTHER['id'], **params)
        result = search(cursor, actor_id=STUDENT['id'], query_vector=[1.0] + [0.0] * 1023, dimensions=1024, **params)
        assert result[0]['document_id'] == identifier
    login(client)
    assert client.get(f'/api/v1/documents/{identifier}/chunks').json()['searchable']
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'idle'
    assert provider.calls == 1


def test_parser_and_vector_worker_do_not_claim_each_others_stage(course):
    identifier = create(course)
    provider = SyntheticProvider()
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'idle'
    assert parse_once(document_id=identifier)['status'] == 'parsed'
    assert claim_job(document_id=identifier) is None
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'ready'


def test_concurrent_embedding_claim_has_one_owner(course):
    identifier = parsed(course)
    barrier = Barrier(2)
    def claim(_):
        barrier.wait(timeout=10)
        return claim_job(document_id=identifier, stage='EMBEDDING')
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(claim, range(2)))
    assert sum(j is not None for j in jobs) == 1
    assert task(identifier)['attempt_count'] == 1


def test_batch_failure_never_publishes_and_manual_retry_preserves_audit(course):
    identifier = parsed(course, ('# Long fixture\n' + 'synthetic text ' * 500).encode())
    assert len(rows(identifier)) > 8
    provider = SyntheticProvider(fail_batch=2)
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'failed'
    assert provider.calls == 2 and documents.get_document(STUDENT, identifier)['can_retry']
    assert all(not row['is_current'] and not row['has_vector'] for row in rows(identifier))
    assert [r['outcome'] for r in json.loads(task(identifier)['model_calls'])] == ['SUCCEEDED', 'FAILED']
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'idle'
    documents.retry_document(STUDENT, identifier, 1, 0)
    assert claim_job(document_id=identifier) is None
    assert vectors.run_once(document_id=identifier, provider=SyntheticProvider())['status'] == 'ready'
    assert len(json.loads(task(identifier)['model_calls'])) > 2


@pytest.mark.parametrize('operation', ['delete', 'replace', 'withdraw'])
def test_invalidated_during_api_call_cannot_publish(course, operation):
    identifier = parsed(course)
    def invalidate():
        if operation == 'delete': documents.delete_document(STUDENT, identifier)
        elif operation == 'replace':
            from uuid import uuid4
            documents.save_document(STUDENT, course, 'new', 'new.txt', b'new text', uuid4().hex,
                                    document_id=identifier, expected_version=1)
        else: courses.change_enrollment(STUDENT, course, False)
    provider = SyntheticProvider(callback=invalidate)
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == ('failed' if operation == 'withdraw' else 'lease_lost')
    assert all(not row['is_current'] for row in rows(identifier))
    with read_connection() as db, db.cursor() as cursor:
        assert not candidates(cursor, actor_id=STUDENT['id'], course_id=course, profile=provider.profile)


def test_expired_old_worker_cannot_overwrite_new_vectors(course):
    identifier = parsed(course)
    old = claim_job(document_id=identifier, stage='EMBEDDING')
    chunks = vectors.read_chunks(old)
    call_id = vectors.begin_call(old, batch=chunks, profile=SyntheticProvider.profile, batch_index=0)
    expire(old)
    assert not renew_job(old)
    new = claim_job(document_id=identifier, stage='EMBEDDING')
    assert vectors.process_job(new, SyntheticProvider()) == 'ready'
    result = SyntheticProvider().embed([c['content'] for c in chunks], kind='document')
    vectors.finish_call(old, call_id, result=result)
    assert not vectors.publish_vectors(old, chunks, result.vectors, result.profile, 1024)
    assert not fail_job(old, 'EMBEDDING_FAILED', permanent=True)
    assert documents.get_document(STUDENT, identifier)['status'] == 'READY'
    assert len(json.loads(task(identifier)['model_calls'])) == 2


def test_chunk_limit_prevents_all_paid_requests(course, monkeypatch):
    identifier = parsed(course, ('long text ' * 300).encode())
    monkeypatch.setattr(get_settings(), 'embedding_max_chunks_per_document', 1)
    provider = SyntheticProvider()
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'failed'
    assert provider.calls == 0 and task(identifier)['error_code'] == 'EMBEDDING_CHUNK_LIMIT'


def test_dimension_mismatch_prevents_claim_and_paid_call(course, monkeypatch):
    identifier = parsed(course)
    monkeypatch.setattr(get_settings(), 'embedding_dimensions', 256)
    provider = SyntheticProvider()
    with pytest.raises(EmbeddingError) as error:
        vectors.run_once(document_id=identifier, provider=provider)
    assert error.value.code == 'EMBEDDING_SCHEMA_MISMATCH'
    assert provider.calls == 0 and documents.get_document(STUDENT, identifier)['status'] == 'PARSED'


def test_atomic_vector_update_rollback(course, monkeypatch):
    identifier = parsed(course, ('long text ' * 100).encode())
    original = pymysql.cursors.Cursor.executemany
    def partial(cursor, query, args):
        if 'UPDATE document_chunks SET embedding=' in query:
            original(cursor, query, args[:1])
            raise RuntimeError('Synthetic publication interruption')
        return original(cursor, query, args)
    monkeypatch.setattr(pymysql.cursors.Cursor, 'executemany', partial)
    assert vectors.run_once(document_id=identifier, provider=SyntheticProvider())['status'] == 'failed'
    assert all(not row['is_current'] and not row['has_vector'] for row in rows(identifier))


def test_owner_withdrawal_before_call_prevents_external_send(course):
    identifier = parsed(course)
    courses.change_enrollment(STUDENT, course, False)
    provider = SyntheticProvider()
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'failed'
    assert provider.calls == 0


def test_embedding_heartbeat_renews_and_deadline_prevents_publication(course, monkeypatch):
    identifier = parsed(course)
    monkeypatch.setattr(get_settings(), 'worker_lease_seconds', 3)
    job = claim_job(document_id=identifier, stage='EMBEDDING')
    before = task(identifier)['lease_until']
    with Heartbeat(job):
        time.sleep(1.2)
        assert task(identifier)['lease_until'] > before
    monkeypatch.setattr(get_settings(), 'worker_task_timeout_seconds', 1)
    provider = SyntheticProvider(callback=lambda: time.sleep(1.1))
    assert vectors.process_job(job, provider) == 'failed'
    assert all(not r['is_current'] for r in rows(identifier))


def test_default_vector_command_never_calls_network(monkeypatch, capsys):
    monkeypatch.setattr(vectors, 'BailianEmbedding', lambda *_: pytest.fail('Default command must stay offline'))
    assert vectors.main([]) == 0
    assert json.loads(capsys.readouterr().out)['network_calls'] == 0


@pytest.mark.parametrize('operation', ['delete', 'replace', 'withdraw'])
def test_ready_document_is_immediately_removed_from_search(course, operation):
    identifier = parsed(course)
    provider = SyntheticProvider()
    assert vectors.run_once(document_id=identifier, provider=provider)['status'] == 'ready'
    if operation == 'delete': documents.delete_document(STUDENT, identifier)
    elif operation == 'replace':
        from uuid import uuid4
        documents.save_document(STUDENT, course, 'new', 'new.txt', b'new text', uuid4().hex,
                                document_id=identifier, expected_version=1)
    else: courses.change_enrollment(STUDENT, course, False)
    with read_connection() as db, db.cursor() as cursor:
        assert not search(cursor, actor_id=STUDENT['id'], course_id=course, profile=provider.profile,
                          query_vector=[1.0] + [0.0] * 1023, dimensions=1024)


def test_lease_expiring_during_sql_updates_rolls_back_publication(course, monkeypatch):
    identifier = parsed(course)
    monkeypatch.setattr(get_settings(), 'worker_lease_seconds', 3)
    original = pymysql.cursors.Cursor.executemany
    def slow(cursor, query, args):
        result = original(cursor, query, args)
        if 'UPDATE document_chunks SET embedding=' in query:
            time.sleep(3.2)  # renewal waits on the same course lock held by this transaction
        return result
    monkeypatch.setattr(pymysql.cursors.Cursor, 'executemany', slow)
    assert vectors.run_once(document_id=identifier, provider=SyntheticProvider())['status'] == 'lease_lost'
    assert all(not row['is_current'] and not row['has_vector'] for row in rows(identifier))
