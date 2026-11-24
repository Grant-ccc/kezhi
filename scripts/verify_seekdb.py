"""Probe actual SeekDB SQL in a disposable, uniquely named database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from threading import Barrier
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import pymysql
from app.core.db import connect


def main():
    name = "course_probe_" + uuid4().hex
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "database": name, "checks": {}}
    connection = connect(root=True, database="")
    created = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            report["version"] = cursor.fetchone()["version"]
            cursor.execute(f"CREATE DATABASE `{name}`")
            created = True
        connection.select_db(name)
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE probe_courses(id INT PRIMARY KEY, code VARCHAR(32) UNIQUE, capacity INT NOT NULL, CHECK(capacity>0))")
            cursor.execute("CREATE TABLE probe_enrollments(id INT PRIMARY KEY, course_id INT, student_id INT, UNIQUE(course_id,student_id), FOREIGN KEY(course_id) REFERENCES probe_courses(id))")
            cursor.execute("INSERT INTO probe_courses VALUES(1,'DB',1)")
        connection.commit()
        expected_failures = {
            "primary_key": "INSERT INTO probe_courses VALUES(1,'DUP',1)",
            "unique": "INSERT INTO probe_courses VALUES(2,'DB',1)",
            "foreign_key": "INSERT INTO probe_enrollments VALUES(1,999,1)",
            "check": "INSERT INTO probe_courses VALUES(2,'BAD',0)",
        }
        for label, sql in expected_failures.items():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
            except pymysql.MySQLError as error:
                expected_codes = {"primary_key": {1062}, "unique": {1062}, "foreign_key": {1452}, "check": {3819, 4025}}
                if error.args[0] not in expected_codes[label]:
                    raise
                report["checks"][label] = {"passed": True, "error_code": error.args[0]}
            else:
                raise AssertionError(f"{label} was not enforced")
            finally:
                connection.rollback()
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO probe_courses VALUES(2,'ROLLBACK',2)")
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM probe_courses WHERE id=2")
            assert cursor.fetchone()["n"] == 0
        connection.rollback()
        report["checks"]["rollback"] = {"passed": True}

        barrier = Barrier(2)
        def compete(student_id):
            peer = connect(root=True, database=name)
            try:
                barrier.wait(timeout=10)
                peer.begin()
                with peer.cursor() as cursor:
                    cursor.execute("SELECT capacity FROM probe_courses WHERE id=1 FOR UPDATE")
                    capacity = cursor.fetchone()["capacity"]
                    cursor.execute("SELECT COUNT(*) AS n FROM probe_enrollments WHERE course_id=1")
                    if cursor.fetchone()["n"] >= capacity:
                        peer.rollback()
                        return False
                    cursor.execute("INSERT INTO probe_enrollments VALUES(%s,1,%s)", (student_id, student_id))
                peer.commit()
                return True
            finally:
                peer.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(compete, [11, 12]))
        assert sum(result) == 1, result
        report["checks"]["concurrent_last_seat"] = {"passed": True, "successful_requests": sum(result)}

        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE probe_docs(id INT PRIMARY KEY,owner_id INT,kind VARCHAR(16),visible BOOLEAN,embedding VECTOR(3))")
            cursor.executemany("INSERT INTO probe_docs VALUES(%s,%s,%s,TRUE,%s)", [(1,1,"TEACHING","[0,1,0]"),(2,11,"NOTE","[0.1,0.9,0]"),(3,12,"NOTE","[1,0,0]")])
        connection.commit()
        query = "SELECT id,cosine_distance(embedding,%s) AS distance FROM probe_docs WHERE visible=TRUE AND (kind='TEACHING' OR owner_id=%s) ORDER BY distance,id LIMIT 5"
        with connection.cursor() as cursor:
            cursor.execute(query, ("[1,0,0]", 11))
            ids = [row["id"] for row in cursor.fetchall()]
            assert set(ids) == {1,2} and 3 not in ids, ids
            cursor.execute("UPDATE probe_docs SET visible=FALSE WHERE id=1")
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute(query, ("[1,0,0]",11))
            assert [row["id"] for row in cursor.fetchall()] == [2]
        report["checks"]["parameterized_authorized_vector_search"] = {"passed": True, "dimension": 3, "note": "Synthetic vectors only; no model selected."}
        report["checks"]["deleted_visibility"] = {"passed": True}
        report["passed"] = True
    except Exception as error:
        report["passed"] = False
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        raise
    finally:
        connection.rollback()
        if created:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{name}`")
        connection.close()
        target = ROOT / "docs/verification/seekdb-capabilities.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
