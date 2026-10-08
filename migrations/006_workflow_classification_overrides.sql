-- Human workflow classification corrections are run-scoped overlays. Parser output stays immutable.
CREATE TABLE IF NOT EXISTS workflow_classification_overrides (
 run_id TEXT NOT NULL REFERENCES runs(id),
 document_id TEXT NOT NULL REFERENCES documents(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 workflow_type TEXT NOT NULL CHECK(workflow_type IN ('DETECTED','OTHER','RFI','SUBMITTAL')),
 identifier TEXT,
 role TEXT,
 status TEXT,
 version INTEGER NOT NULL CHECK(version>0),
 note TEXT NOT NULL CHECK(length(note)<=1000),
 updated_at TEXT NOT NULL,
 PRIMARY KEY(run_id,document_id),
 CHECK(length(COALESCE(identifier,''))<=64),
 CHECK(length(COALESCE(status,''))<=80),
 CHECK(
  (workflow_type IN ('DETECTED','OTHER') AND identifier IS NULL AND role IS NULL AND status IS NULL)
  OR (workflow_type='RFI' AND identifier IS NOT NULL AND role IN ('UNKNOWN','QUESTION','RESPONSE'))
  OR (workflow_type='SUBMITTAL' AND identifier IS NOT NULL AND role='SUBMITTAL')
 )
);
CREATE INDEX IF NOT EXISTS ix_workflow_classification_overrides_project
 ON workflow_classification_overrides(project_id,run_id);

CREATE TABLE IF NOT EXISTS workflow_classification_events (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id),
 document_id TEXT NOT NULL REFERENCES documents(id),
 actor TEXT NOT NULL,
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 note TEXT NOT NULL CHECK(length(note)<=1000),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_workflow_classification_events_target
 ON workflow_classification_events(run_id,document_id,created_at);

INSERT OR IGNORE INTO schema_migrations VALUES(6,datetime('now'));
