"""Create the project's new database and apply non-destructive SQL/seed files."""
from pathlib import Path
import re

from app.core.config import ROOT, get_settings
from app.core.db import connect
from app.core.security import hasher


def statements(path: Path):
    # Project-controlled SQL files contain no semicolons inside strings.
    source = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--"))
    return [part.strip() for part in source.split(";") if part.strip()]


def main():
    settings = get_settings()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", settings.db_name):
        raise ValueError("Invalid project database name")
    connection = connect(root=True, database="")
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` DEFAULT CHARACTER SET utf8mb4")
            cursor.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (settings.db_user, settings.db_password))
            cursor.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON `{settings.db_name}`.* TO %s@'%%'", (settings.db_user,))
        connection.select_db(settings.db_name)
        with connection.cursor() as cursor:
            for statement in statements(ROOT / "sql/001_schema.sql"):
                cursor.execute(statement)
        from app.migrate import apply_migrations, apply_vectors
        apply_migrations(connection)
        apply_vectors(connection, settings.embedding_dimensions)
        connection.begin()
        with connection.cursor() as cursor:
            password_hash = connection.escape(hasher.hash(settings.demo_password))
            for statement in statements(ROOT / "sql/002_seed.sql"):
                cursor.execute(statement.replace("{{DEMO_PASSWORD_HASH}}", password_hash))
        connection.commit()
        print("Applied 12 tables, vector column and job metadata; preserved/inserted demo records.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
