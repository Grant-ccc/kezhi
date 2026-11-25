from app.core.db import read_connection, transaction
from app.core.errors import BusinessError


def list_courses(user: dict, query: str = "", mine: bool = False, page: int = 1):
    conditions, params = ["1=1"], []
    if query:
        conditions.append("(c.name LIKE %s OR c.code LIKE %s)")
        params.extend([f"%{query}%", f"%{query}%"])
    if mine:
        if user["role"] == "TEACHER":
            conditions.append("EXISTS (SELECT 1 FROM course_teachers ct WHERE ct.course_id=c.id AND ct.teacher_id=%s)")
        else:
            conditions.append("EXISTS (SELECT 1 FROM enrollments e WHERE e.course_id=c.id AND e.student_id=%s AND e.status='ACTIVE')")
        params.append(user["id"])
    where = " AND ".join(conditions)
    sql = f"""
        SELECT c.id,c.code,c.name,c.description,c.capacity,c.enrollment_status,
          (SELECT COUNT(*) FROM enrollments e WHERE e.course_id=c.id AND e.status='ACTIVE') AS enrolled_count,
          (SELECT COUNT(*) FROM documents d WHERE d.course_id=c.id AND d.kind='TEACHING' AND d.status<>'DELETED') AS document_count,
          (SELECT GROUP_CONCAT(u.display_name ORDER BY u.id SEPARATOR '、')
           FROM course_teachers ct JOIN users u ON u.id=ct.teacher_id WHERE ct.course_id=c.id) AS teachers,
          EXISTS(SELECT 1 FROM enrollments e WHERE e.course_id=c.id AND e.student_id=%s AND e.status='ACTIVE') AS is_enrolled,
          EXISTS(SELECT 1 FROM course_teachers ct WHERE ct.course_id=c.id AND ct.teacher_id=%s) AS can_manage
        FROM courses c WHERE {where} ORDER BY c.id LIMIT 24 OFFSET %s
    """
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (user["id"], user["id"], *params, (page - 1) * 24))
        items = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) AS total FROM courses c WHERE {where}", params)
        total = cursor.fetchone()["total"]
    for item in items:
        item["is_enrolled"] = bool(item["is_enrolled"])
        item["can_manage"] = bool(item["can_manage"])
    return {"items": items, "total": total, "page": page, "page_size": 24}


def require_teacher(user: dict):
    if user["role"] != "TEACHER":
        raise BusinessError(403, "TEACHER_REQUIRED", "仅教师可以管理课程")


def workspace(user: dict, course_id: int):
    with read_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id,code,name,description FROM courses WHERE id=%s", (course_id,))
        course = cursor.fetchone()
        if not course:
            raise BusinessError(404, "COURSE_NOT_FOUND", "课程不存在")
        if user["role"] == "TEACHER":
            require_manager(cursor, user["id"], course_id)
            condition, parameters = "kind='TEACHING'", (course_id,)
        else:
            cursor.execute("SELECT id FROM enrollments WHERE course_id=%s AND student_id=%s AND status='ACTIVE'", (course_id, user["id"]))
            if not cursor.fetchone():
                raise BusinessError(403, "ENROLLMENT_REQUIRED", "请先选修这门课程")
            condition, parameters = "(kind='TEACHING' OR (kind='NOTE' AND owner_id=%s))", (course_id, user["id"])
        cursor.execute(f"SELECT id,title,kind,status,content_version FROM documents WHERE course_id=%s AND status<>'DELETED' AND {condition} ORDER BY id", parameters)
        return {"course": course, "documents": cursor.fetchall(), "question_answering_available": False}


def lock_course(cursor, course_id: int):
    cursor.execute("SELECT * FROM courses WHERE id=%s FOR UPDATE", (course_id,))
    course = cursor.fetchone()
    if not course:
        raise BusinessError(404, "COURSE_NOT_FOUND", "课程不存在")
    return course


def require_manager(cursor, user_id: int, course_id: int):
    cursor.execute("SELECT id FROM course_teachers WHERE course_id=%s AND teacher_id=%s", (course_id, user_id))
    if not cursor.fetchone():
        raise BusinessError(403, "COURSE_FORBIDDEN", "只能管理自己负责的课程")


