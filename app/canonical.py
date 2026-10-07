"""Deterministic local projection from immutable evidence to a canonical document graph."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.db import Database,DomainError,dumps,now

BUILDER_VERSION='canonical-graph-2'
_DASHES=str.maketrans({'\u2010':'-','\u2011':'-','\u2012':'-','\u2013':'-','\u2014':'-','\u2212':'-'})
_SPEC_SECTION=re.compile(
    r'(?i)^SECTION\s+(\d{5}|\d{2}(?:[ .-]+\d{2}){2})(?:\b|$)')
_TYPED_SECTION=re.compile(r'(?i)^(RFI|SUBMITTAL)\s+(?:NO\.?\s*)?([^>:\n]{1,80})$')
_TABLE_ROW=re.compile(r'(?i)^TABLE\s+(\d+)\s+ROW\s+(\d+)$')


def _hash(*parts: object) -> str:
    return hashlib.sha256(dumps(parts).encode('utf-8')).hexdigest()


def _id(prefix: str,*parts: object) -> str:
    return prefix+'-'+_hash(*parts)[:32]


def _clean(value: object) -> str:
    return ' '.join(str(value or '').translate(_DASHES).split())


def normalize_identifier(kind: str,value: object) -> str:
    text=_clean(value)
    if kind=='SHEET':text=re.sub(r'(?i)^SHEET\s+','',text)
    elif kind=='PARAGRAPH':text=re.sub(r'(?i)^(?:PARAGRAPH|PARA\.?)\s+','',text)
    elif kind in {'RFI','SUBMITTAL'}:text=re.sub(rf'(?i)^{kind}\s+(?:NO\.?\s*)?','',text)
    elif kind=='SPEC_SECTION':text=re.sub(r'(?i)^SECTION\s+','',text)
    return text.upper()


def _bbox(locator: dict) -> tuple[float,float,float,float] | None:
    value=locator.get('bbox')
    if not isinstance(value,list) or len(value)!=4:return None
    if any(isinstance(item,bool) or not isinstance(item,(int,float)) for item in value):return None
    x0,y0,x1,y1=map(float,value)
    if x0>x1 or y0>y1:return None
    return x0,x1,y0,y1


@dataclass(frozen=True)
class BuildCounts:
    source_evidence_count:int
    node_count:int
    edge_count:int
    identifier_count:int
    span_count:int

    def public(self)->dict:
        return {name:getattr(self,name) for name in self.__dataclass_fields__}


class CanonicalGraph:
    def __init__(self,db: Database):self.db=db

    def rebuild_run(self,run: dict | str) -> dict:
        if isinstance(run,str):
            run=self.db.one('SELECT * FROM runs WHERE id=?',(run,))
        run_id=run['id'];project_id=run['project_id'];result=[]
        document_ids=json.loads(run['document_ids']) if isinstance(run.get('document_ids'),str) else run['document_ids']
        evidence=self.db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid',(run_id,))
        fingerprint=_hash(run.get('snapshot_id'),[(row['id'],_hash(row['payload'])) for row in evidence])
        with self.db.connect(True) as connection:
            connection.execute('DELETE FROM content_nodes WHERE run_id=?',(run_id,))
            connection.execute('''INSERT INTO canonical_builds
                (run_id,project_id,state,source_fingerprint,builder_version,detail,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET state=excluded.state,source_evidence_count=0,
                 node_count=0,edge_count=0,identifier_count=0,span_count=0,
                 source_fingerprint=excluded.source_fingerprint,builder_version=excluded.builder_version,
                 detail=excluded.detail,updated_at=excluded.updated_at''',
                (run_id,project_id,'BUILDING',fingerprint,BUILDER_VERSION,'{}',now()))
        try:
            for document_id in document_ids:
                if self.db.one('SELECT 1 FROM document_results WHERE run_id=? AND document_id=?',
                               (run_id,document_id),False):
                    result.append(self.rebuild_document(run,document_id))
            validation=self.validate_run(run_id)
            if not validation['ok']:raise DomainError('Canonical graph integrity validation failed')
        except Exception as exc:
            self.db.execute("UPDATE canonical_builds SET state='FAILED',detail=?,updated_at=? WHERE run_id=?",
                            (dumps({'error_class':type(exc).__name__}),now(),run_id))
            raise
        counts=validation['counts']
        self.db.execute('''UPDATE canonical_builds SET state='READY',source_evidence_count=?,
            node_count=?,edge_count=?,identifier_count=?,span_count=?,detail=?,updated_at=? WHERE run_id=?''',
            (counts['source_evidence_count'],counts['node_count'],counts['edge_count'],
             counts['identifier_count'],counts['span_count'],
             dumps({'document_count':len(result)}),now(),run_id))
        return {'run_id':run_id,'documents':result,'validation':validation}

    def rebuild_document(self,run: dict,document_id: str) -> dict:
        run_id=run['id'];project_id=run['project_id']
        document=self.db.one('SELECT id,project_id,name,sha256 FROM documents WHERE id=?',(document_id,))
        if document['project_id']!=project_id:raise DomainError('Canonical document project mismatch')
        rows=self.db.all('''SELECT e.id AS storage_id,e.rowid AS storage_rowid,e.payload
                            FROM evidence e WHERE e.run_id=? AND e.document_id=? ORDER BY e.rowid''',
                         (run_id,document_id))
        payloads=[]
        for row in rows:
            try:value=json.loads(row['payload'])
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                raise DomainError('Canonical source evidence is malformed') from exc
            if not isinstance(value,dict) or value.get('project_id')!=project_id or value.get('document_id')!=document_id:
                raise DomainError('Canonical source evidence scope mismatch')
            locator=value.get('locator')
            if not isinstance(locator,dict):raise DomainError('Canonical source locator is malformed')
            payloads.append((row,value))
        fingerprint=_hash(run.get('snapshot_id'),document['sha256'],
                          [(row['storage_id'],_hash(value)) for row,value in payloads])
        with self.db.connect(True) as connection:
            connection.execute('DELETE FROM content_nodes WHERE run_id=? AND document_id=?',(run_id,document_id))
            nodes={};ordinal=0

            def add_node(node_type:str,parent_id:str|None,path:str,*,page_number:int|None=None,
                         raw_text:str='',source:tuple[dict,dict]|None=None,locator:dict|None=None,
                         metadata_extra:dict|None=None)->str:
                nonlocal ordinal
                source_row,source_value=source if source else (None,None)
                source_id=source_row['storage_id'] if source_row else None
                stable=(run_id,document_id,node_type,parent_id,path,source_id or '')
                node_id=_id('NODE',*stable)
                if node_id in nodes:return node_id
                content_basis=(source_value or {}).get('content_basis')
                method=(source_value or {}).get('extraction_method')
                source_text=bool(source_value is not None and content_basis!='MODEL_VISION_OUTPUT' and method!='VISION')
                box=_bbox(locator or {})
                min_x=max_x=min_y=max_y=None
                if box:min_x,max_x,min_y,max_y=box
                metadata={}
                if source_value is not None:
                    metadata={'text_map':source_value.get('text_map') or [],
                              'image_crop_uri':source_value.get('image_crop_uri'),
                              'revision_label':source_value.get('revision_label'),
                              'internal_revision_date':source_value.get('internal_revision_date')}
                if metadata_extra:metadata.update(metadata_extra)
                connection.execute('''INSERT INTO content_nodes
                    (id,run_id,project_id,document_id,parent_id,node_type,ordinal,page_number,path,
                     raw_text,normalized_text,source_storage_id,source_evidence_id,is_source_text,
                     extraction_method,confidence,min_x,max_x,min_y,max_y,coordinate_system,
                     content_hash,parser_version,metadata)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (node_id,run_id,project_id,document_id,parent_id,node_type,ordinal,page_number,path,
                     raw_text,_clean(raw_text),source_id,(source_value or {}).get('evidence_id'),int(source_text),
                     method,(source_value or {}).get('confidence'),min_x,max_x,min_y,max_y,
                     (locator or {}).get('coordinate_system'),_hash(raw_text,path,node_type),
                     (source_value or {}).get('parser_version') or BUILDER_VERSION,dumps(metadata)))
                nodes[node_id]=True;ordinal+=1
                if parent_id:
                    edge_id=_id('EDGE',run_id,parent_id,node_id,'PARENT_OF','PARSER_LOCATOR')
                    connection.execute('''INSERT OR IGNORE INTO content_edges
                        (id,run_id,project_id,source_node_id,target_node_id,relation_type,derivation,explicit,metadata)
                        VALUES(?,?,?,?,?,?,?,?,?)''',
                        (edge_id,run_id,project_id,parent_id,node_id,'PARENT_OF','PARSER_LOCATOR',0,'{}'))
                return node_id

            root=add_node('DOCUMENT',None,document['name'])
            scopes={('DOCUMENT',None,''):root}
            for row,value in payloads:
                locator=value['locator'];page=locator.get('page_number')
                page=page if type(page) is int and page>=1 else None
                page_path=f'Page {page}' if page else 'Document'
                page_key=('PAGE',page,'')
                parent=scopes.get(page_key)
                if not parent:
                    parent=add_node('PAGE',root,page_path,page_number=page)
                    scopes[page_key]=parent
                path_parts=[page_path]
                sheet=_clean(locator.get('sheet'))
                if sheet:
                    path_parts.append('Sheet '+sheet)
                    key=('SHEET',page,sheet)
                    sheet_node=scopes.get(key)
                    if not sheet_node:
                        sheet_node=add_node('SHEET',parent,' > '.join(path_parts),page_number=page)
                        scopes[key]=sheet_node
                    self._insert_identifier(connection,run_id,project_id,document_id,sheet_node,
                                            'SHEET',sheet,int(value.get('content_basis')!='MODEL_VISION_OUTPUT'
                                                              and value.get('extraction_method')!='VISION'),None)
                    parent=sheet_node
                section=_clean(locator.get('section'))
                if section:
                    accumulated=[]
                    for part in [item.strip() for item in section.split(' > ') if item.strip()][:16]:
                        accumulated.append(part);path_parts.append(part)
                        key=('SECTION',page,sheet,' > '.join(accumulated))
                        section_node=scopes.get(key)
                        if not section_node:
                            section_node=add_node('SECTION',parent,' > '.join(path_parts),page_number=page)
                            scopes[key]=section_node
                        self._insert_section_identifier(connection,run_id,project_id,document_id,
                                                        section_node,part,int(value.get('content_basis')!='MODEL_VISION_OUTPUT'
                                                                              and value.get('extraction_method')!='VISION'))
                        parent=section_node
                paragraph=_clean(locator.get('paragraph'))
                table_row=_TABLE_ROW.fullmatch(paragraph)
                if paragraph:
                    if table_row:
                        table_number,row_number=map(int,table_row.groups())
                        path_parts.append(f'Table {table_number}')
                        table_key=('TABLE',page,sheet,section,table_number)
                        table_node=scopes.get(table_key)
                        if not table_node:
                            table_node=add_node('SECTION',parent,' > '.join(path_parts),page_number=page,
                                                metadata_extra={'structure_kind':'TABLE',
                                                                'table_number':table_number})
                            scopes[table_key]=table_node
                        parent=table_node;path_parts.append(f'Row {row_number}')
                        key=('TABLE_ROW',page,sheet,section,table_number,row_number)
                    else:
                        path_parts.append('Paragraph '+paragraph)
                        key=('PARAGRAPH',page,sheet,section,paragraph)
                    paragraph_node=scopes.get(key)
                    if not paragraph_node:
                        paragraph_node=add_node(
                            'PARAGRAPH',parent,' > '.join(path_parts),page_number=page,
                            metadata_extra=({'structure_kind':'TABLE_ROW','table_number':table_number,
                                             'row_number':row_number} if table_row else None))
                        scopes[key]=paragraph_node
                    if not table_row:
                        self._insert_identifier(connection,run_id,project_id,document_id,paragraph_node,
                                                'PARAGRAPH',paragraph,int(value.get('content_basis')!='MODEL_VISION_OUTPUT'
                                                                          and value.get('extraction_method')!='VISION'),None)
                    parent=paragraph_node
                raw_text=value.get('raw_text')
                if not isinstance(raw_text,str):raise DomainError('Canonical source text is malformed')
                node_type=('VISUAL_CONTEXT' if value.get('content_basis')=='MODEL_VISION_OUTPUT'
                           or value.get('extraction_method')=='VISION' else 'TEXT_BLOCK')
                node=add_node(node_type,parent,' > '.join(path_parts),page_number=page,
                              raw_text=raw_text,source=(row,value),locator=locator,
                              metadata_extra=({'structure_kind':'TABLE_ROW','table_number':table_number,
                                               'row_number':row_number,
                                               'cell_count':len(value.get('text_map') or [])}
                                              if table_row else None))
                span_id=_id('SPAN',run_id,node,row['storage_id'],0,len(raw_text))
                box=_bbox(locator);coords=(box if box else (None,None,None,None))
                connection.execute('''INSERT INTO content_spans
                    (id,node_id,source_storage_id,source_evidence_id,start_offset,end_offset,
                     min_x,max_x,min_y,max_y,coordinate_system,text_hash,metadata)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (span_id,node,row['storage_id'],value['evidence_id'],0,len(raw_text),*coords,
                     locator.get('coordinate_system'),_hash(raw_text),
                     dumps({'text_map':value.get('text_map') or []})))
            counts=self._counts(connection,run_id)
        return {'document_id':document_id,'source_fingerprint':fingerprint,**counts.public()}

    @staticmethod
    def _insert_identifier(connection,run_id,project_id,document_id,node_id,kind,raw_value,
                           is_source_text,source_span_id):
        normalized=normalize_identifier(kind,raw_value)
        if not normalized:return
        identifier_id=_id('IDENT',run_id,node_id,kind,normalized,'PARSER_LOCATOR')
        connection.execute('''INSERT INTO content_identifiers
            (id,run_id,project_id,document_id,node_id,identifier_type,raw_value,normalized_value,
             is_source_text,source_span_id,derivation) VALUES(?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(run_id,node_id,identifier_type,normalized_value,derivation)
             DO UPDATE SET is_source_text=MAX(content_identifiers.is_source_text,excluded.is_source_text)''',
            (identifier_id,run_id,project_id,document_id,node_id,kind,raw_value,normalized,
             is_source_text,source_span_id,'PARSER_LOCATOR'))

    @classmethod
    def _insert_section_identifier(cls,connection,run_id,project_id,document_id,node_id,value,
                                   is_source_text):
        spec=_SPEC_SECTION.match(value)
        if spec:
            cls._insert_identifier(connection,run_id,project_id,document_id,node_id,
                                   'SPEC_SECTION',re.sub(r'[.-]',' ',spec.group(1)),is_source_text,None)
        typed=_TYPED_SECTION.match(value)
        if typed:
            cls._insert_identifier(connection,run_id,project_id,document_id,node_id,
                                   typed.group(1).upper(),typed.group(2),is_source_text,None)

    @staticmethod
    def _counts(connection,run_id)->BuildCounts:
        def count(table):return connection.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id=?',(run_id,)).fetchone()[0]
        source=connection.execute('SELECT COUNT(*) FROM evidence WHERE run_id=?',(run_id,)).fetchone()[0]
        spans=connection.execute('''SELECT COUNT(*) FROM content_spans s JOIN content_nodes n ON n.id=s.node_id
                                    WHERE n.run_id=?''',(run_id,)).fetchone()[0]
        return BuildCounts(source,count('content_nodes'),count('content_edges'),
                           count('content_identifiers'),spans)

    def validate_run(self,run_id: str) -> dict:
        with self.db.connect() as connection:
            run=connection.execute('SELECT project_id FROM runs WHERE id=?',(run_id,)).fetchone()
            if not run:raise DomainError('Canonical run not found',404)
            checks={
                'cross_project_nodes':connection.execute(
                    'SELECT COUNT(*) FROM content_nodes WHERE run_id=? AND project_id!=?',
                    (run_id,run['project_id'])).fetchone()[0],
                'cross_scope_sources':connection.execute('''SELECT COUNT(*) FROM content_nodes node
                    JOIN evidence source ON source.id=node.source_storage_id
                    WHERE node.run_id=? AND (source.run_id!=node.run_id
                         OR source.project_id!=node.project_id OR source.document_id!=node.document_id)''',
                    (run_id,)).fetchone()[0],
                'cross_scope_spans':connection.execute('''SELECT COUNT(*) FROM content_spans span
                    JOIN content_nodes node ON node.id=span.node_id
                    JOIN evidence source ON source.id=span.source_storage_id
                    WHERE node.run_id=? AND (span.source_storage_id!=node.source_storage_id
                         OR source.run_id!=node.run_id OR source.project_id!=node.project_id
                         OR source.document_id!=node.document_id)''',(run_id,)).fetchone()[0],
                'cross_project_edges':connection.execute('''SELECT COUNT(*) FROM content_edges edge
                    JOIN content_nodes source ON source.id=edge.source_node_id
                    JOIN content_nodes target ON target.id=edge.target_node_id
                    WHERE edge.run_id=? AND (edge.project_id!=? OR source.run_id!=edge.run_id
                         OR target.run_id!=edge.run_id OR source.project_id!=edge.project_id
                         OR target.project_id!=edge.project_id)''',(run_id,run['project_id'])).fetchone()[0],
                'cross_project_identifiers':connection.execute('''SELECT COUNT(*) FROM content_identifiers item
                    JOIN content_nodes node ON node.id=item.node_id
                    WHERE item.run_id=? AND (item.project_id!=? OR node.run_id!=item.run_id
                         OR node.project_id!=item.project_id OR node.document_id!=item.document_id)''',
                    (run_id,run['project_id'])).fetchone()[0],
                'orphan_parents':connection.execute('''SELECT COUNT(*) FROM content_nodes child
                    LEFT JOIN content_nodes parent ON parent.id=child.parent_id
                    WHERE child.run_id=? AND child.parent_id IS NOT NULL AND parent.id IS NULL''',(run_id,)).fetchone()[0],
                'orphan_edges':connection.execute('''SELECT COUNT(*) FROM content_edges edge
                    LEFT JOIN content_nodes source ON source.id=edge.source_node_id
                    LEFT JOIN content_nodes target ON target.id=edge.target_node_id
                    WHERE edge.run_id=? AND (source.id IS NULL OR target.id IS NULL)''',(run_id,)).fetchone()[0],
                'invalid_spans':connection.execute('''SELECT COUNT(*) FROM content_spans span
                    JOIN content_nodes node ON node.id=span.node_id
                    WHERE node.run_id=? AND (span.end_offset>length(node.raw_text)
                         OR span.start_offset>span.end_offset)''',(run_id,)).fetchone()[0],
                'source_text_without_span':connection.execute('''SELECT COUNT(*) FROM content_nodes node
                    LEFT JOIN content_spans span ON span.node_id=node.id
                    WHERE node.run_id=? AND node.is_source_text=1 AND node.raw_text!='' AND span.id IS NULL''',
                    (run_id,)).fetchone()[0],
                'unprojected_evidence':connection.execute('''SELECT COUNT(*) FROM evidence source
                    LEFT JOIN content_nodes node ON node.run_id=source.run_id
                         AND node.source_storage_id=source.id
                    WHERE source.run_id=? AND node.id IS NULL''',(run_id,)).fetchone()[0]
            }
            if getattr(self.db,'canonical_search_available',False):
                searchable=connection.execute('''SELECT COUNT(*) FROM content_nodes
                    WHERE run_id=? AND is_source_text=1 AND raw_text!='' ''',(run_id,)).fetchone()[0]
                indexed=connection.execute('''SELECT COUNT(*) FROM content_search search
                    JOIN content_nodes node ON node.rowid=search.rowid WHERE node.run_id=?''',(run_id,)).fetchone()[0]
                checks['search_projection_mismatch']=abs(searchable-indexed)
            if getattr(self.db,'canonical_bounds_available',False):
                bounded=connection.execute('''SELECT COUNT(*) FROM content_nodes
                    WHERE run_id=? AND min_x IS NOT NULL''',(run_id,)).fetchone()[0]
                indexed=connection.execute('''SELECT COUNT(*) FROM content_bounds bounds
                    JOIN content_nodes node ON node.rowid=bounds.node_rowid WHERE node.run_id=?''',(run_id,)).fetchone()[0]
                checks['bounds_projection_mismatch']=abs(bounded-indexed)
            counts=self._counts(connection,run_id).public()
        failures={name:value for name,value in checks.items() if value}
        return {'ok':not failures,'checks':checks,'counts':counts,'failures':failures,
                'search_available':bool(getattr(self.db,'canonical_search_available',False)),
                'bounds_available':bool(getattr(self.db,'canonical_bounds_available',False))}
