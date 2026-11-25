"""Document ownership and file lifecycle. No public static file directory is exposed."""
import hashlib
import json
import logging
from pathlib import Path
import re
from uuid import uuid4

import pymysql

from app.core.config import get_settings
from app.core.db import read_connection, transaction
from app.core.errors import BusinessError
from app.documents.extraction import validate_file
from app.services.courses import lock_course, require_manager

FIELDS = "d.id,d.course_id,d.owner_id,d.kind,d.title,d.original_filename,d.media_type,d.size_bytes,d.content_version,d.status,d.created_at,d.updated_at,c.name AS course_name"
logger = logging.getLogger(__name__)


def require_course_access(cursor, user: dict, course_id: int):
    if user["role"] == "TEACHER":
        require_manager(cursor, user["id"], course_id)
    else:
        cursor.execute("SELECT id FROM enrollments WHERE course_id=%s AND student_id=%s AND status='ACTIVE'", (course_id, user["id"]))
        if not cursor.fetchone():
            raise BusinessError(403, "ENROLLMENT_REQUIRED", "请先选修这门课程")


def authorize(cursor, user: dict, document: dict, *, write: bool = False, replace: bool = False):
    if document["status"] == "DELETED":
        raise BusinessError(404, "DOCUMENT_NOT_FOUND", "资料不存在或已删除")
    if document["kind"] == "NOTE":
        if user["role"] != "STUDENT" or document["owner_id"] != user["id"]:
            raise BusinessError(404, "DOCUMENT_NOT_FOUND", "资料不存在或无权访问")
        if replace:
            require_course_access(cursor, user, document["course_id"])
    else:
        if write and user["role"] != "TEACHER":
            raise BusinessError(403, "DOCUMENT_FORBIDDEN", "只有任课教师可以修改教学资料")
        require_course_access(cursor, user, document["course_id"])


def lookup(cursor, document_id: int, *, lock: bool = False):
    cursor.execute("SELECT * FROM documents WHERE id=%s" + (" FOR UPDATE" if lock else ""), (document_id,))
    document = cursor.fetchone()
    if not document:
        raise BusinessError(404, "DOCUMENT_NOT_FOUND", "资料不存在或已删除")
    return document


def present(document: dict, user: dict, cursor):
    result = {key: document[key] for key in ("id", "course_id", "owner_id", "kind", "title", "original_filename", "media_type", "size_bytes", "content_version", "status", "created_at", "updated_at", "course_name") if key in document}
    result["can_manage"] = document["owner_id"] == user["id"] if document["kind"] == "NOTE" else user["role"] == "TEACHER"
    cursor.execute("SELECT id FROM enrollments WHERE course_id=%s AND student_id=%s AND status='ACTIVE'", (document["course_id"], user["id"]))
    result["can_replace"] = result["can_manage"] and (user["role"] == "TEACHER" or cursor.fetchone() is not None)
    cursor.execute("SELECT status,stage,attempt_count,retry_round,next_run_at,error_code FROM index_jobs WHERE document_id=%s AND content_version=%s", (document["id"], document["content_version"]))
    job = cursor.fetchone()
    result["index_status"] = job["status"] if job else None
    result["index_stage"] = job["stage"] if job else None
    result["attempt_count"] = job["attempt_count"] if job else 0
    result["retry_round"] = job["retry_round"] if job else 0
    result["next_run_at"] = job["next_run_at"] if job else None
    result["can_retry"] = bool(result["can_replace"] and document["status"] == "FAILED" and job and job["status"] == "FAILED")
    error_code = job["error_code"] if job else None
    errors = {"FILE_MISSING": "原文件缺失，请重新上传或替换", "FILE_CHANGED": "文件完整性校验失败，请替换原文件", "ATTEMPTS_EXHAUSTED": "处理多次中断，请确认服务正常后重试", "PROCESSING_FAILED": "处理暂时失败，将按计划重试或等待手动重试", "PROCESSING_TIMEOUT": "处理超时，请拆分文件后重试",
              "EMBEDDING_ACCESS_DENIED": "向量服务权限不匹配，请联系维护者核对配置", "EMBEDDING_RATE_LIMITED": "向量服务限流，核对用量后可手动重试", "EMBEDDING_TIMEOUT": "向量服务超时，核对用量后可手动重试", "EMBEDDING_ACCESS_REVOKED": "上传者的课程资格已失效，恢复资格后可重试", "EMBEDDING_CHUNK_LIMIT": "片段数量超出本轮处理范围，请拆分资料或联系维护者", "EMBEDDING_INVALID_CHUNKS": "资料分块不符合向量处理要求，请替换文件"}
    result["processing_error"] = (errors.get(error_code, "向量化失败，请核对配置和用量后重试" if job and job["stage"] == 'EMBEDDING' else "文件解析失败，请检查文件后重试") if error_code else None)
    cursor.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE document_id=%s AND content_version=%s", (document["id"], document["content_version"]))
    result["chunk_count"] = cursor.fetchone()["n"]
    return result


