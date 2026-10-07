-- Additive human benchmark adjudication for terminal Reference evaluation items.
-- Model results, citations, source evidence and ordinary result review are not rewritten.
CREATE TABLE IF NOT EXISTS reference_evaluation_adjudications (
 item_id TEXT PRIMARY KEY REFERENCES reference_evaluation_items(id),
 evaluation_id TEXT NOT NULL REFERENCES reference_evaluations(id),
 verdict TEXT NOT NULL CHECK(verdict IN ('FULLY_USABLE','PARTIAL','UNUSABLE')),
 unsupported_claim INTEGER NOT NULL CHECK(unsupported_claim IN (0,1)),
 note TEXT NOT NULL CHECK(length(note)<=2000),
 version INTEGER NOT NULL CHECK(version>=1),
 event_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_adjudications_evaluation
 ON reference_evaluation_adjudications(evaluation_id,item_id);

CREATE TABLE IF NOT EXISTS reference_evaluation_adjudication_events (
 id TEXT PRIMARY KEY,
 evaluation_id TEXT NOT NULL REFERENCES reference_evaluations(id),
 item_id TEXT NOT NULL REFERENCES reference_evaluation_items(id),
 actor TEXT NOT NULL,
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_adjudication_events_item
 ON reference_evaluation_adjudication_events(evaluation_id,item_id,created_at,id);

INSERT OR IGNORE INTO schema_migrations VALUES(15,datetime('now'));
