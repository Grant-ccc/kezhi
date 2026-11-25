-- Applied conditionally by app.migrate to preserve existing rows.
ALTER TABLE index_jobs ADD COLUMN stage VARCHAR(16) NOT NULL DEFAULT 'PARSE';
ALTER TABLE index_jobs ADD COLUMN model_calls JSON NULL;
ALTER TABLE index_jobs ADD CONSTRAINT chk_job_stage CHECK(stage IN ('PARSE','EMBEDDING'));
