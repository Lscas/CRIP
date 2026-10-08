-- Optional complete safe receipt chain for named-v9 terminal evaluation failures.
BEGIN IMMEDIATE;
ALTER TABLE reference_evaluation_failures ADD COLUMN failure_execution_json TEXT;
INSERT OR IGNORE INTO schema_migrations VALUES(26,datetime('now'));
COMMIT;
