"""SQL shared by candidate inspection and exact ranking, before LIMIT is applied."""
import json
import math
from typing import Sequence

AUTHORIZED_FROM = """
 FROM document_chunks ch
 JOIN documents d ON d.id=ch.document_id
 JOIN users actor ON actor.id=%s AND actor.status='ACTIVE'
 WHERE d.course_id=%s AND d.status='READY' AND d.deleted_at IS NULL
   AND ch.content_version=d.content_version AND ch.is_current=TRUE
   AND ch.embedding_profile=%s AND ch.embedding IS NOT NULL
   AND (
     (actor.role='STUDENT'
      AND EXISTS (SELECT 1 FROM enrollments e WHERE e.course_id=d.course_id
                  AND e.student_id=actor.id AND e.status='ACTIVE')
      AND (d.kind='TEACHING' OR (d.kind='NOTE' AND d.owner_id=actor.id)))
     OR
     (actor.role='TEACHER' AND d.kind='TEACHING'
      AND EXISTS (SELECT 1 FROM course_teachers ct WHERE ct.course_id=d.course_id
                  AND ct.teacher_id=actor.id))
   )
"""


def candidates(cursor, *, actor_id: int, course_id: int, profile: str):
    cursor.execute("SELECT ch.id AS chunk_id,d.id AS document_id" + AUTHORIZED_FROM + " ORDER BY ch.id",
                   (actor_id, course_id, profile))
    return cursor.fetchall()


def search(cursor, *, actor_id: int, course_id: int, profile: str,
           query_vector: Sequence[float], dimensions: int, top_k: int = 2):
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("Invalid retrieval limit")
    if (type(dimensions) is not int or not 1 <= dimensions <= 4096
            or len(query_vector) != dimensions
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in query_vector)):
        raise ValueError("Invalid query vector")
    norm = math.hypot(*query_vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid query vector")
    vector = json.dumps(list(query_vector), allow_nan=False, separators=(",", ":"))
    cursor.execute("""SELECT ch.id AS chunk_id,d.id AS document_id,d.title,ch.content,
                    cosine_distance(ch.embedding,%s) AS distance""" + AUTHORIZED_FROM +
                   " ORDER BY distance,ch.id LIMIT %s",
                   (vector, actor_id, course_id, profile, top_k))
    return cursor.fetchall()
