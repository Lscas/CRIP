-- Retain only the safe model-input receipt for a contract-rejected Reference
-- evaluation item.  Rejected provider text and hidden reasoning stay absent.
ALTER TABLE reference_evaluation_failures
 ADD COLUMN execution_receipt_json TEXT;

INSERT OR IGNORE INTO schema_migrations VALUES(17,datetime('now'));
