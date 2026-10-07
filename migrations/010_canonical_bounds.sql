-- Page-local spatial projection for drawing, table and text-region retrieval.
CREATE VIRTUAL TABLE IF NOT EXISTS content_bounds USING rtree(
 node_rowid,
 min_x,max_x,
 min_y,max_y
);

INSERT INTO content_bounds(node_rowid,min_x,max_x,min_y,max_y)
SELECT n.rowid,n.min_x,n.max_x,n.min_y,n.max_y
FROM content_nodes n
WHERE n.min_x IS NOT NULL
  AND NOT EXISTS(SELECT 1 FROM content_bounds bounds WHERE bounds.node_rowid=n.rowid);

CREATE TRIGGER IF NOT EXISTS content_bounds_insert
AFTER INSERT ON content_nodes
WHEN NEW.min_x IS NOT NULL
BEGIN
 INSERT INTO content_bounds VALUES(NEW.rowid,NEW.min_x,NEW.max_x,NEW.min_y,NEW.max_y);
END;

CREATE TRIGGER IF NOT EXISTS content_bounds_delete
AFTER DELETE ON content_nodes
BEGIN
 DELETE FROM content_bounds WHERE node_rowid=OLD.rowid;
END;

CREATE TRIGGER IF NOT EXISTS content_bounds_update
AFTER UPDATE OF min_x,max_x,min_y,max_y ON content_nodes
BEGIN
 DELETE FROM content_bounds WHERE node_rowid=OLD.rowid;
 INSERT INTO content_bounds
 SELECT NEW.rowid,NEW.min_x,NEW.max_x,NEW.min_y,NEW.max_y
 WHERE NEW.min_x IS NOT NULL;
END;

INSERT OR IGNORE INTO schema_migrations VALUES(10,datetime('now'));
