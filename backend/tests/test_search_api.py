from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.db import connect
from app.models.embedding import EmbeddingBatch, EmbeddingError
from app.services import courses, documents, retrieval
from app.workers.vectors import run_once
from test_documents import course, client, login, STUDENT
from test_vector_worker import parsed, SyntheticProvider

pytestmark = pytest.mark.integration


class QueryProvider:
    profile = SyntheticProvider.profile

    def __init__(self, callback=None, fail=False):
        self.calls = 0
        self.callback = callback
        self.fail = fail

    def embed(self, texts, *, kind):
        assert kind == 'query' and len(texts) == 1
        self.calls += 1
        if self.callback: self.callback()
        if self.fail: raise EmbeddingError('EMBEDDING_TIMEOUT', 'Fixed timeout')
        return EmbeddingBatch(((1.0,) + (0.0,) * 1023,), self.profile, 'test-double', 7, 'query-test-id', 1)


@pytest.fixture
def indexed(course, monkeypatch):
    identifier = parsed(course)
    assert run_once(document_id=identifier, provider=SyntheticProvider())['status'] == 'ready'
    provider = QueryProvider()
    monkeypatch.setattr(retrieval, 'BailianEmbedding', lambda _: provider)
    return identifier, provider


def post(client, course_id, key=None, question='BCNF 是什么？'):
    return client.post(f'/api/v1/courses/{course_id}/search', json={'question': question, 'request_key': key or uuid4().hex})


def test_authenticated_results_and_idempotency(client, course, indexed):
    identifier, provider = indexed
    assert post(client, course).status_code == 401
    login(client)
    key = uuid4().hex
    first = post(client, course, key)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body['status'] == 'SUCCEEDED' and body['items'][0]['document_id'] == identifier
    assert 'BCNF' in body['items'][0]['content'] and body['query_tokens'] == 7
    assert post(client, course, key).json()['id'] == body['id']
    assert client.get(f"/api/v1/searches/{body['id']}").json()['items'] == body['items']
    assert provider.calls == 1
    assert post(client, course, key, question='换了一个问题').status_code == 409
    login(client, 'student2')
    assert client.get(f"/api/v1/searches/{body['id']}").status_code == 404
    assert provider.calls == 1


def test_no_authorized_ready_sources_does_not_call_model(client, course, monkeypatch):
    provider = QueryProvider()
    monkeypatch.setattr(retrieval, 'BailianEmbedding', lambda _: provider)
    login(client)
    result = post(client, course).json()
    assert result['status'] == 'SUCCEEDED' and result['items'] == []
    assert provider.calls == 0


@pytest.mark.parametrize('operation', ['delete', 'replace', 'withdraw'])
def test_cached_results_are_reauthorized(client, course, indexed, operation):
    identifier, provider = indexed
    login(client)
    key = uuid4().hex
    result = post(client, course, key).json()
    if operation == 'delete': documents.delete_document(STUDENT, identifier)
    elif operation == 'replace':
        documents.save_document(STUDENT, course, 'new', 'new.txt', b'new text', uuid4().hex,
                                document_id=identifier, expected_version=1)
    else: courses.change_enrollment(STUDENT, course, False)
    expected = 403 if operation == 'withdraw' else 409
    for response in (client.get(f"/api/v1/searches/{result['id']}"), post(client, course, key)):
        assert response.status_code == expected and 'BCNF' not in response.text
    assert provider.calls == 1


def test_handled_failure_is_not_retried(client, course, indexed):
    _, provider = indexed
    provider.fail = True
    login(client)
    key = uuid4().hex
    result = post(client, course, key).json()
    assert result['status'] == 'FAILED' and result['query_tokens'] is None
    assert post(client, course, key).json()['status'] == 'FAILED'
    assert provider.calls == 1


def test_rate_limit_counts_new_requests_only(client, course, indexed, monkeypatch):
    _, provider = indexed
    monkeypatch.setattr(get_settings(), 'retrieval_requests_per_minute', 1)
    login(client)
    key = uuid4().hex
    assert post(client, course, key).status_code == 200
    assert post(client, course, key).status_code == 200
    assert post(client, course).status_code == 429
    assert provider.calls == 1


def test_qualification_is_checked_before_and_after_model(client, course, indexed):
    _, provider = indexed
    login(client)
    provider.callback = lambda: courses.change_enrollment(STUDENT, course, False)
    assert post(client, course).status_code == 403
    assert provider.calls == 1
    assert post(client, course).status_code == 403
    assert provider.calls == 1


def test_deadline_prevents_late_result_and_keeps_usage(client, course, indexed):
    _, provider = indexed
    def expire():
        db = connect(root=True)
        try:
            with db.cursor() as cursor:
                cursor.execute('UPDATE retrieval_requests SET deadline_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE course_id=%s', (course,))
            db.commit()
        finally: db.close()
    provider.callback = expire
    login(client)
    key = uuid4().hex
    result = post(client, course, key).json()
    assert result['status'] == 'FAILED' and result['error_code'] == 'RETRIEVAL_TIMEOUT'
    assert result['items'] == [] and result['query_tokens'] == 7
    assert post(client, course, key).json()['status'] == 'FAILED'
    assert provider.calls == 1


def test_concurrent_same_key_invokes_provider_once(course, indexed):
    _, provider = indexed
    provider.callback = lambda: time.sleep(0.3)
    barrier = Barrier(2)
    key = uuid4().hex
    def submit(_):
        barrier.wait(timeout=10)
        return retrieval.submit(STUDENT, course, 'BCNF 是什么？', key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert provider.calls == 1
    assert len({row['id'] for row in results}) == 1
    assert retrieval.get_request(STUDENT, results[0]['id'])['status'] == 'SUCCEEDED'


def test_expiring_during_sql_does_not_publish_late_result(client, course, indexed, monkeypatch):
    original = retrieval.search
    def expire_after_search(cursor, **kwargs):
        found = original(cursor, **kwargs)
        cursor.execute('UPDATE retrieval_requests SET deadline_at=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE course_id=%s', (course,))
        return found
    monkeypatch.setattr(retrieval, 'search', expire_after_search)
    login(client)
    result = post(client, course).json()
    assert result['status'] == 'FAILED' and result['items'] == []
    assert result['query_tokens'] == 7


def test_validation_and_origin_guards_prevent_model_calls(client, course, indexed):
    _, provider = indexed
    login(client)
    assert post(client, course, question=' ').status_code == 422
    assert post(client, course, question='a' * 601).status_code == 422
    assert client.post(f'/api/v1/courses/{course}/search', headers={'Origin': 'https://untrusted.example'},
                       json={'question':'q','request_key':uuid4().hex}).status_code == 403
    assert provider.calls == 0
