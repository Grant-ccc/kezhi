from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
import time
from uuid import uuid4

import pymysql
import pytest

from app.core.config import get_settings
from app.core.db import connect, read_connection
from app.core.errors import BusinessError
from app.documents.chunking import split_pages
from app.documents.extraction import TextPage
from app.services import courses, documents
from app.workers.documents import Heartbeat, process_job, run_once
from app.workers.queue import claim_job, fail_job, publish_chunks, renew_job
from test_documents import course, client, login, STUDENT, TEXT

pytestmark = pytest.mark.integration


def create(course_id, data=TEXT):
    return documents.save_document(STUDENT,course_id,'worker 验证笔记','worker.md',data,uuid4().hex)['id']


def task(document_id):
    with read_connection() as db, db.cursor() as cur:
        cur.execute('SELECT * FROM index_jobs WHERE document_id=%s ORDER BY content_version DESC LIMIT 1',(document_id,))
        return cur.fetchone()


def expire(job):
    db=connect(root=True)
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE index_jobs SET lease_until=TIMESTAMPADD(SECOND,-1,UTC_TIMESTAMP(6)) WHERE id=%s",(job['id'],))
        db.commit()
    finally:db.close()


def due(document_id):
    db=connect(root=True)
    try:
        with db.cursor() as cur:cur.execute('UPDATE index_jobs SET next_run_at=UTC_TIMESTAMP(6) WHERE document_id=%s',(document_id,))
        db.commit()
    finally:db.close()


def test_parse_publication_is_atomic_and_not_searchable(course):
    identifier=create(course)
    assert run_once(document_id=identifier)['status']=='parsed'
    info=documents.get_document(STUDENT,identifier)
    assert info['status']=='PARSED' and info['index_status']=='PARSED' and info['chunk_count']>0
    assert task(identifier)['worker_token'] is None and task(identifier)['lease_until'] is None
    preview=documents.chunk_preview(STUDENT,identifier)
    assert not preview['searchable'] and 'BCNF' in preview['items'][0]['content']
    with read_connection() as db, db.cursor() as cur:
        cur.execute('SELECT is_current,embedding_profile FROM document_chunks WHERE document_id=%s',(identifier,))
        assert all(not row['is_current'] and row['embedding_profile'] is None for row in cur.fetchall())
    assert run_once(document_id=identifier)['status']=='idle'


def test_concurrent_claim_only_one_owner(course):
    identifier=create(course)
    barrier=Barrier(2)
    def claim(_):barrier.wait(timeout=10);return claim_job(document_id=identifier)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,range(2)))
    assert sum(row is not None for row in results)==1
    assert task(identifier)['attempt_count']==1


def test_expired_worker_cannot_overwrite_new_success(course):
    identifier=create(course)
    old=claim_job(document_id=identifier)
    expire(old)
    assert not renew_job(old)
    new=claim_job(document_id=identifier)
    assert new['worker_token']!=old['worker_token'] and new['attempt_count']==2
    assert process_job(new)=='parsed'
    assert not fail_job(old,'PROCESSING_FAILED')
    assert not publish_chunks(old,split_pages([TextPage('stale private text')]))
    assert documents.get_document(STUDENT,identifier)['status']=='PARSED'
    assert 'stale private text' not in str(documents.chunk_preview(STUDENT,identifier))


def test_repeated_crashes_exhaust_attempts(course,monkeypatch):
    monkeypatch.setattr(get_settings(),'worker_max_attempts',2)
    identifier=create(course)
    expire(claim_job(document_id=identifier))
    expire(claim_job(document_id=identifier))
    assert claim_job(document_id=identifier) is None
    info=documents.get_document(STUDENT,identifier)
    assert info['status']=='FAILED' and info['can_retry'] and info['attempt_count']==2


def test_transient_failures_retry_then_stop(course,monkeypatch):
    monkeypatch.setattr(get_settings(),'worker_max_attempts',2)
    identifier=create(course)
    def broken(*_):raise RuntimeError('Synthetic parser failure')
    assert process_job(claim_job(document_id=identifier),parser=broken)=='retry_scheduled'
    assert task(identifier)['status']=='PENDING' and claim_job(document_id=identifier) is None
    due(identifier)
    assert process_job(claim_job(document_id=identifier),parser=broken)=='failed'
    assert task(identifier)['status']=='FAILED' and documents.get_document(STUDENT,identifier)['status']=='FAILED'


def test_manual_retry_round_is_guarded_and_recovers(course):
    identifier=create(course)
    job=claim_job(document_id=identifier)
    path=documents.storage_path(job['storage_key'])
    path.unlink()
    assert process_job(job)=='failed'
    path.write_bytes(TEXT)
    documents.retry_document(STUDENT,identifier,1,0)
    with pytest.raises(BusinessError) as error:documents.retry_document(STUDENT,identifier,1,0)
    assert error.value.code=='RETRY_CONFLICT'
    assert task(identifier)['retry_round']==1 and task(identifier)['attempt_count']==0
    assert run_once(document_id=identifier)['status']=='parsed'


