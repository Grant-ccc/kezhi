from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request

from app.core.config import get_settings
from app.core.db import read_connection
from app.core.errors import BusinessError

hasher = PasswordHasher()
COOKIE_NAME = "course_session"


def verify_password(value: str, stored: str) -> bool:
    try:
        return hasher.verify(stored, value)
    except (VerificationError, InvalidHashError):
        return False


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=1), "iss": "course-assistant"}, get_settings().jwt_secret, algorithm="HS256")


def current_user(request: Request) -> dict:
    try:
        claims = jwt.decode(request.cookies.get(COOKIE_NAME, ""), get_settings().jwt_secret,
                            algorithms=["HS256"], issuer="course-assistant", options={"require": ["sub", "iat", "exp", "iss"]})
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise BusinessError(401, "LOGIN_REQUIRED", "请先登录") from None
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, username, display_name, role, status FROM users WHERE id=%s", (user_id,))
        user = cursor.fetchone()
    if not user or user["status"] != "ACTIVE":
        raise BusinessError(401, "LOGIN_REQUIRED", "账号已失效，请重新登录")
    return {key: user[key] for key in ("id", "username", "display_name", "role")}
