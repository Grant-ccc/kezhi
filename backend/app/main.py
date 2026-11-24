import logging
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

import pymysql
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from app.core.config import get_settings
from app.core.db import read_connection
from app.core.errors import BusinessError
from app.core.security import COOKIE_NAME, create_token, current_user, hasher, verify_password
from app.services import courses

app = FastAPI(title="课程学习助手 API", version="0.1.0")
logger = logging.getLogger(__name__)
User = Annotated[dict, Depends(current_user)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
attempts: dict[str, deque] = defaultdict(deque)
attempt_lock = Lock()
# Constant dummy hash avoids a username-dependent fast path.
dummy_hash = hasher.hash("not-a-real-user-password")


def error_response(request: Request, status: int, code: str, message: str):
    return JSONResponse(status_code=status, content={"code": code, "message": message, "request_id": getattr(request.state, "request_id", "")})


@app.middleware("http")
async def request_guard(request: Request, call_next):
    request.state.request_id = uuid4().hex
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {value.strip() for value in get_settings().allowed_origins.split(",")}
        if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(request, 403, "ORIGIN_FORBIDDEN", "请求来源不受信任")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(BusinessError)
async def business_error(request: Request, error: BusinessError):
    return error_response(request, error.status, error.code, error.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return error_response(request, 422, "INVALID_INPUT", "请检查填写内容及数值范围")


@app.exception_handler(pymysql.IntegrityError)
async def integrity_error(request: Request, error: pymysql.IntegrityError):
    return error_response(request, 409, "DATA_CONFLICT", "记录重复或与现有业务数据冲突")


@app.exception_handler(pymysql.MySQLError)
async def database_error(request: Request, error: pymysql.MySQLError):
    if error.args and error.args[0] in {3819, 4025}:
        return error_response(request, 409, "BUSINESS_CONSTRAINT", "输入不符合业务约束")
    logger.error("Database operation failed; request_id=%s, error_code=%s", request.state.request_id, error.args[0] if error.args else "unknown")
    return error_response(request, 503, "DATABASE_UNAVAILABLE", "数据库暂时不可用，请稍后重试")


class LoginPayload(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    password: str = Field(min_length=1, max_length=256)


class CoursePayload(BaseModel):
    name: Text
    description: str = Field(default="", max_length=4000)
    capacity: int = Field(strict=True, ge=1, le=10000)
    enrollment_status: Literal["OPEN", "CLOSED"] = "OPEN"


class NewCourse(CoursePayload):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


@app.get("/api/v1/health")
def health():
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 AS ok")
    return {"status": "ok", "database": "seekdb", "models_configured": False, "milestone": "courses"}


@app.post("/api/v1/auth/login")
def login(payload: LoginPayload, request: Request, response: Response):
    key = request.client.host if request.client else "unknown"
    now = monotonic()
    with attempt_lock:
        recent = attempts[key]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 10:
            raise BusinessError(429, "LOGIN_RATE_LIMIT", "登录尝试过于频繁，请一分钟后重试")
        recent.append(now)
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,username,display_name,role,status,password_hash FROM users WHERE username=%s", (payload.username.lower(),))
        user = cursor.fetchone()
    valid = verify_password(payload.password, user["password_hash"] if user else dummy_hash)
    if not user or not valid or user["status"] != "ACTIVE":
        raise BusinessError(401, "INVALID_CREDENTIALS", "账号或密码错误")
    response.set_cookie(COOKIE_NAME, create_token(user["id"]), httponly=True, secure=get_settings().cookie_secure, samesite="strict", max_age=3600, path="/")
    return {key: user[key] for key in ("id", "username", "display_name", "role")}


@app.post("/api/v1/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"message": "已退出登录"}


@app.get("/api/v1/auth/me")
def me(user: User):
    return user


@app.get("/api/v1/courses")
def course_list(user: User, q: str = Query(default="", max_length=120), mine: bool = False, page: int = Query(default=1, ge=1, le=10000)):
    return courses.list_courses(user, q.strip(), mine, page)


@app.post("/api/v1/courses", status_code=201)
def course_create(payload: NewCourse, user: User):
    return courses.create_course(user, payload.model_dump())


@app.get("/api/v1/courses/{course_id}/workspace")
def course_workspace(course_id: int, user: User):
    return courses.workspace(user, course_id)


@app.put("/api/v1/courses/{course_id}")
def course_update(course_id: int, payload: CoursePayload, user: User):
    return courses.update_course(user, course_id, payload.model_dump())


@app.delete("/api/v1/courses/{course_id}")
def course_delete(course_id: int, user: User):
    return courses.delete_course(user, course_id)


@app.post("/api/v1/courses/{course_id}/enrollment")
def enroll(course_id: int, user: User):
    return courses.change_enrollment(user, course_id, True)


@app.delete("/api/v1/courses/{course_id}/enrollment")
def withdraw(course_id: int, user: User):
    return courses.change_enrollment(user, course_id, False)
