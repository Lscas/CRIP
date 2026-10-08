-- Preserve the source of explicitly imported files without changing legacy uploads.
CREATE TABLE IF NOT EXISTS upload_sources (
 upload_id TEXT PRIMARY KEY REFERENCES uploads(id),
 source_document_id TEXT NOT NULL REFERENCES documents(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('EMAIL_ATTACHMENT')),
 source_detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_upload_sources_document
 ON upload_sources(source_document_id);
CREATE INDEX IF NOT EXISTS ix_uploads_document
 ON uploads(document_id);
INSERT OR IGNORE INTO schema_migrations VALUES(5,datetime('now'));