def list_documents(user: dict, *, course_id: int | None = None, page: int = 1, query: str = "", kind: str | None = None):
    with read_connection() as connection, connection.cursor() as cursor:
        params = []
        if course_id is not None:
            cursor.execute("SELECT id FROM courses WHERE id=%s", (course_id,))
            if not cursor.fetchone():
                raise BusinessError(404, "COURSE_NOT_FOUND", "课程不存在")
            require_course_access(cursor, user, course_id)
            conditions = ["d.course_id=%s", "d.status<>'DELETED'"]
            params.append(course_id)
            if user["role"] == "TEACHER":
                conditions.append("d.kind='TEACHING'")
            else:
                conditions.append("(d.kind='TEACHING' OR (d.kind='NOTE' AND d.owner_id=%s))")
                params.append(user["id"])
        else:
            conditions = ["d.owner_id=%s", "d.status<>'DELETED'"]
            params.append(user["id"])
            if user["role"] == "TEACHER":
                conditions.append("d.kind='TEACHING' AND EXISTS(SELECT 1 FROM course_teachers ct WHERE ct.course_id=d.course_id AND ct.teacher_id=%s)")
                params.append(user["id"])
            else:
                conditions.append("d.kind='NOTE'")
        if kind:
            conditions.append("d.kind=%s")
            params.append(kind)
        if query:
            conditions.append("d.title LIKE %s")
            params.append(f"%{query}%")
        where = " AND ".join(conditions)
        cursor.execute(f"SELECT {FIELDS} FROM documents d JOIN courses c ON c.id=d.course_id WHERE {where} ORDER BY d.updated_at DESC,d.id DESC LIMIT 24 OFFSET %s", (*params, (page - 1) * 24))
        rows = cursor.fetchall()
        items = [present(row, user, cursor) for row in rows]
        cursor.execute(f"SELECT COUNT(*) AS n FROM documents d WHERE {where}", params)
        return {"items": items, "total": cursor.fetchone()["n"], "page": page, "page_size": 24}


def storage_path(key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}\.bin", key):
        raise BusinessError(503, "FILE_UNAVAILABLE", "文件暂时不可用")
    root = get_settings().storage_dir.resolve()
    path = (root / key).resolve()
    if path.parent != root:
        raise BusinessError(503, "FILE_UNAVAILABLE", "文件暂时不可用")
    return path


def _save(data: bytes) -> str:
    key = uuid4().hex + ".bin"
    path = storage_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise BusinessError(503, "FILE_WRITE_FAILED", "文件保存失败，请稍后重试") from None
    return key


