-- Additive migration. Existing documents, records, review history and budgets are untouched.
CREATE TABLE IF NOT EXISTS verification_reports (
 record_id TEXT PRIMARY KEY REFERENCES records(id), payload TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_events (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_verification_events_record ON verification_events(record_id);
CREATE TABLE IF NOT EXISTS verification_jobs (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), run_id TEXT NOT NULL REFERENCES runs(id),
 candidate_hash TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, review_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_verification_jobs_state ON verification_jobs(state);
INSERT OR IGNORE INTO schema_migrations VALUES(2,datetime('now'));
