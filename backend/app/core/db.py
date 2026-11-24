from contextlib import contextmanager

import pymysql
from app.core.config import get_settings


def connect(*, root: bool = False, database: str | None = None):
    settings = get_settings()
    connection = pymysql.connect(
        host=settings.db_host, port=settings.db_port,
        user="root" if root else settings.db_user,
        password=settings.seekdb_root_password if root else settings.db_password,
        database=database if database is not None else settings.db_name,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=False, connect_timeout=5, read_timeout=15, write_timeout=15,
    )
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cursor.execute("SET time_zone = '+00:00'")
    return connection


@contextmanager
def transaction():
    connection = connect()
    try:
        connection.begin()
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def read_connection():
    connection = connect()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
