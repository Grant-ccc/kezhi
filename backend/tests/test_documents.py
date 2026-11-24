from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pymysql
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.core.config import get_settings
from app.core.db import connect, read_connection
from app.core.errors import BusinessError
from app.documents.extraction import validate_file
from app.main import app, attempts
from app.services import courses, documents

pytestmark = pytest.mark.integration
TEACHER = {"id": 1, "role": "TEACHER"}
STUDENT = {"id": 11, "role": "STUDENT"}
OTHER = {"id": 12, "role": "STUDENT"}
TEXT = "# 私人笔记\nBCNF 要求每个非平凡函数依赖的决定因素都是超键。".encode()


@pytest.fixture
def course(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    identifier = courses.create_course(TEACHER, {"code": "DOC-"+uuid4().hex[:16], "name": "资料权限测试", "description": "Integration fixture", "capacity": 5, "enrollment_status": "OPEN"})["id"]
    courses.change_enrollment(STUDENT, identifier, True)
    courses.change_enrollment(OTHER, identifier, True)
    yield identifier
    connection = connect(root=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE ch FROM document_chunks ch JOIN documents d ON d.id=ch.document_id WHERE d.course_id=%s", (identifier,))
            cursor.execute("DELETE j FROM index_jobs j JOIN documents d ON d.id=j.document_id WHERE d.course_id=%s", (identifier,))
            for table in ("documents", "enrollments", "course_teachers", "courses"):
                cursor.execute(f"DELETE FROM {table} WHERE {'id' if table=='courses' else 'course_id'}=%s", (identifier,))
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def client():
    attempts.clear()
    with TestClient(app) as client:
        yield client


def login(client, username="student1"):
    response = client.post("/api/v1/auth/login", json={"username":username,"password":get_settings().demo_password})
    assert response.status_code == 200, response.text


def upload(client, course_id, *, key=None, data=TEXT, filename="note.md", title="范式笔记"):
    return client.post(f"/api/v1/courses/{course_id}/documents", data={"title":title,"request_key":key or uuid4().hex}, files={"file":(filename,data,"application/octet-stream")})


def test_private_note_isolated_from_other_student_and_teacher(client, course):
    login(client)
    created = upload(client,course)
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    assert client.get(f"/api/v1/documents/{identifier}/download").content == TEXT
    assert client.get(f"/api/v1/documents/{identifier}/preview").json()["content"] == TEXT.decode()
    for username in ("student2", "teacher1", "teacher2"):
        login(client,username)
        for suffix in ("", "/download", "/preview"):
            response=client.get(f"/api/v1/documents/{identifier}{suffix}")
            assert response.status_code == 404
            assert "范式笔记" not in response.text
        assert client.patch(f"/api/v1/documents/{identifier}", json={"title":"越权"}).status_code == 404
        assert client.delete(f"/api/v1/documents/{identifier}").status_code == 404


def test_public_material_access_requires_current_enrollment(client, course):
    login(client,"teacher1")
    identifier=upload(client,course,title="教师讲义").json()["id"]
    login(client)
    assert client.get(f"/api/v1/documents/{identifier}/download").status_code == 200
    assert client.patch(f"/api/v1/documents/{identifier}",json={"title":"修改教师资料"}).status_code == 403
    courses.change_enrollment(STUDENT,course,False)
    assert client.get(f"/api/v1/documents/{identifier}/download").status_code == 403
    login(client,"student3")
    assert client.get(f"/api/v1/documents?course_id={course}").status_code == 403
    assert upload(client,course).status_code == 403
    login(client,"teacher2")
    assert upload(client,course).status_code == 403


def test_my_notes_remain_manageable_after_withdrawal(client, course):
    login(client)
    identifier=upload(client,course).json()["id"]
    courses.change_enrollment(STUDENT,course,False)
    assert client.get(f"/api/v1/documents/{identifier}/download").content == TEXT
    assert any(row["id"]==identifier and not row["can_replace"] for row in client.get('/api/v1/documents').json()["items"])
    assert client.patch(f"/api/v1/documents/{identifier}",json={"title":"退课后的笔记"}).status_code == 200
    replace=client.put(f"/api/v1/documents/{identifier}/file", data={"title":"新版","expected_version":1,"request_key":uuid4().hex},files={"file":("new.txt",b"updated")})
    assert replace.status_code == 403
    assert client.delete(f"/api/v1/documents/{identifier}").status_code == 200
    assert client.get(f"/api/v1/documents/{identifier}/download").status_code == 404


def test_upload_idempotency_conflict_and_concurrency(course):
    key=uuid4().hex
    barrier=Barrier(2)
    def create(_):
        barrier.wait(timeout=10)
        return documents.save_document(STUDENT,course,"重复上传","note.md",TEXT,key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,range(2)))
    assert len({row["id"] for row in results})==1
    assert sorted(row["replayed"] for row in results)==[False,True]
    assert len(list(get_settings().storage_dir.glob('*.bin')))==1
    with pytest.raises(BusinessError) as error:
        documents.save_document(STUDENT,course,"不同标题","note.md",TEXT,key)
    assert error.value.code=="IDEMPOTENCY_CONFLICT"


def test_replacement_version_jobs_and_old_request_replay(course):
    identifier=documents.save_document(STUDENT,course,"初版","note.md",TEXT,uuid4().hex)["id"]
    key=uuid4().hex
    second=documents.save_document(STUDENT,course,"新版","new.txt",b"Version two",key,document_id=identifier,expected_version=1)
    assert second["content_version"]==2
    replay=documents.save_document(STUDENT,course,"新版","new.txt",b"Version two",key,document_id=identifier,expected_version=1)
    assert replay["replayed"] and replay["content_version"]==2
    with pytest.raises(BusinessError) as error:
        documents.save_document(STUDENT,course,"冲突","bad.txt",b"Conflict",uuid4().hex,document_id=identifier,expected_version=1)
    assert error.value.code=="VERSION_CONFLICT"
    documents.save_document(STUDENT,course,"第三版","third.txt",b"Version three",uuid4().hex,document_id=identifier,expected_version=2)
    assert documents.save_document(STUDENT,course,"新版","new.txt",b"Version two",key,document_id=identifier,expected_version=1)["content_version"]==2
    doc,content=documents.read_file(STUDENT,identifier)
    assert doc["content_version"]==3 and content==b"Version three"
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT content_version,status FROM index_jobs WHERE document_id=%s ORDER BY content_version",(identifier,))
        assert [row["status"] for row in cursor.fetchall()]==['CANCELLED','CANCELLED','PENDING']


def test_delete_invalidates_chunks_tasks_and_download(course):
    identifier=documents.save_document(STUDENT,course,"旧片段","note.md",TEXT,uuid4().hex)["id"]
    connection=connect(root=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO document_chunks(document_id,content_version,chunk_index,content,is_current) VALUES(%s,1,0,'Synthetic pending chunk',TRUE)",(identifier,))
        connection.commit()
    finally: connection.close()
    documents.delete_document(STUDENT,identifier)
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT is_current FROM document_chunks WHERE document_id=%s",(identifier,))
        assert cursor.fetchone()["is_current"]==0
        cursor.execute("SELECT status,worker_token FROM index_jobs WHERE document_id=%s",(identifier,))
        assert cursor.fetchone()=={"status":"CANCELLED","worker_token":None}
    with pytest.raises(BusinessError): documents.read_file(STUDENT,identifier)
    assert documents.list_documents(STUDENT,course_id=course)["total"]==0


def test_failed_task_insert_rolls_back_file_and_document(course, monkeypatch):
    original=pymysql.cursors.Cursor.execute
    def fail(cursor,query,args=None):
        if query.startswith('INSERT INTO index_jobs'): raise RuntimeError('Injected index job failure')
        return original(cursor,query,args)
    with monkeypatch.context() as patch:
        patch.setattr(pymysql.cursors.Cursor,'execute',fail)
        with pytest.raises(RuntimeError):documents.save_document(STUDENT,course,"回滚","note.md",TEXT,uuid4().hex)
    assert not list(get_settings().storage_dir.glob('*.bin'))
    assert documents.list_documents(STUDENT,course_id=course)["total"]==0


@pytest.mark.parametrize("filename,data,code",[("bad.exe",b"MZfile",415),("fake.pdf",b"not a PDF",415),("fake.txt",b"%PDF-1.4",415),("binary.md",b"abc\x00def",415),("empty.txt",b"",422),("spaces.md",b"  \n ",422),("gbk.txt",b"\xc4\xe3\xba\xc3",415)])
def test_invalid_file_rejected_without_rows_or_files(client, course, filename, data, code):
    login(client)
    response=upload(client,course,filename=filename,data=data)
    assert response.status_code==code,response.text
    assert not list(get_settings().storage_dir.glob('*.bin'))
    assert documents.list_documents(STUDENT,course_id=course)["total"]==0


def test_upload_size_limit_is_checked_by_server(client,course,monkeypatch):
    monkeypatch.setattr(get_settings(),'max_upload_bytes',16)
    login(client)
    assert upload(client,course,data=b'a'*17,filename='large.txt').status_code==413


def test_blank_and_encrypted_pdf_rejected():
    for encrypted in (False,True):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        if encrypted:writer.encrypt('local-test-password')
        stream=BytesIO();writer.write(stream)
        with pytest.raises(BusinessError) as error:validate_file('scan.pdf',stream.getvalue())
        assert error.value.code==('ENCRYPTED_PDF' if encrypted else 'NO_EXTRACTABLE_TEXT')


def test_text_pdf_and_safe_filename():
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    content=b'BT /F1 12 Tf 20 100 Td (Database normal forms) Tj ET'
    objects.append(b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream')
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(data)
    data+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    data+=f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    assert validate_file('../../lecture.pdf',data)==('lecture.pdf','application/pdf')


def test_rename_preserves_content_version_and_missing_file_is_clear(course):
    identifier=documents.save_document(STUDENT,course,"原名","note.md",TEXT,uuid4().hex)["id"]
    documents.rename_document(STUDENT,identifier,"新标题")
    assert documents.get_document(STUDENT,identifier)["content_version"]==1
    for path in get_settings().storage_dir.glob('*.bin'):path.unlink()
    with pytest.raises(BusinessError) as error:documents.read_file(STUDENT,identifier)
    assert error.value.code=='FILE_MISSING'


def test_server_kind_filter_applies_before_count_and_pagination(client,course):
    login(client,'teacher1')
    upload(client,course,title='公共讲义')
    login(client)
    own=upload(client,course,title='本人笔记').json()['id']
    login(client,'student2')
    upload(client,course,title='他人私人笔记')
    login(client)
    notes=client.get('/api/v1/documents',params={'course_id':course,'kind':'NOTE'}).json()
    assert notes['total']==1 and [row['id'] for row in notes['items']]==[own]
    public=client.get('/api/v1/documents',params={'course_id':course,'kind':'TEACHING'}).json()
    assert public['total']==1 and public['items'][0]['title']=='公共讲义'
    assert client.get('/api/v1/documents',params={'kind':'INVALID'}).status_code==422


def test_lost_commit_ack_does_not_remove_committed_file(course,monkeypatch):
    key=uuid4().hex
    original=documents.transaction
    @contextmanager
    def lost_ack():
        with original() as connection:
            yield connection
        raise pymysql.OperationalError(2013,'Simulated lost COMMIT acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(documents,'transaction',lost_ack)
        with pytest.raises(pymysql.OperationalError):
            documents.save_document(STUDENT,course,'已提交文件','note.md',TEXT,key)
    replay=documents.save_document(STUDENT,course,'已提交文件','note.md',TEXT,key)
    assert replay['replayed']
    assert documents.read_file(STUDENT,replay['id'])[1]==TEXT
    assert len(list(get_settings().storage_dir.glob('*.bin')))==1