def create_course(user: dict, payload: dict):
    require_teacher(user)
    with transaction() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO courses(code,name,description,capacity,enrollment_status,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
                       (payload["code"], payload["name"], payload["description"], payload["capacity"], payload["enrollment_status"], user["id"]))
        course_id = cursor.lastrowid
        cursor.execute("INSERT INTO course_teachers(course_id,teacher_id) VALUES(%s,%s)", (course_id, user["id"]))
    return {"id": course_id}


def change_enrollment(user: dict, course_id: int, enroll: bool):
    if user["role"] != "STUDENT":
        raise BusinessError(403, "STUDENT_REQUIRED", "仅学生可以选课或退课")
    with transaction() as connection, connection.cursor() as cursor:
        course = lock_course(cursor, course_id)
        cursor.execute("SELECT id,status FROM enrollments WHERE course_id=%s AND student_id=%s", (course_id, user["id"]))
        existing = cursor.fetchone()
        active = existing is not None and existing["status"] == "ACTIVE"
        if enroll:
            if active:
                raise BusinessError(409, "ALREADY_ENROLLED", "你已经选过这门课程")
            if course["enrollment_status"] != "OPEN":
                raise BusinessError(409, "ENROLLMENT_CLOSED", "这门课程暂未开放选课")
            cursor.execute("SELECT COUNT(*) AS count FROM enrollments WHERE course_id=%s AND status='ACTIVE'", (course_id,))
            if cursor.fetchone()["count"] >= course["capacity"]:
                raise BusinessError(409, "COURSE_FULL", "课程名额已满")
            if existing:
                cursor.execute("UPDATE enrollments SET status='ACTIVE',enrolled_at=UTC_TIMESTAMP(6),dropped_at=NULL WHERE id=%s", (existing["id"],))
            else:
                cursor.execute("INSERT INTO enrollments(course_id,student_id,status) VALUES(%s,%s,'ACTIVE')", (course_id, user["id"]))
        else:
            if not active:
                raise BusinessError(409, "NOT_ENROLLED", "你尚未选修这门课程")
            cursor.execute("UPDATE enrollments SET status='DROPPED',dropped_at=UTC_TIMESTAMP(6) WHERE id=%s", (existing["id"],))
    return {"message": "选课成功" if enroll else "已退出课程"}


def update_course(user: dict, course_id: int, payload: dict):
    require_teacher(user)
    with transaction() as connection, connection.cursor() as cursor:
        lock_course(cursor, course_id)
        require_manager(cursor, user["id"], course_id)
        cursor.execute("SELECT COUNT(*) AS count FROM enrollments WHERE course_id=%s AND status='ACTIVE'", (course_id,))
        if payload["capacity"] < cursor.fetchone()["count"]:
            raise BusinessError(409, "CAPACITY_CONFLICT", "容量不能小于当前已选人数")
        cursor.execute("UPDATE courses SET name=%s,description=%s,capacity=%s,enrollment_status=%s WHERE id=%s",
                       (payload["name"], payload["description"], payload["capacity"], payload["enrollment_status"], course_id))
    return {"message": "课程已更新"}


def delete_course(user: dict, course_id: int):
    require_teacher(user)
    with transaction() as connection, connection.cursor() as cursor:
        lock_course(cursor, course_id)
        require_manager(cursor, user["id"], course_id)
        # Retain all historical relationships; only completely unused courses can be removed.
        for table in ("enrollments", "documents", "qa_sessions", "retrieval_requests"):
            cursor.execute(f"SELECT id FROM {table} WHERE course_id=%s LIMIT 1", (course_id,))
            if cursor.fetchone():
                raise BusinessError(409, "COURSE_HAS_RECORDS", "课程已有业务记录，请关闭选课以保留历史")
        cursor.execute("DELETE FROM course_teachers WHERE course_id=%s", (course_id,))
        cursor.execute("DELETE FROM courses WHERE id=%s", (course_id,))
    return {"message": "课程已删除"}
