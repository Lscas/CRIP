PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 size INTEGER NOT NULL CHECK(size>=0), offset INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'UPLOADING', document_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_chunks (
 upload_id TEXT NOT NULL REFERENCES uploads(id), offset INTEGER NOT NULL,
 size INTEGER NOT NULL, checksum TEXT NOT NULL, PRIMARY KEY(upload_id,offset)
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
 size INTEGER NOT NULL, sha256 TEXT NOT NULL, object_key TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(project_id,sha256)
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), provider TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, document_ids TEXT NOT NULL, status TEXT NOT NULL,
 stage TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 started_epoch REAL NOT NULL, deadline_epoch REAL NOT NULL, coverage TEXT NOT NULL DEFAULT '{}',
 capabilities TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS document_results (
 run_id TEXT NOT NULL REFERENCES runs(id), document_id TEXT NOT NULL REFERENCES documents(id),
 status TEXT NOT NULL, summary TEXT NOT NULL, PRIMARY KEY(run_id,document_id)
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), project_id TEXT NOT NULL,
 document_id TEXT NOT NULL REFERENCES documents(id), payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING', extraction TEXT, error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_evidence_run ON evidence(run_id);
CREATE TABLE IF NOT EXISTS records (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), project_id TEXT NOT NULL,
 kind TEXT NOT NULL, envelope TEXT NOT NULL, review_version INTEGER NOT NULL DEFAULT 0,
 logical_key TEXT NOT NULL, UNIQUE(run_id,kind,logical_key)
);
CREATE INDEX IF NOT EXISTS ix_records_run ON records(run_id,kind);
CREATE TABLE IF NOT EXISTS review_events (
 id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id), actor TEXT NOT NULL,
 action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_accounts (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), limit_units INTEGER NOT NULL DEFAULT 300000000,
 spent_units INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS model_calls (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), run_id TEXT NOT NULL REFERENCES runs(id),
 task_key TEXT NOT NULL, model TEXT NOT NULL, state TEXT NOT NULL,
 reserved_units INTEGER NOT NULL CHECK(reserved_units>0), actual_units INTEGER,
 input_rate TEXT NOT NULL, output_rate TEXT NOT NULL, request_hash TEXT NOT NULL,
 usage TEXT, provider_request_id TEXT, response TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
 key TEXT PRIMARY KEY, project_id TEXT NOT NULL, response TEXT NOT NULL, expires_epoch REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
INSERT OR IGNORE INTO schema_migrations VALUES(1,datetime('now'));
