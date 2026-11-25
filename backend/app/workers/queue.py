"""Persistent parse/embedding jobs. All mutations lock course -> document -> job."""
from contextlib import contextmanager
from uuid import uuid4

from app.core.config import get_settings
from app.core.db import read_connection, transaction
from app.services.courses import lock_course
from app.services.documents import lookup


@contextmanager
def locked_job(job):
    with transaction() as connection, connection.cursor() as cursor:
        lock_course(cursor, job["course_id"])
        document = lookup(cursor, job["document_id"], lock=True)
        cursor.execute("SELECT *,lease_until>UTC_TIMESTAMP(6) AS lease_valid FROM index_jobs WHERE id=%s FOR UPDATE", (job["id"],))
        current = cursor.fetchone()
        yield cursor, current, document


def owned(job, current, document):
    return bool(current and current["status"] == "RUNNING" and current["worker_token"] == job["worker_token"]
                and current["lease_valid"] and document["status"] == "PROCESSING"
                and current["stage"] == job["stage"] and document["deleted_at"] is None
                and current["content_version"] == job["content_version"] == document["content_version"])


def claim_job(*, document_id: int | None = None, stage: str = "PARSE"):
    if stage not in {"PARSE", "EMBEDDING"}:
        raise ValueError("Invalid processing stage")
    # Read candidate ids without locks; recheck every condition after ordered locks.
    params = (stage, stage, document_id) if document_id is not None else (stage, stage)
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT j.id,j.document_id,d.course_id FROM index_jobs j
            JOIN documents d ON d.id=j.document_id AND d.content_version=j.content_version
            WHERE j.stage=%s AND d.status IN ('PROCESSING','PARSED') AND
              ((j.status='PENDING' AND j.next_run_at<=UTC_TIMESTAMP(6)) OR
               (j.status='PARSED' AND %s='EMBEDDING') OR
               (j.status='RUNNING' AND (j.lease_until IS NULL OR j.lease_until<=UTC_TIMESTAMP(6))))"""
                       + (" AND d.id=%s" if document_id is not None else "")
                       + " ORDER BY j.next_run_at,j.id LIMIT 16", params)
        candidates = cursor.fetchall()
    settings = get_settings()
    for candidate in candidates:
        with locked_job(candidate) as (cursor, current, document):
            if not current or current['stage'] != stage or document["status"] not in {'PROCESSING','PARSED'} or current["content_version"] != document["content_version"]:
                continue
            cursor.execute("SELECT next_run_at<=UTC_TIMESTAMP(6) AS due FROM index_jobs WHERE id=%s", (current["id"],))
            due = cursor.fetchone()["due"]
            eligible = (current["status"] == "PENDING" and due) or (current["status"] == "RUNNING" and not current["lease_valid"]) or (stage == 'EMBEDDING' and current['status'] == 'PARSED')
            if not eligible:
                continue
            attempts = 0 if current['status'] == 'PARSED' else current['attempt_count']
            if attempts >= settings.worker_max_attempts:
                cursor.execute("UPDATE index_jobs SET status='FAILED',error_code='ATTEMPTS_EXHAUSTED',worker_token=NULL,lease_until=NULL WHERE id=%s", (current["id"],))
                cursor.execute("UPDATE documents SET status='FAILED',error_code='ATTEMPTS_EXHAUSTED',error_detail=NULL WHERE id=%s", (document["id"],))
                continue
            token = uuid4().hex
            cursor.execute("UPDATE index_jobs SET status='RUNNING',attempt_count=%s,worker_token=%s,lease_until=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)),error_code=NULL WHERE id=%s", (attempts + 1, token, settings.worker_lease_seconds, current["id"]))
            cursor.execute("UPDATE documents SET status='PROCESSING' WHERE id=%s", (document['id'],))
            return {**current, "status": "RUNNING", "attempt_count": attempts + 1, "worker_token": token,
                    "course_id": document["course_id"], "storage_key": document["storage_key"],
                    "original_filename": document["original_filename"], "content_hash": document["content_hash"], "size_bytes": document["size_bytes"]}
    return None


def renew_job(job):
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return False
        cursor.execute("UPDATE index_jobs SET lease_until=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)) WHERE id=%s", (get_settings().worker_lease_seconds, job["id"]))
        return True


def publish_chunks(job, chunks):
    if not chunks:
        raise ValueError("No chunks to publish")
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return False
        cursor.executemany("""INSERT INTO document_chunks(document_id,content_version,chunk_index,content,page_number,section_title,char_start,char_end,is_current)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,FALSE)""",
                           [(job["document_id"], job["content_version"], chunk.index, chunk.content, chunk.page_number, chunk.section_title, chunk.char_start, chunk.char_end) for chunk in chunks])
        # Neither document nor chunks are searchable until real vector publication.
        cursor.execute("UPDATE documents SET status='PARSED',error_code=NULL,error_detail=NULL WHERE id=%s", (job["document_id"],))
        cursor.execute("UPDATE index_jobs SET status='PARSED',stage='EMBEDDING',worker_token=NULL,lease_until=NULL,error_code=NULL WHERE id=%s", (job["id"],))
        return True


def fail_job(job, code: str, *, permanent: bool = False):
    with locked_job(job) as (cursor, current, document):
        if not owned(job, current, document):
            return False
        terminal = permanent or current["attempt_count"] >= get_settings().worker_max_attempts
        delay = min(300, get_settings().worker_retry_base_seconds * 2 ** (current["attempt_count"] - 1))
        cursor.execute("UPDATE index_jobs SET status=%s,error_code=%s,worker_token=NULL,lease_until=NULL,next_run_at=TIMESTAMPADD(SECOND,%s,UTC_TIMESTAMP(6)) WHERE id=%s", ("FAILED" if terminal else "PENDING", code, delay, job["id"]))
        cursor.execute("UPDATE documents SET status=%s,error_code=%s,error_detail=NULL WHERE id=%s", ("FAILED" if terminal else "PROCESSING", code, job["document_id"]))
        return "failed" if terminal else "retry_scheduled"
