"""Run with python -m app.workers.documents [--once | --drain]. No model calls."""
import argparse
import hashlib
import json
import logging
from threading import Event, Thread
from time import monotonic

from app.core.config import get_settings
from app.core.errors import BusinessError
from app.documents.chunking import split_pages
from app.documents.extraction import extract_pages
from app.services.documents import storage_path
from app.workers.queue import claim_job, fail_job, publish_chunks, renew_job

logger = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, job):
        self.job = job
        self.stop = Event()
        self.lost = Event()
        self.started = monotonic()
        self.thread = Thread(target=self._loop, daemon=True)

    def _loop(self):
        interval = get_settings().worker_lease_seconds / 3
        while not self.stop.wait(interval):
            if monotonic() - self.started >= get_settings().worker_task_timeout_seconds:
                self.lost.set()
                return
            try:
                if not renew_job(self.job):
                    self.lost.set()
                    return
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=20)


def process_job(job, *, parser=extract_pages):
    with Heartbeat(job) as heartbeat:
        try:
            try:
                with storage_path(job["storage_key"]).open("rb") as handle:
                    data = handle.read(get_settings().max_upload_bytes + 1)
            except FileNotFoundError:
                raise BusinessError(404, "FILE_MISSING", "原文件不存在") from None
            if len(data) != job["size_bytes"] or hashlib.sha256(data).hexdigest() != job["content_hash"]:
                raise BusinessError(409, "FILE_CHANGED", "文件完整性校验失败")
            _, media_type, pages = parser(job["original_filename"], data)
            chunks = split_pages(pages, markdown=media_type == "text/markdown")
            if not chunks:
                raise BusinessError(422, "NO_EXTRACTABLE_TEXT", "没有可用正文")
            if heartbeat.lost.is_set():
                return "lease_lost"
            if monotonic() - heartbeat.started >= get_settings().worker_task_timeout_seconds:
                return fail_job(job, "PROCESSING_TIMEOUT") or "lease_lost"
            return "parsed" if publish_chunks(job, chunks) else "lease_lost"
        except BusinessError as error:
            updated = fail_job(job, error.code, permanent=True)
            return "failed" if updated else "lease_lost"
        except Exception as error:
            # Do not log paths, private text, exception strings, or original filenames.
            logger.warning("Parse attempt failed: job=%s type=%s", job["id"], type(error).__name__)
            updated = fail_job(job, "PROCESSING_FAILED")
            return updated or "lease_lost"


def run_once(*, document_id: int | None = None):
    job = claim_job(document_id=document_id)
    if job is None:
        return {"status": "idle"}
    return {"job_id": job["id"], "document_id": job["document_id"], "status": process_job(job)}


def main():
    parser = argparse.ArgumentParser(description="Parse pending course documents without selecting an Embedding model")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process at most one due task")
    mode.add_argument("--drain", action="store_true", help="Process due tasks until idle; future retries wait for a later run")
    parser.add_argument("--document-id", type=int, help="Limit processing to one document (local diagnostics)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            result = run_once(document_id=args.document_id)
            if result["status"] != "idle" or args.once or args.drain:
                print(json.dumps(result), flush=True)
            if args.once or (args.drain and result["status"] == "idle"):
                return
            if result["status"] == "idle":
                Event().wait(get_settings().worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopped; any interrupted lease can be reclaimed after expiry")


if __name__ == "__main__":
    main()
