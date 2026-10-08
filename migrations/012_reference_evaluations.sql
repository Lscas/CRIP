-- Additive local task layer for frozen Reference QA question sets.
-- Existing analysis runs, results, citations, reviews and model-call records are retained.
CREATE TABLE IF NOT EXISTS reference_evaluations (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 150),
 question_set_hash TEXT NOT NULL CHECK(length(question_set_hash)=64),
 profile_json TEXT NOT NULL CHECK(length(profile_json) BETWEEN 2 AND 4000),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_evaluations_project
 ON reference_evaluations(project_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS ix_reference_evaluations_run
 ON reference_evaluations(run_id,created_at DESC,id);

CREATE TABLE IF NOT EXISTS reference_evaluation_items (
 id TEXT PRIMARY KEY,
 evaluation_id TEXT NOT NULL REFERENCES reference_evaluations(id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 49),
 question TEXT NOT NULL CHECK(length(question) BETWEEN 3 AND 1000),
 question_key TEXT NOT NULL CHECK(length(question_key)=64),
 result_id TEXT REFERENCES reference_results(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(evaluation_id,ordinal),
 UNIQUE(evaluation_id,question_key)
);
CREATE INDEX IF NOT EXISTS ix_reference_evaluation_items_result
 ON reference_evaluation_items(result_id,evaluation_id);

INSERT OR IGNORE INTO schema_migrations VALUES(12,datetime('now'));
