-- Password hash is substituted by bootstrap.py from the local demo password.
-- Existing records and passwords are deliberately preserved on repeated runs.
INSERT IGNORE INTO users(id,username,password_hash,display_name,role) VALUES
(1,'teacher1',{{DEMO_PASSWORD_HASH}},'林老师','TEACHER'),
(2,'teacher2',{{DEMO_PASSWORD_HASH}},'陈老师','TEACHER'),
(11,'student1',{{DEMO_PASSWORD_HASH}},'林同学','STUDENT'),
(12,'student2',{{DEMO_PASSWORD_HASH}},'张同学','STUDENT'),
(13,'student3',{{DEMO_PASSWORD_HASH}},'李同学','STUDENT');

INSERT IGNORE INTO courses(id,code,name,description,capacity,enrollment_status,created_by) VALUES
(1,'DB-2026-FALL','数据库原理','从关系模型到事务并发，结合课件与个人笔记，建立可核对的数据库知识体系。',40,'OPEN',1),
(2,'ALGO-2026-FALL','算法设计与分析','理解算法的设计思路与复杂度，在课程资料中连接概念与例题。',35,'OPEN',2),
(3,'DB-LAB-2026','数据库实验研讨','一个名额的演示课程，用于验证选课容量约束与并发事务。',1,'OPEN',1);

INSERT IGNORE INTO course_teachers(course_id,teacher_id) VALUES (1,1),(2,2),(3,1);
INSERT IGNORE INTO enrollments(course_id,student_id,status) VALUES (1,11,'ACTIVE'),(2,12,'ACTIVE');
