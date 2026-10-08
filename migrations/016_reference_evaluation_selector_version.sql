-- Freeze the local page-selector policy for every Reference evaluation.
-- Existing evaluations retain the six-page selector-v3 behavior; new rows
-- explicitly choose a supported version at creation time.
ALTER TABLE reference_evaluations
 ADD COLUMN selector_version TEXT NOT NULL DEFAULT 'literal-page-selector-3'
 CHECK(selector_version IN ('literal-page-selector-3','literal-page-selector-4'));

INSERT OR IGNORE INTO schema_migrations VALUES(16,datetime('now'));