@pytest.mark.parametrize('operation',['delete','replace'])
def test_invalidated_running_job_cannot_write(course,operation):
    identifier=create(course)
    job=claim_job(document_id=identifier)
    if operation=='delete':documents.delete_document(STUDENT,identifier)
    else:documents.save_document(STUDENT,course,'新版','new.txt',b'New source text',uuid4().hex,document_id=identifier,expected_version=1)
    assert process_job(job)=='lease_lost'
    assert not fail_job(job,'PROCESSING_FAILED')
    with read_connection() as db, db.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM document_chunks WHERE document_id=%s AND content_version=1',(identifier,))
        assert cur.fetchone()['n']==0
    if operation=='replace':assert run_once(document_id=identifier)['status']=='parsed'


def test_heartbeat_extends_lease(course,monkeypatch):
    monkeypatch.setattr(get_settings(),'worker_lease_seconds',3)
    identifier=create(course)
    job=claim_job(document_id=identifier)
    first=task(identifier)['lease_until']
    with Heartbeat(job) as heartbeat:
        time.sleep(1.2)
        assert not heartbeat.lost.is_set()
        assert task(identifier)['lease_until']>first


def test_chunk_insert_failure_does_not_publish_partial_text(course,monkeypatch):
    identifier=create(course,('# 标题\n'+'长段落测试。'*200).encode())
    job=claim_job(document_id=identifier)
    original=pymysql.cursors.Cursor.executemany
    def partial(cursor,query,args):
        if 'INSERT INTO document_chunks' in query:
            original(cursor,query,args[:1])
            raise RuntimeError('Injected interruption after first chunk')
        return original(cursor,query,args)
    with monkeypatch.context() as patch:
        patch.setattr(pymysql.cursors.Cursor,'executemany',partial)
        assert process_job(job)=='retry_scheduled'
    with read_connection() as db, db.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM document_chunks WHERE document_id=%s',(identifier,))
        assert cur.fetchone()['n']==0
    assert documents.get_document(STUDENT,identifier)['status']=='PROCESSING'


def test_file_integrity_failure_is_not_silently_parsed(course):
    identifier=create(course)
    job=claim_job(document_id=identifier)
    documents.storage_path(job['storage_key']).write_bytes(b'Changed on disk')
    assert process_job(job)=='failed'
    assert task(identifier)['error_code']=='FILE_CHANGED'


def test_chunk_preview_and_retry_enforce_permissions(client,course):
    identifier=create(course)
    run_once(document_id=identifier)
    for username in ('student2','teacher1'):
        login(client,username)
        assert client.get(f'/api/v1/documents/{identifier}/chunks').status_code==404
        assert client.post(f'/api/v1/documents/{identifier}/retry',json={'expected_version':1,'expected_retry_round':0}).status_code==404
    login(client)
    courses.change_enrollment(STUDENT,course,False)
    assert client.get(f'/api/v1/documents/{identifier}/chunks').status_code==200
    assert client.post(f'/api/v1/documents/{identifier}/retry',json={'expected_version':1,'expected_retry_round':0}).status_code==403


def test_new_process_reclaims_persisted_expired_job(course):
    identifier=create(course)
    expire(claim_job(document_id=identifier))
    result=subprocess.run([sys.executable,'-m','app.workers.documents','--once','--document-id',str(identifier)],cwd=Path(__file__).resolve().parents[1],env={**os.environ,'STORAGE_DIR':str(get_settings().storage_dir)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['status']=='parsed'
    assert task(identifier)['attempt_count']==2


def test_chunk_offsets_headings_and_coverage():
    text='# 范式\n\n'+('第一段解释。'*60)+'\n\n## 事务\n'+('第二段说明。'*150)
    chunks=split_pages([TextPage(text)],markdown=True,size=150,overlap=20)
    covered=set()
    for index,chunk in enumerate(chunks):
        assert chunk.index==index and len(chunk.content)<=150
        assert text[chunk.char_start:chunk.char_end]==chunk.content
        covered.update(range(chunk.char_start,chunk.char_end))
    assert all(index in covered for index,char in enumerate(text) if not char.isspace())
    assert chunks[0].section_title=='范式' and chunks[-1].section_title=='事务'
    pages=split_pages([TextPage('第一页',1),TextPage('第二页',2)])
    assert [chunk.page_number for chunk in pages]==[1,2]


def test_chunker_rejects_nonprogressing_parameters():
    with pytest.raises(ValueError):split_pages([TextPage('text')],size=10,overlap=10)


def test_processing_deadline_prevents_late_publication(course,monkeypatch):
    from app.documents.extraction import extract_pages
    monkeypatch.setattr(get_settings(),'worker_task_timeout_seconds',1)
    identifier=create(course)
    def slow(name,data):
        time.sleep(1.1)
        return extract_pages(name,data)
    assert process_job(claim_job(document_id=identifier),parser=slow)=='retry_scheduled'
    assert task(identifier)['error_code']=='PROCESSING_TIMEOUT'
    assert documents.get_document(STUDENT,identifier)['chunk_count']==0
