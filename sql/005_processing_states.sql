-- Add the expanded constraint before dropping the old one: no unguarded interval.
-- Use app.migrate for restart-safe execution instead of replaying these manually.
ALTER TABLE documents ADD CONSTRAINT chk_document_status_v2 CHECK(status IN ('PROCESSING','PARSED','READY','FAILED','DELETED'));
ALTER TABLE documents DROP CHECK chk_document_status;
ALTER TABLE index_jobs ADD CONSTRAINT chk_job_status_v2 CHECK(status IN ('PENDING','RUNNING','PARSED','SUCCEEDED','FAILED','CANCELLED'));
ALTER TABLE index_jobs DROP CHECK chk_job_status;
