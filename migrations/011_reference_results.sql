-- Additive local persistence for Reference QA results, citations and human review.
-- Parser evidence, legacy records and model-call history are not rewritten.
CREATE TABLE IF NOT EXISTS reference_results (
 id TEXT PRIMARY KEY,
 result_key TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 qa_version TEXT NOT NULL CHECK(qa_version='3'),
 question TEXT NOT NULL CHECK(length(question) BETWEEN 3 AND 1000),
 question_key TEXT NOT NULL CHECK(length(question_key)=64),
 status TEXT NOT NULL CHECK(status IN ('ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT','MODEL_DISABLED')),
 answer_basis TEXT NOT NULL CHECK(length(answer_basis) BETWEEN 1 AND 120),
 provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 180),
 model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 180),
 result_hash TEXT NOT NULL CHECK(length(result_hash)=64),
 result_json TEXT NOT NULL,
 review_status TEXT NOT NULL CHECK(review_status IN ('PENDING','ACCEPTED','REJECTED','NOT_APPLICABLE')),
 review_version INTEGER NOT NULL DEFAULT 0 CHECK(review_version>=0),
 review_event_id TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_results_project
 ON reference_results(project_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS ix_reference_results_run
 ON reference_results(run_id,created_at DESC,id);

CREATE TABLE IF NOT EXISTS reference_result_citations (
 result_id TEXT NOT NULL REFERENCES reference_results(id),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 claim_index INTEGER NOT NULL CHECK(claim_index>=0),
 citation_index INTEGER NOT NULL CHECK(citation_index>=0),
 citation_type TEXT NOT NULL CHECK(citation_type IN ('TEXT','IMAGE_REGION')),
 evidence_id TEXT,
 region_id TEXT,
 document_id TEXT NOT NULL REFERENCES documents(id),
 page_number INTEGER,
 citation_json TEXT NOT NULL,
 PRIMARY KEY(result_id,ordinal),
 CHECK(
  (citation_type='TEXT' AND evidence_id IS NOT NULL AND region_id IS NULL)
  OR (citation_type='IMAGE_REGION' AND evidence_id IS NULL AND region_id IS NOT NULL)
 )
);
CREATE INDEX IF NOT EXISTS ix_reference_citations_document
 ON reference_result_citations(document_id,result_id);

CREATE TABLE IF NOT EXISTS reference_result_review_events (
 id TEXT PRIMARY KEY,
 result_id TEXT NOT NULL REFERENCES reference_results(id),
 actor TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('ACCEPTED','REJECTED')),
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 note TEXT NOT NULL CHECK(length(note)<=2000),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reference_review_events_result
 ON reference_result_review_events(result_id,created_at,id);

INSERT OR IGNORE INTO schema_migrations VALUES(11,datetime('now'));
