from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pymysql
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import connect, read_connection
from app.core.errors import BusinessError
from app.main import app, attempts
from app.services import courses

pytestmark = pytest.mark.integration
teacher = {"id": 1, "role": "TEACHER"}
student1 = {"id": 11, "role": "STUDENT"}
student2 = {"id": 12, "role": "STUDENT"}


@pytest.fixture
def empty_course():
    payload = {"code": "TEST-" + uuid4().hex[:16], "name": "自动化验证课程", "description": "Temporary integration fixture", "capacity": 1, "enrollment_status": "OPEN"}
    course_id = courses.create_course(teacher, payload)["id"]
    yield course_id, payload
    connection = connect(root=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM enrollments WHERE course_id=%s", (course_id,))
            cursor.execute("DELETE FROM course_teachers WHERE course_id=%s", (course_id,))
            cursor.execute("DELETE FROM courses WHERE id=%s", (course_id,))
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def client():
    attempts.clear()
    with TestClient(app) as client:
        yield client


def sign_in(client, username="student1"):
    result = client.post("/api/v1/auth/login", json={"username": username, "password": get_settings().demo_password})
    assert result.status_code == 200, result.text
    return result


def test_login_cookie_and_logout(client):
    response = sign_in(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").json()["id"] == 11
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/courses").status_code == 401


def test_invalid_credentials_and_login_limit(client):
    for _ in range(10):
        response = client.post("/api/v1/auth/login", json={"username": "doesnotexist", "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "student1", "password": "wrong"}).status_code == 429


def test_browser_cross_origin_write_rejected(client):
    sign_in(client)
    assert client.post("/api/v1/auth/logout", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200


def test_student_cannot_create_course(client):
    sign_in(client)
    result = client.post("/api/v1/courses", json={"code": "DENIED", "name": "课程", "capacity": 10})
    assert result.status_code == 403


def test_join_statistics_and_query_binding(client):
    sign_in(client)
    result = client.get("/api/v1/courses").json()
    assert any(item["id"] == 1 and item["teachers"] and item["is_enrolled"] for item in result["items"])
    assert client.get("/api/v1/courses", params={"q": "' OR 1=1 --"}).json()["total"] == 0


def test_concurrent_last_seat(empty_course):
    course_id, _ = empty_course
    barrier = Barrier(2)
    def enroll(user):
        barrier.wait(timeout=10)
        try:
            courses.change_enrollment(user, course_id, True)
            return "success"
        except BusinessError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(enroll, [student1, student2]))
    assert sorted(outcomes) == ["COURSE_FULL", "success"]
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM enrollments WHERE course_id=%s AND status='ACTIVE'", (course_id,))
        assert cursor.fetchone()["n"] == 1


def test_withdraw_reenroll_no_duplicate_and_workspace_access(empty_course):
    course_id, _ = empty_course
    with pytest.raises(BusinessError, match="请先选修"):
        courses.workspace(student1, course_id)
    courses.change_enrollment(student1, course_id, True)
    assert courses.workspace(student1, course_id)["course"]["id"] == course_id
    with pytest.raises(BusinessError) as error:
        courses.change_enrollment(student1, course_id, True)
    assert error.value.code == "ALREADY_ENROLLED"
    courses.change_enrollment(student1, course_id, False)
    with pytest.raises(BusinessError):
        courses.workspace(student1, course_id)
    courses.change_enrollment(student1, course_id, True)
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM enrollments WHERE course_id=%s AND student_id=11", (course_id,))
        assert cursor.fetchone()["n"] == 1


def test_capacity_conflict_does_not_change_existing_value(empty_course):
    course_id, payload = empty_course
    courses.update_course(teacher, course_id, {**payload, "capacity": 2})
    courses.change_enrollment(student1, course_id, True)
    courses.change_enrollment(student2, course_id, True)
    with pytest.raises(BusinessError) as error:
        courses.update_course(teacher, course_id, {**payload, "capacity": 1})
    assert error.value.code == "CAPACITY_CONFLICT"
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT capacity FROM courses WHERE id=%s", (course_id,))
        assert cursor.fetchone()["capacity"] == 2


def test_teacher_object_permission(empty_course):
    course_id, payload = empty_course
    with pytest.raises(BusinessError) as error:
        courses.update_course({"id": 2, "role": "TEACHER"}, course_id, payload)
    assert error.value.code == "COURSE_FORBIDDEN"


def test_create_course_rolls_back_if_teaching_assignment_fails(monkeypatch):
    code = "ROLLBACK-" + uuid4().hex[:16]
    original = pymysql.cursors.Cursor.execute
    def fail_second_write(cursor, query, args=None):
        if query.startswith("INSERT INTO course_teachers"):
            raise RuntimeError("Injected failure on the second business write")
        return original(cursor, query, args)
    with monkeypatch.context() as patch:
        patch.setattr(pymysql.cursors.Cursor, "execute", fail_second_write)
        with pytest.raises(RuntimeError, match="second business write"):
            courses.create_course(teacher, {"code": code, "name": "Rollback", "description": "", "capacity": 1, "enrollment_status": "OPEN"})
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM courses WHERE code=%s", (code,))
        assert cursor.fetchone()["n"] == 0


def test_cannot_delete_course_with_enrollment_history(empty_course):
    course_id, _ = empty_course
    courses.change_enrollment(student1, course_id, True)
    courses.change_enrollment(student1, course_id, False)
    with pytest.raises(BusinessError) as error:
        courses.delete_course(teacher, course_id)
    assert error.value.code == "COURSE_HAS_RECORDS"


def test_invalid_capacity_validated_by_server(client):
    sign_in(client, "teacher1")
    assert client.post("/api/v1/courses", json={"code": "BAD", "name": "Bad", "capacity": 0}).status_code == 422
    assert client.post("/api/v1/courses", json={"code": "BAD", "name": "Bad", "capacity": True}).status_code == 422
