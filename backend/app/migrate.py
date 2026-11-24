"""Idempotent state migration; DDL is not a multi-table transaction."""
from app.core.config import ROOT
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


if __name__ == "__main__":
    connection = connect(root=True)
    try:
        apply_migrations(connection)
    finally:
        connection.close()
