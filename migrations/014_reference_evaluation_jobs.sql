-- Additive managed execution sessions for frozen Reference evaluations.
-- Jobs retain only local task state; model output remains in reference_results.
CREATE TABLE IF NOT EXISTS reference_evaluation_jobs (
 id TEXT PRIMARY KEY,
 evaluation_id TEXT NOT NULL REFERENCES reference_evaluations(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 state TEXT NOT NULL CHECK(state IN (
   'QUEUED','RUNNING','STOP_REQUESTED','COMPLETED','STOPPED','HALTED','INTERRUPTED')),
 reason_code TEXT NOT NULL CHECK(reason_code IN (
   'NONE','COMPLETED','USER_STOPPED','PROFILE_CHANGED','UNRESOLVED_CALL',
   'ACTIVE_WORK_CONFLICT','PROVIDER_HALTED','SERVICE_INTERRUPTED','INTERNAL_ERROR')),
 stop_requested INTEGER NOT NULL DEFAULT 0 CHECK(stop_requested IN (0,1)),
 pending_at_start INTEGER NOT NULL CHECK(pending_at_start BETWEEN 1 AND 50),
 completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count BETWEEN 0 AND 50),
 failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count BETWEEN 0 AND 50),
 current_item_id TEXT REFERENCES reference_evaluation_items(id),
 confirmed_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_reference_evaluation_jobs_project
 ON reference_evaluation_jobs(project_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS ix_reference_evaluation_jobs_evaluation
 ON reference_evaluation_jobs(evaluation_id,created_at DESC,id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reference_evaluation_jobs_active
 ON reference_evaluation_jobs((1))
 WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED');

INSERT OR IGNORE INTO schema_migrations VALUES(14,datetime('now'));
