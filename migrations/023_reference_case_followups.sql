-- Immutable links from a human case to a later, explicitly saved Reference QA result.
CREATE TABLE IF NOT EXISTS reference_case_followups (
 id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL REFERENCES reference_cases(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 result_id TEXT NOT NULL REFERENCES reference_results(id),
 result_status TEXT NOT NULL CHECK(result_status IN ('ANSWERED','CANNOT_ANSWER','NEED_USER_INPUT')),
 proof_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
 created_at TEXT NOT NULL,
 UNIQUE(case_id,result_id)
);
CREATE INDEX IF NOT EXISTS ix_reference_case_followups_case ON reference_case_followups(case_id,created_at,id);
INSERT OR IGNORE INTO schema_migrations VALUES(23,datetime('now'));
