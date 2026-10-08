CREATE TABLE {{TABLE}} (
 id TEXT PRIMARY KEY,
 result_key TEXT NOT NULL UNIQUE,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 qa_version TEXT NOT NULL CHECK(qa_version='3'),
 question TEXT NOT NULL CHECK(length(question) BETWEEN 3 AND 1000),
 question_key TEXT NOT NULL CHECK(length(question_key)=64),
 result_kind TEXT NOT NULL DEFAULT 'REFERENCE_QA_RESULT' CHECK(result_kind IN ('REFERENCE_QA_RESULT','PROJECTION_LOOP_OUTCOME')),
 status TEXT NOT NULL CHECK((result_kind='REFERENCE_QA_RESULT' AND status IN ('ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT','MODEL_DISABLED')) OR (result_kind='PROJECTION_LOOP_OUTCOME' AND status IN ('REVIEW_REQUIRED','CANNOT_ANSWER','NEED_USER_INPUT'))),
 answer_basis TEXT NOT NULL CHECK(length(answer_basis) BETWEEN 1 AND 120),
 provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 180),
 model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 180),
 result_hash TEXT NOT NULL CHECK(length(result_hash)=64),
 result_json TEXT NOT NULL,
 review_status TEXT NOT NULL CHECK((result_kind='PROJECTION_LOOP_OUTCOME' AND review_status='NOT_APPLICABLE') OR (result_kind='REFERENCE_QA_RESULT' AND review_status IN ('PENDING','ACCEPTED','REJECTED','NOT_APPLICABLE'))),
 review_version INTEGER NOT NULL DEFAULT 0 CHECK(review_version>=0),
 review_event_id TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX {{PROJECT_INDEX}} ON reference_results(project_id,created_at DESC,id);
CREATE INDEX {{RUN_INDEX}} ON reference_results(run_id,created_at DESC,id);
