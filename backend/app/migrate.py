"""Idempotent state migration; DDL is not a multi-table transaction."""
import argparse
import re
from app.core.config import ROOT
from app.core.config import get_settings
from app.core.db import connect


def apply_migrations(connection):
    text = (ROOT / "sql/005_processing_states.sql").read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    statements = [part.strip() for part in text.split(";") if part.strip()]
    for index, (table, constraint) in enumerate((("documents", "chk_document_status"), ("index_jobs", "chk_job_status"))):
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW CREATE TABLE {table}")
            row = cursor.fetchone()
            ddl = next(value for key, value in row.items() if "create" in key.lower())
            old_exists = f"`{constraint}`" in ddl
            new_exists = f"`{constraint}_v2`" in ddl
            if not old_exists and not new_exists:
                raise RuntimeError(f"Expected state constraint missing on {table}; inspect schema before migration")
            if not new_exists:
                cursor.execute(statements[index * 2])
            if old_exists:
                cursor.execute(statements[index * 2 + 1])
    print("Processing state migration applied or already current; business rows preserved.")
    job_statements = [part.strip() for part in (ROOT / "sql/006_embedding_jobs.sql").read_text(encoding="utf-8").split(";") if part.strip()]
    with connection.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM index_jobs")
        columns = {row["Field"] for row in cursor.fetchall()}
        for index, column in enumerate(("stage", "model_calls")):
            if column not in columns:
                cursor.execute(job_statements[index])
        cursor.execute("SHOW CREATE TABLE index_jobs")
        ddl = next(value for key, value in cursor.fetchone().items() if "create" in key.lower())
        if "`chk_job_stage`" not in ddl:
            cursor.execute(job_statements[2])
        cursor.execute("UPDATE index_jobs SET stage='EMBEDDING' WHERE status='PARSED' AND stage='PARSE'")
    connection.commit()
    print("Embedding job metadata applied or already current.")


def vector_dimension(cursor):
    cursor.execute("SHOW COLUMNS FROM document_chunks LIKE 'embedding'")
    row = cursor.fetchone()
    if not row:
        return None
    match = re.fullmatch(r"vector\((\d+)\)", row["Type"].lower())
    if not match:
        raise ValueError("Unexpected embedding column type")
    return int(match[1])


def apply_vectors(connection, dimensions: int):
    if type(dimensions) is not int or dimensions not in {256, 512, 768, 1024, 1536, 2048, 2560}:
        raise ValueError("Unsupported embedding dimensions")
    with connection.cursor() as cursor:
        existing = vector_dimension(cursor)
        if existing is None:
            sql = (ROOT / "sql/003_vectors.sql.template").read_text(encoding="utf-8")
            sql = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
            cursor.execute(sql.replace("{{EMBEDDING_DIM}}", str(dimensions)).strip().rstrip(";"))
        elif existing != dimensions:
            raise ValueError("Embedding dimensions differ from database; explicit rebuild required")
    print(f"Vector column verified: {dimensions} dimensions; no existing vectors modified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", action="store_true", help="Add/verify vector column using configured dimensions; never resize existing column")
    args = parser.parse_args()
    connection = connect(root=True)
    try:
        apply_migrations(connection)
        if args.vectors:
            apply_vectors(connection, get_settings().embedding_dimensions)
    finally:
        connection.close()
