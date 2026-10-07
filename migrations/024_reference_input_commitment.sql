-- Additive named Reference-route commitment; legacy calls remain NULL.
ALTER TABLE model_calls ADD COLUMN reference_input_commitment_version TEXT;
ALTER TABLE model_calls ADD COLUMN reference_input_commitment_sha256 TEXT;
INSERT OR IGNORE INTO schema_migrations VALUES(24,datetime('now'));
