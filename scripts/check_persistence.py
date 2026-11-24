"""Prepare/verify a project-owned sentinel across a container restart."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.db import read_connection
from app.services.courses import create_course, delete_course

target = ROOT / "docs/verification/persistence.json"
teacher = {"id": 1, "role": "TEACHER"}
if sys.argv[1:] == ["prepare"]:
    code = "PERSIST-" + uuid4().hex[:16]
    identifier = create_course(teacher, {"code": code, "name": "Persistence verification", "description": "Temporary restart sentinel", "capacity": 2, "enrollment_status": "CLOSED"})["id"]
    target.write_text(json.dumps({"id": identifier, "code": code, "prepared_at": datetime.now(timezone.utc).isoformat(), "passed": False}, indent=2), encoding="utf-8")
    print("Prepared project-owned persistence sentinel.")
elif sys.argv[1:] == ["verify"]:
    record = json.loads(target.read_text(encoding="utf-8"))
    assert record["code"].startswith("PERSIST-")
    for attempt in range(45):
        try:
            with read_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT id,code FROM courses WHERE id=%s AND code=%s", (record["id"],record["code"]))
                assert cursor.fetchone(), "Persistence sentinel was not retained"
            break
        except AssertionError:
            raise
        except Exception:
            if attempt == 44:
                raise
            time.sleep(1)
    delete_course(teacher, record["id"])
    record.update(passed=True, verified_at=datetime.now(timezone.utc).isoformat(), sentinel_cleaned=True)
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("Restart persistence passed; only the project-owned sentinel was removed.")
else:
    raise SystemExit("Use prepare before restart and verify afterwards.")
