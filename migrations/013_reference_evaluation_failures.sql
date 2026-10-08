-- Durable safe terminal failures for frozen Reference QA evaluation items.
-- Rejected provider text and exception messages are never stored here.
CREATE TABLE IF NOT EXISTS reference_evaluation_failures (
 id TEXT PRIMARY KEY,
 evaluation_id TEXT NOT NULL REFERENCES reference_evaluations(id),
 item_id TEXT NOT NULL UNIQUE REFERENCES reference_evaluation_items(id),
 code TEXT NOT NULL CHECK(code IN ('MODEL_OUTPUT_REJECTED')),
 stage TEXT NOT NULL CHECK(stage='EXECUTION'),
 detail TEXT NOT NULL CHECK(length(detail) BETWEEN 1 AND 240),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_evaluation_failures_evaluation
 ON reference_evaluation_failures(evaluation_id,created_at,id);

INSERT OR IGNORE INTO schema_migrations VALUES(13,datetime('now'));