def _fingerprint(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _existing_request(cursor, user: dict, request_key: str, payload_hash: str):
    cursor.execute("SELECT document_id,content_version,payload_hash FROM index_jobs WHERE requested_by=%s AND request_key=%s", (user["id"], request_key))
    job = cursor.fetchone()
    if not job:
        return None
    document = lookup(cursor, job["document_id"])
    authorize(cursor, user, document, write=True)
    if job["payload_hash"] != payload_hash:
        raise BusinessError(409, "IDEMPOTENCY_CONFLICT", "同一请求标识对应了不同文件或操作，请重新发起")
    return {"id": document["id"], "content_version": job["content_version"], "replayed": True}


def save_document(user: dict, course_id: int, title: str, filename: str, data: bytes, request_key: str, *, document_id: int | None = None, expected_version: int | None = None):
    # Fast authorization before expensive parsing or writing any file.
    with read_connection() as connection, connection.cursor() as cursor:
        require_course_access(cursor, user, course_id)
        if document_id is not None:
            authorize(cursor, user, lookup(cursor, document_id), write=True, replace=True)
    name, media_type = validate_file(filename, data)
    content_hash = hashlib.sha256(data).hexdigest()
    payload_hash = _fingerprint({"course_id": course_id, "title": title, "filename": name, "content_hash": content_hash, "document_id": document_id, "expected_version": expected_version})
    key = None
    try:
        with transaction() as connection, connection.cursor() as cursor:
            lock_course(cursor, course_id)  # Same ordering as enrollment/withdrawal.
            require_course_access(cursor, user, course_id)
            replay = _existing_request(cursor, user, request_key, payload_hash)
            if replay:
                return replay
            if document_id is not None:
                document = lookup(cursor, document_id, lock=True)
                authorize(cursor, user, document, write=True, replace=True)
                if document["course_id"] != course_id:
                    raise BusinessError(409, "COURSE_CONFLICT", "资料课程不匹配")
                if document["content_version"] != expected_version:
                    raise BusinessError(409, "VERSION_CONFLICT", "资料已更新，请刷新后重试")
                version = document["content_version"] + 1
            else:
                version = 1
            key = _save(data)
            if document_id is None:
                cursor.execute("""INSERT INTO documents(course_id,owner_id,kind,title,original_filename,storage_key,media_type,size_bytes,content_hash,upload_request_key,upload_payload_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (course_id, user["id"], "TEACHING" if user["role"] == "TEACHER" else "NOTE", title, name, key, media_type, len(data), content_hash, request_key, payload_hash))
                document_id = cursor.lastrowid
            else:
                cursor.execute("UPDATE documents SET title=%s,original_filename=%s,storage_key=%s,media_type=%s,size_bytes=%s,content_hash=%s,content_version=%s,status='PROCESSING',error_code=NULL,error_detail=NULL WHERE id=%s", (title, name, key, media_type, len(data), content_hash, version, document_id))
                cursor.execute("UPDATE document_chunks SET is_current=FALSE WHERE document_id=%s", (document_id,))
                cursor.execute("UPDATE index_jobs SET status='CANCELLED',worker_token=NULL,lease_until=NULL WHERE document_id=%s AND status IN ('PENDING','RUNNING','PARSED','FAILED')", (document_id,))
            cursor.execute("INSERT INTO index_jobs(document_id,content_version,requested_by,request_key,payload_hash) VALUES(%s,%s,%s,%s,%s)", (document_id, version, user["id"], request_key, payload_hash))
        return {"id": document_id, "content_version": version, "replayed": False}
    except Exception:
        if key:
            # COMMIT may have succeeded even if its acknowledgement was lost.
            # Never remove a file referenced by a committed row; retain on uncertainty.
            try:
                with read_connection() as check, check.cursor() as cursor:
                    cursor.execute("SELECT id FROM documents WHERE storage_key=%s LIMIT 1", (key,))
                    referenced = cursor.fetchone() is not None
                if not referenced:
                    storage_path(key).unlink(missing_ok=True)
            except (pymysql.MySQLError, OSError):
                logger.warning("Upload cleanup deferred; database/file state uncertain")
        raise


def get_document(user: dict, document_id: int):
    with read_connection() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        authorize(cursor, user, document)
        cursor.execute("SELECT name FROM courses WHERE id=%s", (document["course_id"],))
        document["course_name"] = cursor.fetchone()["name"]
        return present(document, user, cursor)


def read_file(user: dict, document_id: int):
    # Lock course -> document to serialize with withdraw/replace/delete, then read bytes.
    # Returning bytes avoids a later FileResponse open bypassing these state checks.
    with transaction() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        lock_course(cursor, document["course_id"])
        document = lookup(cursor, document_id, lock=True)
        authorize(cursor, user, document)
        try:
            data = storage_path(document["storage_key"]).read_bytes()
        except OSError:
            raise BusinessError(404, "FILE_MISSING", "原文件暂不可用，请联系上传者") from None
        return document, data


def rename_document(user: dict, document_id: int, title: str):
    with transaction() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        lock_course(cursor, document["course_id"])
        document = lookup(cursor, document_id, lock=True)
        authorize(cursor, user, document, write=True)
        cursor.execute("UPDATE documents SET title=%s WHERE id=%s", (title, document_id))
    return {"message": "资料标题已更新"}


def delete_document(user: dict, document_id: int):
    with transaction() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        lock_course(cursor, document["course_id"])
        document = lookup(cursor, document_id, lock=True)
        authorize(cursor, user, document, write=True)
        cursor.execute("UPDATE documents SET status='DELETED',deleted_at=UTC_TIMESTAMP(6) WHERE id=%s", (document_id,))
        cursor.execute("UPDATE document_chunks SET is_current=FALSE WHERE document_id=%s", (document_id,))
        cursor.execute("UPDATE index_jobs SET status='CANCELLED',worker_token=NULL,lease_until=NULL WHERE document_id=%s AND status IN ('PENDING','RUNNING','PARSED','FAILED')", (document_id,))
    # Retain original files for now; a separate retention/cleanup policy follows.
    return {"message": "资料已删除，访问权限已撤销"}


def retry_document(user: dict, document_id: int, expected_version: int, expected_retry_round: int):
    with transaction() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        lock_course(cursor, document["course_id"])
        document = lookup(cursor, document_id, lock=True)
        authorize(cursor, user, document, write=True, replace=True)
        cursor.execute("SELECT * FROM index_jobs WHERE document_id=%s AND content_version=%s FOR UPDATE", (document_id, document["content_version"]))
        job = cursor.fetchone()
        if document["content_version"] != expected_version or not job or job["retry_round"] != expected_retry_round:
            raise BusinessError(409, "RETRY_CONFLICT", "资料版本或重试轮次已变化，请刷新后重试")
        if document["status"] != "FAILED" or job["status"] != "FAILED":
            raise BusinessError(409, "RETRY_NOT_AVAILABLE", "只有处理失败的当前版本可以重试")
        cursor.execute("UPDATE index_jobs SET status='PENDING',retry_round=retry_round+1,attempt_count=0,next_run_at=UTC_TIMESTAMP(6),lease_until=NULL,worker_token=NULL,error_code=NULL WHERE id=%s", (job["id"],))
        cursor.execute("UPDATE documents SET status='PROCESSING',error_code=NULL,error_detail=NULL WHERE id=%s", (document_id,))
    return {"message": "已重新加入处理队列"}


def chunk_preview(user: dict, document_id: int, page: int = 1):
    from app.documents.chunking import CHUNKING_PROFILE
    with transaction() as connection, connection.cursor() as cursor:
        document = lookup(cursor, document_id)
        lock_course(cursor, document["course_id"])
        document = lookup(cursor, document_id, lock=True)
        authorize(cursor, user, document)
        if document["status"] not in {"PARSED", "READY"}:
            raise BusinessError(409, "PARSE_NOT_READY", "正文分块尚未准备好，请稍后刷新")
        cursor.execute("SELECT chunk_index,content,page_number,section_title,char_start,char_end FROM document_chunks WHERE document_id=%s AND content_version=%s ORDER BY chunk_index LIMIT 20 OFFSET %s", (document_id, document["content_version"], (page - 1) * 20))
        items = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS n FROM document_chunks WHERE document_id=%s AND content_version=%s", (document_id, document["content_version"]))
        return {"items": items, "total": cursor.fetchone()["n"], "page": page, "page_size": 20, "content_version": document["content_version"], "chunking_profile": CHUNKING_PROFILE, "searchable": document["status"] == "READY"}
