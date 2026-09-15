-- Additive migration. Unknown model calls can be reconciled without deleting cost history.
CREATE TABLE IF NOT EXISTS call_reconciliation_events (
 id TEXT PRIMARY KEY,
 call_id TEXT NOT NULL REFERENCES model_calls(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 actor TEXT NOT NULL,
 payload TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_call_reconciliation_project
 ON call_reconciliation_events(project_id,created_at);
INSERT OR IGNORE INTO schema_migrations VALUES(3,datetime('now'));
