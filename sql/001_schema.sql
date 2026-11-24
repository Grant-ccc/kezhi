-- Business schema. Embedding dimension is deliberately deferred to 003_vectors.sql.template.
CREATE TABLE IF NOT EXISTS users (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 username VARCHAR(64) NOT NULL,
 password_hash VARCHAR(255) NOT NULL,
 display_name VARCHAR(120) NOT NULL,
 role VARCHAR(16) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_users_username(username),
 CONSTRAINT chk_user_role CHECK (role IN ('STUDENT','TEACHER')),
 CONSTRAINT chk_user_status CHECK (status IN ('ACTIVE','DISABLED'))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS courses (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 code VARCHAR(64) NOT NULL,
 name VARCHAR(120) NOT NULL,
 description TEXT NOT NULL,
 capacity INT NOT NULL,
 enrollment_status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
 created_by BIGINT NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_courses_code(code),
 KEY idx_courses_creator(created_by),
 CONSTRAINT fk_courses_creator FOREIGN KEY(created_by) REFERENCES users(id),
 CONSTRAINT chk_course_capacity CHECK(capacity>0),
 CONSTRAINT chk_course_status CHECK(enrollment_status IN ('OPEN','CLOSED'))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS course_teachers (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 course_id BIGINT NOT NULL,
 teacher_id BIGINT NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_course_teacher(course_id,teacher_id),
 KEY idx_teacher_courses(teacher_id,course_id),
 CONSTRAINT fk_teaching_course FOREIGN KEY(course_id) REFERENCES courses(id),
 CONSTRAINT fk_teaching_user FOREIGN KEY(teacher_id) REFERENCES users(id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS enrollments (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 course_id BIGINT NOT NULL,
 student_id BIGINT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
 enrolled_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 dropped_at DATETIME(6) NULL,
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_course_student(course_id,student_id),
 KEY idx_course_enrollment(course_id,status),
 KEY idx_student_enrollment(student_id,status,course_id),
 CONSTRAINT fk_enrollment_course FOREIGN KEY(course_id) REFERENCES courses(id),
 CONSTRAINT fk_enrollment_student FOREIGN KEY(student_id) REFERENCES users(id),
 CONSTRAINT chk_enrollment_status CHECK(status IN ('ACTIVE','DROPPED'))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS documents (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 course_id BIGINT NOT NULL,
 owner_id BIGINT NOT NULL,
 kind VARCHAR(16) NOT NULL,
 title VARCHAR(255) NOT NULL,
 original_filename VARCHAR(255) NOT NULL,
 storage_key VARCHAR(255) NOT NULL,
 media_type VARCHAR(120) NOT NULL,
 size_bytes BIGINT NOT NULL,
 content_hash CHAR(64) NOT NULL,
 upload_request_key VARCHAR(64) NOT NULL,
 upload_payload_hash CHAR(64) NOT NULL,
 content_version INT NOT NULL DEFAULT 1,
 status VARCHAR(16) NOT NULL DEFAULT 'PROCESSING',
 error_code VARCHAR(64) NULL,
 error_detail TEXT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 deleted_at DATETIME(6) NULL,
 UNIQUE KEY uq_document_upload(owner_id,upload_request_key),
 KEY idx_course_documents(course_id,kind,status),
 KEY idx_owner_documents(owner_id,kind,status),
 CONSTRAINT fk_document_course FOREIGN KEY(course_id) REFERENCES courses(id),
 CONSTRAINT fk_document_owner FOREIGN KEY(owner_id) REFERENCES users(id),
 CONSTRAINT chk_document_kind CHECK(kind IN ('TEACHING','NOTE')),
 CONSTRAINT chk_document_status CHECK(status IN ('PROCESSING','READY','FAILED','DELETED')),
 CONSTRAINT chk_document_size CHECK(size_bytes>0),
 CONSTRAINT chk_document_version CHECK(content_version>0)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS document_chunks (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 document_id BIGINT NOT NULL,
 content_version INT NOT NULL,
 chunk_index INT NOT NULL,
 content MEDIUMTEXT NOT NULL,
 page_number INT NULL,
 section_title VARCHAR(255) NULL,
 char_start INT NULL,
 char_end INT NULL,
 embedding_profile VARCHAR(255) NULL,
 is_current BOOLEAN NOT NULL DEFAULT FALSE,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_document_chunk(document_id,content_version,chunk_index),
 CONSTRAINT fk_chunk_document FOREIGN KEY(document_id) REFERENCES documents(id),
 CONSTRAINT chk_chunk_index CHECK(chunk_index>=0),
 CONSTRAINT chk_chunk_version CHECK(content_version>0)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_jobs (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 document_id BIGINT NOT NULL,
 content_version INT NOT NULL,
 requested_by BIGINT NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
 retry_round INT NOT NULL DEFAULT 0,
 attempt_count INT NOT NULL DEFAULT 0,
 next_run_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 lease_until DATETIME(6) NULL,
 worker_token VARCHAR(64) NULL,
 error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_index_version(document_id,content_version),
 UNIQUE KEY uq_index_request(requested_by,request_key),
 KEY idx_pending_jobs(status,next_run_at),
 CONSTRAINT fk_job_document FOREIGN KEY(document_id) REFERENCES documents(id),
 CONSTRAINT fk_job_requester FOREIGN KEY(requested_by) REFERENCES users(id),
 CONSTRAINT chk_job_status CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
 CONSTRAINT chk_job_attempts CHECK(attempt_count>=0 AND retry_round>=0)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS qa_sessions (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 user_id BIGINT NOT NULL,
 course_id BIGINT NOT NULL,
 title VARCHAR(255) NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 KEY idx_user_sessions(user_id,updated_at),
 KEY idx_session_course(course_id),
 CONSTRAINT fk_session_user FOREIGN KEY(user_id) REFERENCES users(id),
 CONSTRAINT fk_session_course FOREIGN KEY(course_id) REFERENCES courses(id)
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS qa_messages (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 session_id BIGINT NOT NULL,
 request_key VARCHAR(64) NOT NULL,
 question_hash CHAR(64) NOT NULL,
 question TEXT NOT NULL,
 answer MEDIUMTEXT NULL,
 status VARCHAR(32) NOT NULL DEFAULT 'PROCESSING',
 model_provider VARCHAR(120) NULL,
 model_version VARCHAR(255) NULL,
 prompt_version VARCHAR(64) NULL,
 retrieval_config_version VARCHAR(64) NULL,
 embedding_profile VARCHAR(255) NULL,
 input_tokens BIGINT NULL,
 output_tokens BIGINT NULL,
 estimated_cost DECIMAL(16,8) NULL,
 currency VARCHAR(8) NULL,
 attempt_count INT NOT NULL DEFAULT 0,
 model_calls JSON NULL,
 usage_complete BOOLEAN NOT NULL DEFAULT FALSE,
 retrieval_ms INT NULL,
 generation_ms INT NULL,
 total_ms INT NULL,
 error_code VARCHAR(64) NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 completed_at DATETIME(6) NULL,
 UNIQUE KEY uq_message_request(session_id,request_key),
 KEY idx_session_messages(session_id,created_at),
 CONSTRAINT fk_message_session FOREIGN KEY(session_id) REFERENCES qa_sessions(id),
 CONSTRAINT chk_message_status CHECK(status IN ('PROCESSING','SUCCEEDED','INSUFFICIENT_EVIDENCE','FAILED'))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS answer_evidence (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 message_id BIGINT NOT NULL,
 chunk_id BIGINT NOT NULL,
 retrieval_rank INT NOT NULL,
 retrieval_score DOUBLE NULL,
 sent_to_model BOOLEAN NOT NULL DEFAULT FALSE,
 citation_number INT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_evidence_chunk(message_id,chunk_id),
 UNIQUE KEY uq_evidence_citation(message_id,citation_number),
 KEY idx_evidence_chunk(chunk_id),
 CONSTRAINT fk_evidence_message FOREIGN KEY(message_id) REFERENCES qa_messages(id),
 CONSTRAINT fk_evidence_chunk FOREIGN KEY(chunk_id) REFERENCES document_chunks(id),
 CONSTRAINT chk_evidence_rank CHECK(retrieval_rank>0),
 CONSTRAINT chk_evidence_citation CHECK(citation_number IS NULL OR (citation_number>0 AND sent_to_model=TRUE))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS answer_feedback (
 id BIGINT PRIMARY KEY AUTO_INCREMENT,
 message_id BIGINT NOT NULL,
 rating VARCHAR(16) NOT NULL,
 reason VARCHAR(64) NULL,
 comment TEXT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
 UNIQUE KEY uq_feedback_message(message_id),
 CONSTRAINT fk_feedback_message FOREIGN KEY(message_id) REFERENCES qa_messages(id),
 CONSTRAINT chk_feedback_rating CHECK(rating IN ('HELPFUL','UNHELPFUL'))
) DEFAULT CHARSET=utf8mb4;
