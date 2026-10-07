-- Rebuildable canonical document graph. Source evidence remains immutable.
CREATE TABLE IF NOT EXISTS canonical_builds (
 run_id TEXT PRIMARY KEY REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 state TEXT NOT NULL CHECK(state IN ('BUILDING','READY','FAILED')),
 source_evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(source_evidence_count>=0),
 node_count INTEGER NOT NULL DEFAULT 0 CHECK(node_count>=0),
 edge_count INTEGER NOT NULL DEFAULT 0 CHECK(edge_count>=0),
 identifier_count INTEGER NOT NULL DEFAULT 0 CHECK(identifier_count>=0),
 span_count INTEGER NOT NULL DEFAULT 0 CHECK(span_count>=0),
 source_fingerprint TEXT NOT NULL,
 builder_version TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_nodes (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 document_id TEXT NOT NULL REFERENCES documents(id),
 parent_id TEXT REFERENCES content_nodes(id) ON DELETE CASCADE,
 node_type TEXT NOT NULL CHECK(node_type IN (
  'DOCUMENT','PAGE','SHEET','SECTION','PARAGRAPH','TEXT_BLOCK','VISUAL_CONTEXT'
 )),
 ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 page_number INTEGER CHECK(page_number IS NULL OR page_number>=1),
 path TEXT NOT NULL DEFAULT '',
 raw_text TEXT NOT NULL DEFAULT '',
 normalized_text TEXT NOT NULL DEFAULT '',
 source_storage_id TEXT REFERENCES evidence(id),
 source_evidence_id TEXT,
 is_source_text INTEGER NOT NULL CHECK(is_source_text IN (0,1)),
 extraction_method TEXT,
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 min_x REAL,
 max_x REAL,
 min_y REAL,
 max_y REAL,
 coordinate_system TEXT,
 content_hash TEXT NOT NULL,
 parser_version TEXT NOT NULL,
 metadata TEXT NOT NULL DEFAULT '{}',
 CHECK((min_x IS NULL AND max_x IS NULL AND min_y IS NULL AND max_y IS NULL)
    OR (min_x IS NOT NULL AND max_x IS NOT NULL AND min_y IS NOT NULL AND max_y IS NOT NULL
        AND min_x<=max_x AND min_y<=max_y)),
 UNIQUE(run_id,source_storage_id)
);
CREATE INDEX IF NOT EXISTS ix_content_nodes_run_document
 ON content_nodes(run_id,document_id,ordinal);
CREATE INDEX IF NOT EXISTS ix_content_nodes_parent
 ON content_nodes(parent_id,ordinal);
CREATE INDEX IF NOT EXISTS ix_content_nodes_scope
 ON content_nodes(run_id,node_type,page_number,path);

CREATE TABLE IF NOT EXISTS content_spans (
 id TEXT PRIMARY KEY,
 node_id TEXT NOT NULL REFERENCES content_nodes(id) ON DELETE CASCADE,
 source_storage_id TEXT NOT NULL REFERENCES evidence(id),
 source_evidence_id TEXT NOT NULL,
 start_offset INTEGER NOT NULL CHECK(start_offset>=0),
 end_offset INTEGER NOT NULL CHECK(end_offset>=start_offset),
 min_x REAL,
 max_x REAL,
 min_y REAL,
 max_y REAL,
 coordinate_system TEXT,
 text_hash TEXT NOT NULL,
 metadata TEXT NOT NULL DEFAULT '{}',
 UNIQUE(node_id,start_offset,end_offset)
);
CREATE INDEX IF NOT EXISTS ix_content_spans_source
 ON content_spans(source_storage_id,start_offset,end_offset);

CREATE TABLE IF NOT EXISTS content_edges (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 source_node_id TEXT NOT NULL REFERENCES content_nodes(id) ON DELETE CASCADE,
 target_node_id TEXT NOT NULL REFERENCES content_nodes(id) ON DELETE CASCADE,
 relation_type TEXT NOT NULL CHECK(relation_type IN (
  'PARENT_OF','CONTINUES_ON','REFERENCES','CALLOUT_TO','LOCATED_IN',
  'TABLE_HEADER_FOR','DEFINED_BY','EXCEPTION_TO','SAME_ITEM_AS','SUPERSEDES','EXTRACTED_FROM'
 )),
 source_span_id TEXT REFERENCES content_spans(id),
 derivation TEXT NOT NULL,
 explicit INTEGER NOT NULL CHECK(explicit IN (0,1)),
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 metadata TEXT NOT NULL DEFAULT '{}',
 UNIQUE(run_id,source_node_id,target_node_id,relation_type,derivation)
);
CREATE INDEX IF NOT EXISTS ix_content_edges_source
 ON content_edges(run_id,source_node_id,relation_type);
CREATE INDEX IF NOT EXISTS ix_content_edges_target
 ON content_edges(run_id,target_node_id,relation_type);

CREATE TABLE IF NOT EXISTS content_identifiers (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 document_id TEXT NOT NULL REFERENCES documents(id),
 node_id TEXT NOT NULL REFERENCES content_nodes(id) ON DELETE CASCADE,
 identifier_type TEXT NOT NULL CHECK(identifier_type IN (
  'SHEET','PARAGRAPH','SPEC_SECTION','RFI','SUBMITTAL','DETAIL','ARTICLE','CLAUSE'
 )),
 raw_value TEXT NOT NULL,
 normalized_value TEXT NOT NULL,
 is_source_text INTEGER NOT NULL CHECK(is_source_text IN (0,1)),
 source_span_id TEXT REFERENCES content_spans(id),
 derivation TEXT NOT NULL,
 UNIQUE(run_id,node_id,identifier_type,normalized_value,derivation)
);
CREATE INDEX IF NOT EXISTS ix_content_identifiers_lookup
 ON content_identifiers(run_id,identifier_type,normalized_value,is_source_text);

CREATE TABLE IF NOT EXISTS content_assertions (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 subject_node_id TEXT NOT NULL REFERENCES content_nodes(id) ON DELETE CASCADE,
 predicate TEXT NOT NULL,
 value_text TEXT,
 value_number TEXT,
 unit TEXT,
 qualifier TEXT,
 scope_node_id TEXT REFERENCES content_nodes(id),
 source_span_id TEXT NOT NULL REFERENCES content_spans(id),
 extraction_method TEXT NOT NULL,
 confidence REAL CHECK(confidence IS NULL OR (confidence>=0 AND confidence<=1)),
 review_status TEXT NOT NULL DEFAULT 'PENDING',
 metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_content_assertions_subject
 ON content_assertions(run_id,subject_node_id,predicate);

INSERT OR IGNORE INTO schema_migrations VALUES(8,datetime('now'));
