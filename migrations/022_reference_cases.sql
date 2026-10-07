-- Local human follow-up cases.  Source results and evaluation items remain immutable.
CREATE TABLE IF NOT EXISTS reference_cases (
 id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 question TEXT NOT NULL CHECK(length(question) BETWEEN 3 AND 1000),
 question_key TEXT NOT NULL CHECK(length(question_key)=64),
 result_id TEXT REFERENCES reference_results(id),
 evaluation_id TEXT REFERENCES reference_evaluations(id),
 evaluation_item_id TEXT REFERENCES reference_evaluation_items(id),
 source_status TEXT NOT NULL CHECK(source_status IN ('ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT','FAILED')),
 status TEXT NOT NULL CHECK(status IN ('OPEN','NEEDS_INFORMATION','IN_REVIEW','RESOLVED')),
 assignee TEXT NOT NULL DEFAULT '' CHECK(length(assignee)<=240),
 resolution TEXT NOT NULL DEFAULT '' CHECK(length(resolution)<=4000),
 version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 CHECK((result_id IS NOT NULL) OR (evaluation_item_id IS NOT NULL)),
 CHECK((evaluation_id IS NULL) = (evaluation_item_id IS NULL)),
 CHECK(status<>'RESOLVED' OR length(trim(resolution))>0)
);
CREATE INDEX IF NOT EXISTS ix_reference_cases_project
 ON reference_cases(project_id,status,updated_at DESC,id);
CREATE INDEX IF NOT EXISTS ix_reference_cases_run
 ON reference_cases(run_id,updated_at DESC,id);

CREATE TABLE IF NOT EXISTS reference_case_events (
 id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL REFERENCES reference_cases(id),
 actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 240),
 action TEXT NOT NULL CHECK(action IN ('CREATED','UPDATED','REOPENED','SUPPLEMENTAL_QUESTION','ATTACHMENTS_ADDED')),
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=4000),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_case_events_case
 ON reference_case_events(case_id,created_at,id);

CREATE TABLE IF NOT EXISTS reference_case_attachments (
 case_id TEXT NOT NULL REFERENCES reference_cases(id),
 document_id TEXT NOT NULL REFERENCES documents(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(case_id,document_id)
);

INSERT OR IGNORE INTO schema_migrations VALUES(22,datetime('now'));
