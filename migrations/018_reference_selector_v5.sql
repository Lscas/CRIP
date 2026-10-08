-- Expand the frozen Reference selector allowlist without rewriting existing
-- v3/v4 evaluations. SQLite cannot alter a column CHECK in place, so rebuild
-- only the parent table while foreign-key enforcement is temporarily disabled.
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;

CREATE TABLE reference_evaluations_v5 (
 id TEXT PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES projects(id),
 run_id TEXT NOT NULL REFERENCES runs(id),
 snapshot_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 150),
 question_set_hash TEXT NOT NULL CHECK(length(question_set_hash)=64),
 profile_json TEXT NOT NULL CHECK(length(profile_json) BETWEEN 2 AND 4000),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 selector_version TEXT NOT NULL DEFAULT 'literal-page-selector-3'
  CHECK(selector_version IN (
   'literal-page-selector-3','literal-page-selector-4','literal-page-selector-5'))
);

INSERT INTO reference_evaluations_v5(
 id,project_id,run_id,snapshot_id,name,question_set_hash,profile_json,
 created_at,updated_at,selector_version)
SELECT id,project_id,run_id,snapshot_id,name,question_set_hash,profile_json,
       created_at,updated_at,selector_version
FROM reference_evaluations;

DROP TABLE reference_evaluations;
ALTER TABLE reference_evaluations_v5 RENAME TO reference_evaluations;
CREATE INDEX ix_reference_evaluations_project
 ON reference_evaluations(project_id,created_at DESC,id);
CREATE INDEX ix_reference_evaluations_run
 ON reference_evaluations(run_id,created_at DESC,id);

INSERT OR IGNORE INTO schema_migrations VALUES(18,datetime('now'));
COMMIT;
PRAGMA foreign_keys=ON;
