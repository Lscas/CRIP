-- Optional local full-text index for run-scoped project questions.
-- Database.__init__ treats a missing SQLite FTS5 module as a supported fallback.
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_search USING fts5(
 file_name,
 locator,
 raw_text,
 tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO evidence_search(rowid,file_name,locator,raw_text)
SELECT e.rowid,d.name,
       TRIM(
         COALESCE(json_extract(e.payload,'$.locator.section'),'') || ' ' ||
         COALESCE(json_extract(e.payload,'$.locator.sheet'),'') || ' ' ||
         COALESCE(json_extract(e.payload,'$.locator.page'),'') || ' ' ||
         COALESCE(json_extract(e.payload,'$.locator.paragraph'),'')
       ),
       COALESCE(json_extract(e.payload,'$.raw_text'),'')
FROM evidence e
JOIN documents d ON d.id=e.document_id
WHERE json_valid(e.payload)
  AND NOT EXISTS(SELECT 1 FROM evidence_search search WHERE search.rowid=e.rowid);

CREATE TRIGGER IF NOT EXISTS evidence_search_insert
AFTER INSERT ON evidence
WHEN json_valid(NEW.payload)
BEGIN
 INSERT INTO evidence_search(rowid,file_name,locator,raw_text)
 SELECT NEW.rowid,d.name,
        TRIM(
          COALESCE(json_extract(NEW.payload,'$.locator.section'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.sheet'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.page'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.paragraph'),'')
        ),
        COALESCE(json_extract(NEW.payload,'$.raw_text'),'')
 FROM documents d WHERE d.id=NEW.document_id;
END;

CREATE TRIGGER IF NOT EXISTS evidence_search_delete
AFTER DELETE ON evidence
BEGIN
 DELETE FROM evidence_search WHERE rowid=OLD.rowid;
END;

CREATE TRIGGER IF NOT EXISTS evidence_search_update
AFTER UPDATE OF payload,run_id,project_id,document_id ON evidence
BEGIN
 DELETE FROM evidence_search WHERE rowid=OLD.rowid;
 INSERT INTO evidence_search(rowid,file_name,locator,raw_text)
 SELECT NEW.rowid,d.name,
        TRIM(
          COALESCE(json_extract(NEW.payload,'$.locator.section'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.sheet'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.page'),'') || ' ' ||
          COALESCE(json_extract(NEW.payload,'$.locator.paragraph'),'')
        ),
        COALESCE(json_extract(NEW.payload,'$.raw_text'),'')
 FROM documents d WHERE d.id=NEW.document_id AND json_valid(NEW.payload);
END;

INSERT OR IGNORE INTO schema_migrations VALUES(7,datetime('now'));
