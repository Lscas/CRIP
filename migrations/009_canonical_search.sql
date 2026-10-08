-- Source-text-only full-text projection. Visual narration is deliberately excluded.
CREATE VIRTUAL TABLE IF NOT EXISTS content_search USING fts5(
 node_type,
 path,
 raw_text,
 tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO content_search(rowid,node_type,path,raw_text)
SELECT n.rowid,n.node_type,n.path,n.raw_text
FROM content_nodes n
WHERE n.is_source_text=1 AND n.raw_text!=''
  AND NOT EXISTS(SELECT 1 FROM content_search search WHERE search.rowid=n.rowid);

CREATE TRIGGER IF NOT EXISTS content_search_insert
AFTER INSERT ON content_nodes
WHEN NEW.is_source_text=1 AND NEW.raw_text!=''
BEGIN
 INSERT INTO content_search(rowid,node_type,path,raw_text)
 VALUES(NEW.rowid,NEW.node_type,NEW.path,NEW.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS content_search_delete
AFTER DELETE ON content_nodes
BEGIN
 DELETE FROM content_search WHERE rowid=OLD.rowid;
END;

CREATE TRIGGER IF NOT EXISTS content_search_update
AFTER UPDATE OF node_type,path,raw_text,is_source_text ON content_nodes
BEGIN
 DELETE FROM content_search WHERE rowid=OLD.rowid;
 INSERT INTO content_search(rowid,node_type,path,raw_text)
 SELECT NEW.rowid,NEW.node_type,NEW.path,NEW.raw_text
 WHERE NEW.is_source_text=1 AND NEW.raw_text!='';
END;

INSERT OR IGNORE INTO schema_migrations VALUES(9,datetime('now'));
