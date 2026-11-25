"""A/B retrieval experiment. --live sends four synthetic texts and one question."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from time import monotonic
from uuid import uuid4

import pymysql
from pydantic import ValidationError

from app.bootstrap import statements
from app.core.config import ROOT, get_settings
from app.core.db import connect
from app.models.embedding import BailianEmbedding, EmbeddingError, EmbeddingProvider
from app.retrieval.search import candidates, search

CASE_PATH = ROOT / "evals/cases/retrieval-v0.1.json"


@dataclass
class ExperimentDatabase:
    connection: object
    name: str
    version: str
    cleaned: bool = False


@contextmanager
def experiment_database(dimensions: int, audit: dict | None = None):
    if type(dimensions) is not int or not 1 <= dimensions <= 4096:
        raise ValueError("Invalid experiment dimensions")
    name = "retrieval_eval_" + uuid4().hex
    # The name is generated here and never accepted from CLI or config.
    assert name != get_settings().db_name
    connection = connect(root=True, database="")
    created = False
    database = ExperimentDatabase(connection, name, "")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            database.version = cursor.fetchone()["version"]
            cursor.execute(f"CREATE DATABASE `{name}` DEFAULT CHARACTER SET utf8mb4")
            created = True
        connection.select_db(name)
        with connection.cursor() as cursor:
            for statement in statements(ROOT / "sql/001_schema.sql"):
                cursor.execute(statement)
            cursor.execute(f"ALTER TABLE document_chunks ADD COLUMN embedding VECTOR({dimensions})")
        yield database
    finally:
        try:
            connection.rollback()
            if created:
                with connection.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE `{name}`")
                database.cleaned = True
                if audit is not None:
                    audit["temporary_database_cleaned"] = True
        finally:
            connection.close()


def load_case():
    data = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    case = data["cases"][0]
    # Bound this CLI to the already reviewed synthetic fixture, never arbitrary uploads.
    if (case["case_id"] != "transfer_atomicity_001" or case["provenance"] != "synthetic"
            or [d["id"] for d in case["documents"]] != ["A", "B", "C", "D"]):
        raise ValueError("Unexpected evaluation fixture")
    return case


def seed_case(connection, case, vectors, profile):
    with connection.cursor() as cursor:
        cursor.executemany("INSERT INTO users(id,username,password_hash,display_name,role) VALUES(%s,%s,'not-a-login-hash',%s,%s)",
                           [(1, "teacher_demo", "teacher_demo", "TEACHER"), (2, "teacher_other", "teacher_other", "TEACHER"),
                            (11, "student_a", "student_a", "STUDENT"), (12, "student_b", "student_b", "STUDENT"),
                            (13, "student_unenrolled", "student_unenrolled", "STUDENT")])
        cursor.executemany("INSERT INTO courses(id,code,name,description,capacity,created_by) VALUES(%s,%s,%s,'Synthetic evaluation',10,1)",
                           [(1, "database_demo", "database_demo"), (2, "other_course", "other_course")])
        cursor.execute("INSERT INTO course_teachers(course_id,teacher_id) VALUES(1,1)")
        cursor.execute("INSERT INTO enrollments(course_id,student_id) VALUES(1,11),(1,12)")
        for identifier, (document, vector) in enumerate(zip(case["documents"], vectors, strict=True), 1):
            owner = {"teacher_demo": 1, "student_a": 11, "student_b": 12}[document["owner_key"]]
            digest = hashlib.sha256(document["text"].encode()).hexdigest()
            cursor.execute("""INSERT INTO documents(id,course_id,owner_id,kind,title,original_filename,storage_key,
                           media_type,size_bytes,content_hash,upload_request_key,upload_payload_hash,status)
                           VALUES(%s,1,%s,%s,%s,'fixture.txt','no-physical-file','text/plain',%s,%s,%s,%s,'READY')""",
                           (identifier, owner, document["kind"], document["id"], len(document["text"].encode()),
                            digest, document["id"], digest))
            cursor.execute("""INSERT INTO document_chunks(id,document_id,content_version,chunk_index,content,
                           embedding_profile,is_current,embedding) VALUES(%s,%s,1,0,%s,%s,TRUE,%s)""",
                           (identifier, identifier, document["text"], profile, json.dumps(vector, allow_nan=False)))
    connection.commit()


def call_summary(batch, kind):
    return {"kind": kind, "requested_model": batch.requested_model, "vector_count": len(batch.vectors),
            "dimensions": len(batch.vectors[0]), "total_tokens": batch.total_tokens,
            "request_id": batch.request_id, "elapsed_ms": batch.elapsed_ms}


def run_case(provider: EmbeddingProvider, *, dimensions: int, source: str, report: dict | None = None):
    case = load_case()
    report = {} if report is None else report
    report.update({"checked_at": datetime.now(timezone.utc).isoformat(), "case_id": case["case_id"],
              "vector_source": source, "fixture_sha256": hashlib.sha256(CASE_PATH.read_bytes()).hexdigest(),
              "question": case["question"], "calls": [], "checks": {}, "temporary_database_cleaned": False,
              "scope": "Single synthetic case on the business schema in an isolated database; no production ingestion, HTTP authorization, generation or follow-up policy evaluated."})
    # Verify database availability/schema before spending any model calls.
    with experiment_database(dimensions, report) as database:
        connection = database.connection
        report["seekdb_version"] = database.version
        docs = provider.embed([d["text"] for d in case["documents"]], kind="document")
        report["calls"].append(call_summary(docs, "document"))
        query = provider.embed([case["question"]], kind="query")
        report["calls"].append(call_summary(query, "query"))
        if docs.profile != query.profile or len(docs.vectors) != 4 or len(query.vectors) != 1:
            raise ValueError("Incompatible evaluation vectors")
        report["profile"] = docs.profile
        seed_case(connection, case, docs.vectors, docs.profile)
        parameters = {"actor_id": 11, "course_id": 1, "profile": docs.profile}
        ids = {i: d["id"] for i, d in enumerate(case["documents"], 1)}
        def inspect(actor_id=11, course_id=1):
            with connection.cursor() as cursor:
                params = {**parameters, "actor_id": actor_id, "course_id": course_id}
                allowed = [ids[row["document_id"]] for row in candidates(cursor, **params)]
                found = search(cursor, **params, query_vector=query.vectors[0], dimensions=dimensions,
                               top_k=case["expected"]["top_k"])
            return allowed, [{"document_id": ids[row["document_id"]], "rank": rank,
                             "cosine_distance": float(row["distance"])} for rank, row in enumerate(found, 1)]
        start = monotonic()
        allowed, found = inspect()
        report["retrieval_ms"] = round((monotonic() - start) * 1000)
        report["authorized_candidate_ids"] = allowed
        report["results"] = found
        returned = [row["document_id"] for row in found]
        relevant = set(case["expected"]["relevant_document_ids"])
        report["metrics"] = {"recall_at_2": len(relevant & set(returned)) / len(relevant),
                             "precision_at_2": len(relevant & set(returned)) / case["expected"]["top_k"],
                             "case_count": 1, "not_general_accuracy": True}
        checks = report["checks"]
        checks["candidate_scope"] = set(allowed) == set(case["expected"]["authorized_document_ids"])
        checks["relevant_set"] = set(returned) == relevant
        checks["forbidden_excluded"] = not set(case["expected"]["forbidden_document_ids"]) & set(allowed + returned)
        variants = [
            ("deleted", "UPDATE documents SET deleted_at=UTC_TIMESTAMP(6) WHERE id=2", {"A", "C"}, 11),
            ("status_deleted", "UPDATE documents SET status='DELETED' WHERE id=2", {"A", "C"}, 11),
            ("replaced_version", "UPDATE documents SET content_version=2 WHERE id=2", {"A", "C"}, 11),
            ("not_current", "UPDATE document_chunks SET is_current=FALSE WHERE id=2", {"A", "C"}, 11),
            ("different_profile", "UPDATE document_chunks SET embedding_profile='different-profile' WHERE id=2", {"A", "C"}, 11),
            ("parsed_not_ready", "UPDATE documents SET status='PARSED' WHERE id=2", {"A", "C"}, 11),
            ("wrong_course", "UPDATE documents SET course_id=2 WHERE id=2", {"A", "C"}, 11),
            ("withdrawn", "UPDATE enrollments SET status='DROPPED' WHERE student_id=11", set(), 11),
            ("disabled", "UPDATE users SET status='DISABLED' WHERE id=11", set(), 11),
            ("teacher_no_private_notes", None, {"A", "C"}, 1),
            ("other_student_own_notes_only", None, {"A", "C", "D"}, 12),
            ("unenrolled_student", None, set(), 13),
            ("unassigned_teacher", None, set(), 2),
        ]
        report["visibility_variants"] = []
        for name, mutation, expected, actor_id in variants:
            try:
                with connection.cursor() as cursor:
                    if mutation: cursor.execute(mutation)
                variant_allowed, variant_found = inspect(actor_id=actor_id)
                matches = set(variant_allowed) == expected and {r["document_id"] for r in variant_found}.issubset(expected)
                # When only A/C remain, both should be returned (top_k=2), not silently lost.
                matches = matches and (len(variant_found) == min(2, len(expected)))
                checks[name] = matches
                report["visibility_variants"].append({"name": name, "candidate_ids": variant_allowed,
                                                     "returned_ids": [r["document_id"] for r in variant_found], "passed": matches})
            finally:
                connection.rollback()  # restore the independent variant, including any active read transaction
        tokens = [item["total_tokens"] for item in report["calls"]]
        report["reported_total_tokens"] = sum(tokens) if all(t is not None for t in tokens) else None
        report["actual_billed_amount"] = None
    report["temporary_database_cleaned"] = database.cleaned
    report["passed"] = all(report["checks"].values()) and database.cleaned
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="真实百炼两次请求、四份自编资料与一个问题；使用自动清理的独立临时库，可能计费")
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "not_run", "planned_model_requests": 2, "network_calls": 0,
                          "instruction": "显式添加 --live 才运行；不发送已有上传文件"}, ensure_ascii=False))
        return 0
    report = {}
    try:
        settings = get_settings()
        run_case(BailianEmbedding(settings), dimensions=settings.embedding_dimensions, source="real_bailian_api", report=report)
    except (EmbeddingError, ValidationError, ValueError, pymysql.MySQLError) as error:
        # No upstream bodies, vectors or connection credentials in CLI errors.
        code = error.code if isinstance(error, EmbeddingError) else type(error).__name__
        report.update({"passed": False, "status": "failed", "code": code,
                       "message": "实验未完成；不自动重试，已发出的请求可能计费。临时库在退出时尝试清理。"})
    target = ROOT / "evals/results" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-retrieval-v0.1.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report_file": str(target)}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
