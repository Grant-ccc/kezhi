-- JOIN: courses taught by a teacher (example teacher id = 1).
SELECT c.id,c.name,u.display_name
FROM courses c JOIN course_teachers ct ON ct.course_id=c.id
JOIN users u ON u.id=ct.teacher_id
WHERE ct.teacher_id=1 ORDER BY c.id;

-- Aggregate separately before joining to prevent multiplied counts.
SELECT c.id,c.name,COALESCE(e.enrolled_count,0) AS enrolled_count,
       COALESCE(d.document_count,0) AS document_count
FROM courses c
LEFT JOIN (SELECT course_id,COUNT(*) AS enrolled_count FROM enrollments WHERE status='ACTIVE' GROUP BY course_id) e ON e.course_id=c.id
LEFT JOIN (SELECT course_id,COUNT(*) AS document_count FROM documents WHERE kind='TEACHING' AND status<>'DELETED' GROUP BY course_id) d ON d.course_id=c.id
ORDER BY c.id;

-- Index inspection on real seeded data.
EXPLAIN SELECT course_id FROM enrollments WHERE student_id=11 AND status='ACTIVE';
