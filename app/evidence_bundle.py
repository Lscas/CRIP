"""QA V2 effective-source selection and bounded Canonical evidence bundles."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass,replace
from datetime import date
from pathlib import PurePath

from app.canonical import normalize_identifier
from app.db import Database,DomainError

MAX_BUNDLE_BYTES=42_000
MAX_BLOCK_BYTES=12_000
MAX_SEARCH_ROWS=96
RETRIEVAL_VERSION='qa-v2-bundle-1'

_DELTA_KIND=re.compile(r'(?i)\b(?:ADDENDUM|ASI|RFI|PCD|BULLETIN)\b')
_DATE_SUFFIX=re.compile(r'(?i)(?:[\s_.-]+(?:ISSUED|DATED)?[\s_.-]*)\d{4}[._-]\d{2}[._-]\d{2}$')
_REV_SUFFIX=re.compile(r'(?i)(?:[\s_.-]+|\()REV(?:ISION)?[\s_.-]*[A-Z0-9]+\)?$')
_TABLE_PATH=re.compile(r'^(.*?\bTable\s+\d+)(?:\s*>.*)?$',re.IGNORECASE)
_STOP_WORDS={
    'a','an','and','are','as','at','be','by','does','for','from','how','in','is','it','of','on',
    'or','the','to','what','when','where','which','who','with','please','tell','show','require',
}


@dataclass(frozen=True)
class EffectiveDocument:
    document_id:str
    name:str
    lineage_key:str
    source_kind:str
    issue_date:str|None
    revision_label:str|None
    selected:bool=True
    reason:str='ONLY_VERSION'

    def public(self)->dict:
        return {name:getattr(self,name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class EffectiveSourceSet:
    documents:tuple[EffectiveDocument,...]
    conflicts:tuple[str,...]

    @property
    def document_ids(self)->tuple[str,...]:
        return tuple(item.document_id for item in self.documents if item.selected)

    def public(self)->dict:
        return {
            'policy':'EXPLICIT_ISSUE_DATE_THEN_REVISION_KEEP_AMBIGUOUS',
            'active_document_ids':list(self.document_ids),
            'documents':[item.public() for item in self.documents],
            'conflicts':list(self.conflicts),
        }


@dataclass(frozen=True)
class EvidenceCitation:
    evidence_id:str
    document_id:str
    file_name:str
    page_number:int|None
    path:str
    quote:str
    bbox:list[float]|None
    coordinate_system:str|None
    extraction_method:str|None

    def public(self)->dict:
        return {name:getattr(self,name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class EvidenceBlock:
    block_id:str
    document_id:str
    file_name:str
    page_number:int|None
    path:str
    text:str
    citations:tuple[EvidenceCitation,...]
    score:int

    def public(self)->dict:
        return {
            'block_id':self.block_id,'document_id':self.document_id,'file_name':self.file_name,
            'page_number':self.page_number,'path':self.path,'text':self.text,
            'citations':[item.public() for item in self.citations],'score':self.score,
        }


@dataclass(frozen=True)
class EvidenceBundle:
    run_id:str
    question:str
    source_set:EffectiveSourceSet
    blocks:tuple[EvidenceBlock,...]
    missing:tuple[str,...]
    byte_count:int

    def public(self)->dict:
        return {
            'retrieval_version':RETRIEVAL_VERSION,'run_id':self.run_id,'question':self.question,
            'effective_sources':self.source_set.public(),
            'blocks':[item.public() for item in self.blocks],
            'missing':list(self.missing),'byte_count':self.byte_count,
        }


def _lineage(name:str)->str:
    value=PurePath(name).stem.upper().strip()
    previous=None
    while value and value!=previous:
        previous=value
        value=_DATE_SUFFIX.sub('',value).strip(' ._-')
        value=_REV_SUFFIX.sub('',value).strip(' ._-')
    return re.sub(r'[\s_.-]+',' ',value)


def _single(values:set[str])->str|None:
    return next(iter(values)) if len(values)==1 else None


def _date_value(value:str|None)->date|None:
    if not value or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return None
    try:return date.fromisoformat(value)
    except ValueError:return None


def _ordered_revisions(values:list[str])->list[int]|None:
    clean=[re.sub(r'(?i)^REV(?:ISION)?\s*','',value.strip()) for value in values]
    if all(re.fullmatch(r'\d+',value) for value in clean):return [int(value) for value in clean]
    if all(re.fullmatch(r'[A-Z]',value.upper()) for value in clean):return [ord(value.upper()) for value in clean]
    matches=[re.fullmatch(r'([A-Z._-]*?)(\d+)',value.upper()) for value in clean]
    if all(matches) and len({match.group(1) for match in matches})==1:
        return [int(match.group(2)) for match in matches]
    return None


def _resolve_group(items:list[EffectiveDocument])->tuple[list[EffectiveDocument],str|None]:
    if len(items)==1:
        reason='DELTA_OVERLAY' if items[0].source_kind=='DELTA' else 'ONLY_VERSION'
        return [replace(items[0],reason=reason)],None
    label=f'{items[0].source_kind}:{items[0].lineage_key}'
    if any(item.issue_date is None and item.revision_label is None for item in items):
        return [replace(item,reason='KEPT_AMBIGUOUS') for item in items],f'{label}: missing explicit version metadata'
    dates=[_date_value(item.issue_date) for item in items]
    candidates=items
    selected_reason='NEWEST_EXPLICIT_REVISION'
    if any(item.issue_date is not None for item in items) and not all(value is not None for value in dates):
        return [replace(item,reason='KEPT_AMBIGUOUS') for item in items],f'{label}: incomplete or invalid issue dates'
    if all(value is not None for value in dates):
        newest=max(dates)
        candidates=[item for item,value in zip(items,dates) if value==newest]
        selected_reason='NEWEST_EXPLICIT_ISSUE_DATE'
    if len(candidates)>1:
        labels=[item.revision_label for item in candidates]
        if any(value is None for value in labels):
            return [replace(item,reason='KEPT_AMBIGUOUS') for item in items],f'{label}: tied issue date without comparable revisions'
        ordered=_ordered_revisions([value for value in labels if value is not None])
        if ordered is None:
            return [replace(item,reason='KEPT_AMBIGUOUS') for item in items],f'{label}: revision labels are not safely comparable'
        newest=max(ordered)
        candidates=[item for item,value in zip(candidates,ordered) if value==newest]
        selected_reason=('NEWEST_EXPLICIT_REVISION' if not all(value is not None for value in dates)
                         else 'NEWEST_EXPLICIT_ISSUE_DATE_AND_REVISION')
    if len(candidates)!=1:
        return [replace(item,reason='KEPT_AMBIGUOUS') for item in items],f'{label}: duplicate newest versions'
    selected=candidates[0]
    reason_for_old=('SUPERSEDED_BY_EXPLICIT_ISSUE_DATE' if selected_reason.startswith('NEWEST_EXPLICIT_ISSUE_DATE')
                    else 'SUPERSEDED_BY_EXPLICIT_REVISION')
    return [replace(item,selected=item.document_id==selected.document_id,
                    reason=selected_reason if item.document_id==selected.document_id else reason_for_old)
            for item in items],None


def effective_source_set(db:Database,run:dict)->EffectiveSourceSet:
    build=db.one('SELECT state,project_id FROM canonical_builds WHERE run_id=?',(run['id'],),required=False)
    if not build or build['state']!='READY':
        raise DomainError('QA V2 requires a ready Canonical document graph.',409)
    if build['project_id']!=run['project_id']:
        raise DomainError('QA V2 Canonical project scope mismatch.',409)
    raw_ids=run.get('document_ids')
    document_ids=json.loads(raw_ids) if isinstance(raw_ids,str) else list(raw_ids or [])
    if not document_ids:return EffectiveSourceSet((),('The analysis run contains no documents.',))
    placeholders=','.join('?' for _ in document_ids)
    rows=db.all(f'''SELECT id,name FROM documents
                    WHERE project_id=? AND id IN ({placeholders}) ORDER BY name,id''',
                (run['project_id'],*document_ids))
    if len(rows)!=len(set(document_ids)):
        raise DomainError('QA V2 run documents do not match the project scope.',409)
    metadata=db.all(f'''SELECT document_id,metadata FROM content_nodes
                        WHERE run_id=? AND document_id IN ({placeholders})
                          AND source_storage_id IS NOT NULL AND is_source_text=1''',
                    (run['id'],*document_ids))
    dates={document_id:set() for document_id in document_ids}
    revisions={document_id:set() for document_id in document_ids}
    for row in metadata:
        try:value=json.loads(row['metadata'])
        except (TypeError,ValueError,json.JSONDecodeError):continue
        issued=value.get('internal_revision_date');revision=value.get('revision_label')
        if isinstance(issued,str) and issued.strip():dates[row['document_id']].add(issued.strip())
        if isinstance(revision,str) and revision.strip():revisions[row['document_id']].add(revision.strip())
    documents=[];conflicts=[]
    for row in rows:
        document_id=row['id'];name=row['name']
        if len(dates[document_id])>1 or len(revisions[document_id])>1:
            conflicts.append(f'{name}: conflicting version metadata inside the document')
        documents.append(EffectiveDocument(
            document_id,name,_lineage(name),'DELTA' if _DELTA_KIND.search(PurePath(name).stem) else 'BASE',
            _single(dates[document_id]),_single(revisions[document_id])))
    resolved=[]
    groups={}
    for item in documents:groups.setdefault((item.source_kind,item.lineage_key),[]).append(item)
    for items in groups.values():
        if any(len(dates[item.document_id])>1 or len(revisions[item.document_id])>1 for item in items):
            resolved.extend(replace(item,reason='KEPT_AMBIGUOUS') for item in items)
            continue
        selected,conflict=_resolve_group(items);resolved.extend(selected)
        if conflict:conflicts.append(conflict)
    return EffectiveSourceSet(tuple(sorted(resolved,key=lambda item:(item.name,item.document_id))),
                              tuple(conflicts))


def _question_identifiers(question:str)->list[tuple[str,str]]:
    patterns=(
        ('SPEC_SECTION',r'(?i)\b(?:SPECIFICATION\s+SECTION|SPEC\s+SECTION|SECTION)\s*[:#]?\s*(\d{5}|\d{2}(?:[ .-]+\d{2}){2})'),
        ('PARAGRAPH',r'(?i)\b(?:PARAGRAPH|PARA\.?|CLAUSE|ARTICLE)\s*[:#]?\s*(\d+(?:\.\d+)+)'),
        ('SHEET',r'(?i)\b(?:SHEET|DRAWING|DWG\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9._/-]{1,31})'),
        ('RFI',r'(?i)\bRFI\s*(?:NO\.?\s*)?[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{0,31})'),
        ('SUBMITTAL',r'(?i)\bSUBMITTAL\s*(?:NO\.?\s*)?[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{0,31})'),
    )
    found=[]
    for kind,pattern in patterns:
        for match in re.finditer(pattern,question):
            value=normalize_identifier(kind,match.group(1))
            pair=(kind,value)
            if value and pair not in found:found.append(pair)
    return found


def _terms(question:str)->list[str]:
    found=[]
    for value in re.findall(r'[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*|[\u3400-\u9fff]{2,}',question):
        normalized=value.casefold().strip('._/-')
        if len(normalized)<2 or normalized in _STOP_WORDS or normalized in found:continue
        found.append(normalized)
    return found[:24]


def _candidate_rows(db:Database,run:dict,document_ids:tuple[str,...],question:str)->dict[str,tuple[dict,int]]:
    if not document_ids:return {}
    placeholders=','.join('?' for _ in document_ids);candidates={}
    identifiers=_question_identifiers(question);identifier_sets=[];identifier_rows={}
    for kind,value in identifiers:
        rows=db.all(f'''SELECT n.*,d.name AS file_name FROM content_identifiers i
                        JOIN content_nodes scope ON scope.id=i.node_id
                        JOIN content_nodes n ON n.run_id=i.run_id AND n.document_id=i.document_id
                          AND n.is_source_text=1
                          AND (n.path=scope.path OR n.path LIKE scope.path||' > %')
                        JOIN documents d ON d.id=n.document_id
                        WHERE i.run_id=? AND i.identifier_type=? AND i.normalized_value=?
                          AND i.is_source_text=1 AND n.document_id IN ({placeholders})''',
                    (run['id'],kind,value,*document_ids))
        current={row['id'] for row in rows};identifier_sets.append(current)
        for row in rows:identifier_rows[row['id']]=row
    if identifier_sets:
        for node_id in set.intersection(*identifier_sets):candidates[node_id]=(identifier_rows[node_id],200)
    terms=_terms(question)
    if terms:
        if db.canonical_search_available:
            query=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
            rows=db.all(f'''SELECT n.*,d.name AS file_name FROM content_search
                            JOIN content_nodes n ON n.rowid=content_search.rowid
                            JOIN documents d ON d.id=n.document_id
                            WHERE content_search MATCH ? AND n.run_id=?
                              AND n.document_id IN ({placeholders})
                            ORDER BY bm25(content_search),n.ordinal
                            LIMIT {MAX_SEARCH_ROWS}''',(query,run['id'],*document_ids))
        else:
            conditions=' OR '.join('n.normalized_text LIKE ?' for _ in terms)
            rows=db.all(f'''SELECT n.*,d.name AS file_name FROM content_nodes n
                            JOIN documents d ON d.id=n.document_id
                            WHERE n.run_id=? AND n.is_source_text=1
                              AND n.document_id IN ({placeholders}) AND ({conditions})
                            ORDER BY n.ordinal LIMIT {MAX_SEARCH_ROWS}''',
                        (run['id'],*document_ids,*[f'%{term}%' for term in terms]))
        for row in rows:
            haystack=(row['normalized_text']+' '+row['path']).casefold()
            score=80+min(20,sum(term in haystack for term in terms)*4)
            previous=candidates.get(row['id'])
            if previous is None or score>previous[1]:candidates[row['id']]=(row,score)
    return candidates


def _clip_bytes(value:str,limit:int)->str:
    raw=value.encode('utf-8')
    if len(raw)<=limit:return value
    return raw[:max(0,limit-3)].decode('utf-8','ignore')+'...'


def _root_path(path:str)->tuple[str,bool]:
    table=_TABLE_PATH.match(path)
    return (table.group(1),True) if table else (path,False)


def _block(db:Database,run_id:str,document_id:str,file_name:str,root_path:str,is_table:bool,
           candidate_ids:set[str],score:int)->EvidenceBlock|None:
    suffix=" OR path LIKE ?" if is_table else ''
    args=(run_id,document_id,root_path,root_path+' > %') if is_table else (run_id,document_id,root_path)
    rows=db.all(f'''SELECT * FROM content_nodes WHERE run_id=? AND document_id=?
                    AND is_source_text=1 AND (path=?{suffix}) ORDER BY ordinal''',args)
    if not rows:return None
    selected=[];used=0
    priority=sorted(rows,key=lambda row:(0 if row['id'] in candidate_ids else 1,
                                         min(abs(row['ordinal']-candidate['ordinal'])
                                             for candidate in rows if candidate['id'] in candidate_ids),
                                         row['ordinal']))
    if is_table and rows[0] not in priority[:len(candidate_ids)]:
        priority.insert(len(candidate_ids),priority.pop(priority.index(rows[0])))
    for row in priority:
        piece=f"[{row['path']}]\n{row['raw_text']}"
        size=len(piece.encode('utf-8'))+(2 if selected else 0)
        if selected and used+size>MAX_BLOCK_BYTES:continue
        if not selected and size>MAX_BLOCK_BYTES:piece=_clip_bytes(piece,MAX_BLOCK_BYTES);size=len(piece.encode('utf-8'))
        selected.append((row,piece));used+=size
    selected.sort(key=lambda pair:pair[0]['ordinal'])
    text='\n\n'.join(piece for _,piece in selected)
    citations=[]
    for row,_ in selected:
        bbox=([row['min_x'],row['min_y'],row['max_x'],row['max_y']]
              if row['min_x'] is not None else None)
        citations.append(EvidenceCitation(
            row['source_evidence_id'],document_id,file_name,row['page_number'],row['path'],
            row['raw_text'],bbox,row['coordinate_system'],row['extraction_method']))
    pages={row['page_number'] for row,_ in selected}
    return EvidenceBlock(f'{document_id}:{root_path}',document_id,file_name,
                         next(iter(pages)) if len(pages)==1 else None,root_path,text,tuple(citations),score)


def build_evidence_bundle(db:Database,run:dict,question:str)->EvidenceBundle:
    source_set=effective_source_set(db,run)
    candidates=_candidate_rows(db,run,source_set.document_ids,question)
    grouped={}
    for node_id,(row,score) in candidates.items():
        root_path,is_table=_root_path(row['path']);key=(row['document_id'],root_path,is_table)
        value=grouped.setdefault(key,{'file_name':row['file_name'],'ids':set(),'score':0})
        value['ids'].add(node_id);value['score']=max(value['score'],score)
    blocks=[];used=0
    for (document_id,root_path,is_table),value in sorted(
            grouped.items(),key=lambda item:(-item[1]['score'],item[0][1],item[0][0])):
        block=_block(db,run['id'],document_id,value['file_name'],root_path,is_table,
                     value['ids'],value['score'])
        if block is None:continue
        size=len(block.text.encode('utf-8'))
        if used+size>MAX_BUNDLE_BYTES:continue
        blocks.append(block);used+=size
    missing=() if blocks else ('NO_MATCHING_SOURCE_TEXT',)
    return EvidenceBundle(run['id'],question,source_set,tuple(blocks),missing,used)
