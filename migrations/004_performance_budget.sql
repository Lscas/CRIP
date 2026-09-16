-- Additive runtime metrics and project budget-limit audit history.
CREATE TABLE IF NOT EXISTS run_metrics (
 run_id TEXT NOT NULL REFERENCES runs(id),
 metric TEXT NOT NULL,
 samples INTEGER NOT NULL CHECK(samples>0),
 total_ms INTEGER NOT NULL CHECK(total_ms>=0),
 max_ms INTEGER NOT NULL CHECK(max_ms>=0),
 PRIMARY KEY(run_id,metric)
);
CREATE TABLE IF NOT EXISTS budget_limit_events (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 actor TEXT NOT NULL,
 previous_units INTEGER NOT NULL CHECK(previous_units>=0),
 new_units INTEGER NOT NULL CHECK(new_units>0),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_budget_limit_events_project
 ON budget_limit_events(project_id,created_at);
INSERT OR IGNORE INTO schema_migrations VALUES(4,datetime('now'));
