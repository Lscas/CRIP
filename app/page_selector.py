"""Transparent local page selection over immutable parser evidence.

This module intentionally does not use the Canonical graph, embeddings, model
output, extracted requirements or reviewer records. It ranks literal source
pages so an advanced model can receive a small, inspectable input slice.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePath

from app.db import Database,DomainError,dumps

LEGACY_SELECTOR_VERSION='literal-page-selector-3'
COMPACT_SELECTOR_VERSION='literal-page-selector-4'
SCOPED_SELECTOR_VERSION='literal-page-selector-5'
ROW_FOCUS_SELECTOR_VERSION='literal-page-selector-6'
SELECTOR_VERSION='literal-page-selector-7'
COMPLETE_SELECTOR_VERSION='literal-page-selector-8'
LAYOUT_BOUND_SELECTOR_VERSION='literal-page-selector-9'
COMPLETE_SELECTOR_VERSIONS=(COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION)
LAYOUT_BINDING_VERSION='source-layout-binding-1'
LAYOUT_NAVIGATION_VERSION='source-layout-navigation-1'
SELECTOR_PAGE_LIMITS={
    LEGACY_SELECTOR_VERSION:6,COMPACT_SELECTOR_VERSION:4,
    SCOPED_SELECTOR_VERSION:4,ROW_FOCUS_SELECTOR_VERSION:4,SELECTOR_VERSION:4,
    COMPLETE_SELECTOR_VERSION:4,LAYOUT_BOUND_SELECTOR_VERSION:4}
DEFAULT_MAX_BYTES=48_000
MAX_CANDIDATE_PAGES=256
MAX_LAYOUT_BYTES=6_000

_TOKEN=re.compile(r'[A-Za-z0-9]+(?:[._/#-][A-Za-z0-9]+)*|[\u3400-\u9fff]{2,}')
_EXPLICIT_QUERY_ID=re.compile(
    r'(?i)\b(?:'
    r'(?:SPECIFICATION\s+SECTION|SPEC\s+SECTION|SECTION)\s*[:#]?\s*'
    r'(?P<spec>\d{5}|\d{2}(?:[ .-]+\d{2}){2})|'
    r'(?:PARAGRAPH|PARA\.?|CLAUSE|ARTICLE)\s*[:#]?\s*'
    r'(?P<paragraph>\d+(?:\.\d+)+)|'
    r'(?:SHEET|DRAWING|DWG\.?)\s*[:#]?\s*'
    r'(?P<sheet>(?=[A-Z0-9._/-]{2,32}\b)(?=[A-Z0-9._/-]*\d)'
    r'[A-Z0-9][A-Z0-9._/-]{1,31})|'
    r'(?:RFI|SUBMITTAL)\s*(?:NO\.?\s*)?[:#-]?\s*'
    r'(?P<workflow>(?=[A-Z0-9._/-]{1,32}\b)(?=[A-Z0-9._/-]*\d)'
    r'[A-Z0-9][A-Z0-9._/-]{0,31}))')
_STOP={
    'a','an','and','are','as','at','be','by','does','for','from','how','in','is','it','of','on',
    'do','or','please','require','requires','show','shown','stated','tell','the','to','using',
    'what','when','where','which','who','with',
    '是多少','是什么','哪些','什么','是否','请问','显示','告诉','这个','项目',
}
_DRAWING_FILE=re.compile(r'(?i)(?:^|[\s_.-])(?:drawing|drawings|plan|plans)(?:[\s_.-]|$)')
_BUILDING_ENTITY=re.compile(
    r'(?i)\b(?:BUILDING|BLDG\.?)\s*(?:NO\.?\s*)?[#:\-]?\s*([A-Z0-9][A-Z0-9._/\-]{0,15})\b')
_COVER_FIELD_PATTERNS=(
    re.compile(r'(?i)\bproject\s+name\b'),
    re.compile(r'(?i)\b(?:doe\s+)?job\s+(?:number|no\.?)\b'),
    re.compile(r'(?i)\b(?:project\s+)?address\b'),
    re.compile(r'(?i)\bscope\s+of\s+work\b'),
)


def _clip_utf8(value:str,limit:int)->str:
    raw=value.encode('utf-8')
    if len(raw)<=limit:return value
    return raw[:max(0,limit)].decode('utf-8','ignore')


def _query_parts(question:str)->tuple[list[str],list[str]]:
    terms=[];identifiers=[];covered=[]
    for match in _EXPLICIT_QUERY_ID.finditer(question):
        value=' '.join(match.group(0).casefold().split())
        if value not in terms:terms.append(value)
        identifier=next((match.group(name) for name in ('spec','paragraph','sheet','workflow')
                         if match.group(name) is not None),None)
        if identifier:
            identifier=' '.join(identifier.casefold().split()).strip('._/#-')
            if identifier and identifier not in terms:terms.append(identifier)
            if identifier and identifier not in identifiers:identifiers.append(identifier)
        covered.append(match.span())
    for match in _TOKEN.finditer(question):
        if any(start<=match.start()<end for start,end in covered):continue
        value=match.group(0).casefold().strip('._/#-')
        if value.isdigit() and len(value)<3:continue
        candidates=[value]
        if re.fullmatch(r'[\u3400-\u9fff]{4,16}',value):
            candidates.extend(value[index:index+2] for index in range(len(value)-1))
        for candidate in candidates:
            if len(candidate)<2 or candidate in _STOP or candidate in terms:continue
            terms.append(candidate)
            if len(terms)>=24:return terms,identifiers
    return terms,identifiers


def _query_terms(question:str)->list[str]:
    return _query_parts(question)[0]


def _v6_query_terms(terms:list[str])->list[str]:
    """Add bounded spelling variants only; do not add semantic synonyms."""
    result=list(terms)
    for term in tuple(terms):
        if not re.fullmatch(r'[a-z]+(?:-[a-z]+)*',term):continue
        compound='-' in term
        for word in term.split('-'):
            variants=[]
            if word.endswith('ness') and len(word)>6:variants.append(word[:-4])
            elif word.endswith('ies') and len(word)>4:variants.append(word[:-3]+'y')
            elif word.endswith('s') and len(word)>4:variants.append(word[:-1])
            elif compound:variants.append(word+'s')
            if compound:variants.insert(0,word)
            for variant in variants:
                if len(variant)<3 or variant in _STOP or variant in result:continue
                result.append(variant)
                if len(result)>=24:return result
    return result


def _drawing_cover_intent(question:str)->bool:
    return sum(bool(pattern.search(question)) for pattern in _COVER_FIELD_PATTERNS)>=2


def _building_entities(value:str)->set[str]:
    entities=set()
    for match in _BUILDING_ENTITY.finditer(value):
        identifier=match.group(1).upper().rstrip('.')
        if len(identifier)==1 or any(char.isdigit() for char in identifier):
            entities.add(identifier)
    return entities


def _is_identifier(term:str)->bool:
    return bool(re.search(r'\d',term))


def _literal_count(value:str,term:str)->int:
    if re.search(r'[\u3400-\u9fff]',term):return value.count(term)
    return len(re.findall(r'(?<![A-Za-z0-9])'+re.escape(term)+r'(?![A-Za-z0-9])',value))


def _lineage(name:str)->str:
    value=PurePath(name).stem.casefold()
    value=re.sub(r'(?:[\s_.-]+|\()rev(?:ision)?[\s_.-]*[a-z0-9]+\)?$','',value)
    value=re.sub(r'[\s_.-]+(?:issued[\s_.-]*)?\d{4}[._-]\d{2}[._-]\d{2}$','',value)
    return re.sub(r'[\s_.-]+',' ',value).strip()


def _run_document_ids(run:dict)->list[str]:
    raw=run.get('document_ids') or []
    try:values=json.loads(raw) if isinstance(raw,str) else list(raw)
    except (TypeError,ValueError,json.JSONDecodeError) as exc:
        raise DomainError('The analysis run has an invalid document snapshot.',409) from exc
    if not all(isinstance(value,str) and value for value in values):
        raise DomainError('The analysis run has an invalid document snapshot.',409)
    return values


def _row_text(value:dict)->str:
    text=value.get('raw_text')
    return text if isinstance(text,str) else ''


def _layout_lines(value:dict,text:str)->str|None:
    """Re-expose native PDF word positions without changing or summarizing source text."""
    if value.get('extraction_method')!='TEXT_LAYER':return None
    mapping=value.get('text_map')
    if not isinstance(mapping,list) or len(mapping)<2:return None
    words=[]
    for item in mapping:
        if not isinstance(item,dict):continue
        start=item.get('start');end=item.get('end');box=item.get('bbox')
        if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(text)
                or not isinstance(box,list) or len(box)!=4
                or any(not isinstance(number,(int,float)) or isinstance(number,bool)
                       for number in box)):
            continue
        numbers=tuple(map(float,box))
        if (not all(math.isfinite(number) for number in numbers)
                or numbers[2]<numbers[0] or numbers[3]<numbers[1]):
            continue
        token=text[start:end]
        if token.strip():words.append((token,*numbers))
    if len(words)<2:return None
    lines=[]
    for word in sorted(words,key=lambda item:(item[2],item[1])):
        top=word[2];height=max(1.0,word[4]-top)
        if lines:
            previous=lines[-1]
            anchor=sum(item[2] for item in previous)/len(previous)
            if abs(top-anchor)<=max(2.0,min(5.0,height*.45)):
                previous.append(word);continue
        lines.append([word])
    rendered=[];used=0
    for line in lines:
        line.sort(key=lambda item:item[1]);parts=[];previous=None
        for word in line:
            if previous is not None:
                gap=word[1]-previous[3]
                height=max(previous[4]-previous[2],word[4]-word[2],1.0)
                parts.append(' || ' if gap>max(24.0,height*2.5) else ' ')
            parts.append(word[0]);previous=word
        visible=''.join(parts)
        width=len(visible.encode('utf-8'))+(1 if rendered else 0)
        if used+width>MAX_LAYOUT_BYTES:break
        rendered.append(visible);used+=width
    result='\n'.join(rendered)
    return result if ' || ' in result else None


def _valid_layout_member(item:object,text:str)->tuple[int,int,list[float]]|None:
    if not isinstance(item,dict):return None
    start=item.get('start');end=item.get('end');box=item.get('bbox')
    if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(text)
            or not isinstance(box,list) or len(box)!=4
            or any(not isinstance(number,(int,float)) or isinstance(number,bool) for number in box)):
        return None
    numbers=list(map(float,box))
    if not all(math.isfinite(number) for number in numbers) or numbers[2]<numbers[0] or numbers[3]<numbers[1]:
        return None
    return start,end,numbers


def _layout_geometry(words:list[dict],*,max_bytes:int|None):
    """Single geometry core; callers choose legacy strings or local provenance."""
    if len(words)<2:return None,None
    lines=[]
    for word in sorted(words,key=lambda item:(item['bbox'][1],item['bbox'][0])):
        top=word['bbox'][1];height=max(1.0,word['bbox'][3]-top)
        if lines:
            previous=lines[-1]
            anchor=sum(item['bbox'][1] for item in previous)/len(previous)
            if abs(top-anchor)<=max(2.0,min(5.0,height*.45)):
                previous.append(word);continue
        lines.append([word])
    segments=[]
    for line in lines:
        line.sort(key=lambda item:item['bbox'][0]);current=[]
        for word in line:
            if current:
                previous=current[-1]
                gap=word['bbox'][0]-previous['bbox'][2]
                height=max(previous['bbox'][3]-previous['bbox'][1],word['bbox'][3]-word['bbox'][1],1.0)
                if gap>max(24.0,height*2.5):
                    segments.append(current);current=[]
            current.append(word)
        if current:segments.append(current)
    if len(segments)<2:return None,None
    columns=[]
    for segment in sorted(segments,key=lambda line:(line[0]['bbox'][0],line[0]['bbox'][1])):
        if columns and segment[0]['bbox'][0]-columns[-1][-1][0]['bbox'][0]<=80.0:
            columns[-1].append(segment)
        else:columns.append([segment])
    if len(columns)<2:return None,None
    rendered=[];out=[];used=0
    for column_index,column in enumerate(columns):
        if column_index:
            if max_bytes is not None and used+3>max_bytes:break
            rendered.append('||');used+=3
        output=[]
        for line in sorted(column,key=lambda item:(item[0]['bbox'][1],item[0]['bbox'][0])):
            visible=' '.join(word['text'] for word in line)
            width=len(visible.encode('utf-8'))+(1 if rendered else 0)
            if max_bytes is not None and used+width>max_bytes:
                result='\n'.join(rendered)
                return (result,out+([output] if output else [])) if result else (None,None)
            rendered.append(visible);used+=width
            output.append(line)
        out.append(output)
    result='\n'.join(rendered)
    return (result,out) if any(line=='||' for line in rendered) else (None,None)


def _page_layout_columns(values:list[tuple[dict,str]],*,max_bytes:int|None=MAX_LAYOUT_BYTES)->str|None:
    """Legacy-tolerant wrapper; it intentionally requires no source identity."""
    words=[]
    for value,text in values:
        if value.get('extraction_method')!='TEXT_LAYER':continue
        mapping=value.get('text_map')
        if not isinstance(mapping,list):continue
        for item in mapping:
            valid=_valid_layout_member(item,text)
            if valid is None:continue
            start,end,bbox=valid;token=text[start:end]
            if token.strip():words.append({'text':token,'bbox':bbox})
    rendered,_=_layout_geometry(words,max_bytes=max_bytes)
    return rendered


class LayoutBindingError(ValueError):pass


def _ranges(length:int,spans:list[tuple[int,int]]):
    merged=[]
    for start,end in sorted(spans):
        if not merged or start>merged[-1][1]:merged.append([start,end])
        else:merged[-1][1]=max(merged[-1][1],end)
    gaps=[];at=0
    for start,end in merged:
        if at<start:gaps.append([at,start])
        at=end
    if at<length:gaps.append([at,length])
    return merged,gaps


def _binding_words(row:dict,input_index:int):
    if not isinstance(row,dict):raise LayoutBindingError('evidence member must be an object')
    evidence_id,document_id,text=row.get('evidence_id'),row.get('document_id'),row.get('raw_text')
    if not all(isinstance(value,str) and value for value in (evidence_id,document_id,text)):
        raise LayoutBindingError('evidence requires id, document id, and raw text')
    source={'input_index':input_index,'evidence_id':evidence_id,'document_id':document_id,
            'raw_text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    if row.get('extraction_method')!='TEXT_LAYER':
        source.update(binding_status='UNBOUND',binding_reason='NOT_TEXT_LAYER',mapped_ranges=[],unmapped_ranges=[[0,len(text)]])
        return [],source
    locator=row.get('locator') if isinstance(row.get('locator'),dict) else {}
    page=locator.get('page_number')
    if type(page) is not int or page<1:
        source.update(binding_status='UNBOUND',binding_reason='MISSING_OR_INVALID_PAGE',mapped_ranges=[],unmapped_ranges=[[0,len(text)]])
        return [],source
    source['page_number']=page;mapping=row.get('text_map')
    if not isinstance(mapping,list):
        source.update(binding_status='UNBOUND',binding_reason='MISSING_TEXT_MAP',mapped_ranges=[],unmapped_ranges=[[0,len(text)]])
        return [],source
    words=[];spans=[];invalid=0
    for ordinal,item in enumerate(mapping):
        valid=_valid_layout_member(item,text)
        if valid is None:invalid+=1;continue
        start,end,bbox=valid;spans.append((start,end));token=text[start:end]
        if token.strip():words.append({'text':token,'evidence_id':evidence_id,'document_id':document_id,
                                       'page_number':page,'start':start,'end':end,'bbox':bbox,
                                       '_input_index':input_index,'_ordinal':ordinal})
    mapped,unmapped=_ranges(len(text),spans)
    if words:source.update(binding_status='BOUND',mapped_ranges=mapped,unmapped_ranges=unmapped,invalid_text_map_members=invalid)
    else:source.update(binding_status='UNBOUND',binding_reason='INVALID_TEXT_MAP' if invalid else 'NO_NONBLANK_MAPPED_TOKEN',mapped_ranges=mapped,unmapped_ranges=unmapped,invalid_text_map_members=invalid)
    return words,source


def _provenance_layout(words:list[dict],*,max_bytes:int|None):
    rendered,columns=_layout_geometry(words,max_bytes=max_bytes)
    if rendered is None:return None
    output=[]
    for column in columns:
        lines=[]
        for segment in column:
            text=' '.join(word['text'] for word in segment)
            parts=[{'text':word['text'],'evidence_id':word['evidence_id']} for word in segment]
            spans=[{key:word[key] for key in ('evidence_id','document_id','page_number','start','end','bbox','text')} for word in segment]
            lines.append({'text':text,'evidence_ids':list(dict.fromkeys(part['evidence_id'] for part in parts)),
                          'parts':parts,'spans':spans})
        output.append(lines)
    return {'rendered_layout_lines':rendered,
            'model_navigation':[[{'text':line['text'],'evidence_ids':line['evidence_ids'],'parts':line['parts']} for line in column] for column in output],
            'columns':output}


def _stable(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def build_layout_binding(evidence_rows:list[dict],*,max_bytes:int|None=None):
    if not isinstance(evidence_rows,(list,tuple)):raise LayoutBindingError('selected evidence list required')
    sources=[];groups={};seen=set()
    for index,row in enumerate(evidence_rows,1):
        words,source=_binding_words(row,index)
        if source['evidence_id'] in seen:raise LayoutBindingError('duplicate evidence id in selected evidence')
        seen.add(source['evidence_id']);sources.append(source)
        if words:groups.setdefault((source['document_id'],source['page_number']),[]).extend(words)
    layouts=[]
    for (document_id,page),words in sorted(groups.items(),key=lambda item:min(word['_input_index'] for word in item[1])):
        layout=_provenance_layout(words,max_bytes=max_bytes)
        if layout:layouts.append({'document_id':document_id,'page_number':page,**layout})
    unbound=[{'evidence_id':source['evidence_id'],'binding_reason':source['binding_reason']}
             for source in sources if source['binding_status']=='UNBOUND']
    body={'binding_version':LAYOUT_BINDING_VERSION,'selection_status':'EMPTY' if not evidence_rows else 'BOUND_WITH_DIAGNOSTICS',
          'sources':sources,'unbound_sources':unbound,'page_layouts':layouts,
          'limitations':['Navigation binds observed text spans to source evidence; it does not prove object semantics or answer support.',
                         'Model navigation is compact; bbox, offsets and hashes remain local provenance.']}
    return {**body,'binding_sha256':hashlib.sha256(_stable(body).encode()).hexdigest()}


def verify_layout_binding(binding:dict,evidence_rows:list[dict],max_bytes:int|None=None):
    if not isinstance(binding,dict) or binding.get('binding_version')!=LAYOUT_BINDING_VERSION:
        raise LayoutBindingError('unsupported layout binding version')
    if binding!=build_layout_binding(evidence_rows,max_bytes=max_bytes):
        raise LayoutBindingError('layout binding does not exactly match selected source evidence')


def filter_layout_binding(binding:dict,evidence_rows:list[dict],kept_refs:list[str],max_bytes:int|None=None):
    """Verify the complete old binding, then rebuild from current retained rows."""
    verify_layout_binding(binding,evidence_rows,max_bytes=max_bytes)
    if not isinstance(kept_refs,list) or len(set(kept_refs))!=len(kept_refs):
        raise LayoutBindingError('kept evidence refs must be unique')
    indexed={f'E{index}':row for index,row in enumerate(evidence_rows,1)}
    if any(reference not in indexed for reference in kept_refs):
        raise LayoutBindingError('kept evidence ref is outside selected evidence')
    return build_layout_binding([indexed[reference] for reference in kept_refs],max_bytes=max_bytes)


def model_layout_navigation(rows:list[dict]):
    """Fresh compact dispatch view; aliases are rebuilt from current row order."""
    binding=build_layout_binding(rows)
    aliases={source['evidence_id']:f'E{index}' for index,source in enumerate(binding['sources'],1)}
    if len(aliases)!=len(binding['sources']):raise LayoutBindingError('duplicate evidence id in selected evidence')
    def reference(stable_id):
        alias=aliases.get(stable_id)
        if alias is None:raise LayoutBindingError('layout navigation has a dangling evidence id')
        return alias
    pages=[]
    for page in binding['page_layouts']:
        columns=[]
        for column in page['model_navigation']:
            columns.append([{'text':line['text'],'parts':[
                {'text':part['text'],'ref':reference(part['evidence_id'])} for part in line['parts']]} for line in column])
        pages.append({'document_id':page['document_id'],'page_number':page['page_number'],'columns':columns})
    return {'navigation_version':LAYOUT_NAVIGATION_VERSION,'pages':pages,'unbound_refs':[
        {'ref':reference(item['evidence_id']),'reason':item['binding_reason']} for item in binding['unbound_sources']],
        'limitations':['Navigation is non-citable and does not establish semantic support.']}


@dataclass(frozen=True)
class SelectedPage:
    page_key:str
    document_id:str
    file_name:str
    page_number:int|None
    score:int
    matched_terms:tuple[str,...]
    reason_codes:tuple[str,...]
    source_evidence_ids:tuple[str,...]
    source_text_bytes:int
    selected_text_bytes:int
    issue_dates:tuple[str,...]
    revision_labels:tuple[str,...]
    preview:str

    def public(self)->dict:
        return {
            'page_key':self.page_key,'document_id':self.document_id,'file_name':self.file_name,
            'page_number':self.page_number,'score':self.score,
            'matched_terms':list(self.matched_terms),'reason_codes':list(self.reason_codes),
            'source_evidence_ids':list(self.source_evidence_ids),
            'source_text_bytes':self.source_text_bytes,'selected_text_bytes':self.selected_text_bytes,
            'issue_dates':list(self.issue_dates),'revision_labels':list(self.revision_labels),
            'preview':self.preview,
        }


@dataclass(frozen=True)
class PageSelection:
    run_id:str
    snapshot_id:str
    question:str
    query_terms:tuple[str,...]
    selected_pages:tuple[SelectedPage,...]
    evidence_rows:tuple[dict,...]
    candidate_pages_considered:int
    excluded_by_candidate_limit:int
    excluded_by_page_limit:int
    excluded_by_byte_limit:int
    byte_count:int
    max_pages:int
    max_bytes:int|None
    source_conflicts:tuple[str,...]
    question_entities:tuple[str,...]
    excluded_entity_pages:int
    excluded_entity_rows:int
    selector_version:str
    selection_id:str

    def public(self)->dict:
        value={
            'selection_id':self.selection_id,'selector_version':self.selector_version,
            'run_id':self.run_id,'snapshot_id':self.snapshot_id,'question':self.question,
            'model_called':False,'semantic_graph_used':False,'embeddings_used':False,
            'ranking_policy':'LITERAL_SOURCE_PAGE_MATCHES_ONLY',
            'query_terms':list(self.query_terms),
            'candidate_pages_considered':self.candidate_pages_considered,
            'selected_page_count':len(self.selected_pages),
            'excluded_by_candidate_limit':self.excluded_by_candidate_limit,
            'excluded_by_page_limit':self.excluded_by_page_limit,
            'excluded_by_byte_limit':self.excluded_by_byte_limit,
            'byte_count':self.byte_count,
            'max_pages':None if self.selector_version in COMPLETE_SELECTOR_VERSIONS else self.max_pages,
            'max_bytes':self.max_bytes,'source_conflicts':list(self.source_conflicts),
            'entity_scope_policy':(
                'EXPLICIT_BUILDING_MATCH' if self.question_entities else 'NOT_APPLICABLE'),
            'question_entities':[f'BUILDING {value}' for value in self.question_entities],
            'excluded_by_entity_scope':{
                'pages':self.excluded_entity_pages,'evidence_rows':self.excluded_entity_rows},
            'pages':[item.public() for item in self.selected_pages],
            **({'context_policy':'COMPLETE_SELECTED_SCOPE_V1',
                'source_text_clipped':False,
                'anchor_page_limit':self.max_pages,
                'anchor_page_count':min(self.max_pages,len(self.selected_pages)),
                'expanded_page_count':max(0,len(self.selected_pages)-self.max_pages),
                'layout_available_pages':sum(bool(row.get('layout_lines')) for row in self.evidence_rows),
                'source_row_order':'SAVED_SOURCE_ORDER'}
               if self.selector_version in COMPLETE_SELECTOR_VERSIONS else {}),
        }
        if self.selector_version==LAYOUT_BOUND_SELECTOR_VERSION:
            binding=build_layout_binding(list(self.evidence_rows))
            value.update({
                'context_policy':'COMPLETE_SELECTED_SCOPE_WITH_LAYOUT_BINDING_V1',
                'layout_available_pages':len(binding['page_layouts']),
                'unbound_source_count':len(binding['unbound_sources']),
            })
        return value


def _source_rows(db:Database,run:dict)->tuple[list[dict],list[str]]:
    document_ids=_run_document_ids(run)
    if not document_ids:return [],[]
    placeholders=','.join('?' for _ in document_ids)
    rows=db.all(f'''SELECT e.rowid AS storage_order,e.document_id,e.payload,d.name AS file_name
                    FROM evidence e JOIN documents d ON d.id=e.document_id
                    WHERE e.run_id=? AND e.project_id=?
                      AND e.document_id IN ({placeholders})
                    ORDER BY e.rowid''',(run['id'],run['project_id'],*document_ids))
    result=[];conflicts=[];metadata={document_id:{'date':set(),'revision':set()}
                                    for document_id in document_ids}
    for row in rows:
        try:value=json.loads(row['payload'])
        except (TypeError,ValueError,json.JSONDecodeError):
            conflicts.append(f"{row['file_name']}: malformed parser evidence was excluded")
            continue
        if (not isinstance(value,dict) or value.get('project_id')!=run['project_id']
                or value.get('document_id')!=row['document_id']
                or value.get('input_snapshot_id')!=run['snapshot_id']):
            raise DomainError('Page-selection evidence does not match the saved project snapshot.',409)
        if value.get('content_basis')=='MODEL_VISION_OUTPUT' or value.get('extraction_method')=='VISION':
            continue
        text=_row_text(value)
        if not text:continue
        issued=value.get('internal_revision_date');revision=value.get('revision_label')
        if isinstance(issued,str) and issued.strip():metadata[row['document_id']]['date'].add(issued.strip())
        if isinstance(revision,str) and revision.strip():metadata[row['document_id']]['revision'].add(revision.strip())
        result.append({**row,'value':value})
    for document_id,values in metadata.items():
        if len(values['date'])>1 or len(values['revision'])>1:
            name=next((row['file_name'] for row in rows if row['document_id']==document_id),document_id)
            conflicts.append(f'{name}: conflicting explicit version metadata retained')
    return result,conflicts


def _page_score(page:dict,terms:list[str],identifiers:list[str],
                row_terms:list[str]|None=None)->tuple[int,list[str],list[str],dict[str,int]]:
    name=page['file_name'].casefold();locator=' '.join(page['locator_text']).casefold()
    bodies=[item['value']['raw_text'].casefold() for item in page['rows']]
    matched=[];reasons=set();row_hits={}
    score=0
    for term in terms:
        weight=32 if term in identifiers or _is_identifier(term) else 6
        hit=False
        if _literal_count(name,term):score+=weight*2;reasons.add('FILE_NAME_LITERAL');hit=True
        if _literal_count(locator,term):score+=weight*2;reasons.add('PARSER_LOCATOR_LITERAL');hit=True
        source_hits=0
        for index,body in enumerate(bodies):
            count=min(2,_literal_count(body,term))
            if count:
                source_hits+=count;reasons.add('SOURCE_TEXT_LITERAL');hit=True
                row_hits[index]=row_hits.get(index,0)+weight*count
        score+=weight*min(2,source_hits)
        if term in identifiers and _literal_count(locator,term):
            score+=weight*16;reasons.add('EXACT_IDENTIFIER_LOCATOR')
        if hit:matched.append(term)
    for term in (row_terms or terms):
        if term in terms:continue
        weight=32 if _is_identifier(term) else 6
        for index,body in enumerate(bodies):
            count=min(2,_literal_count(body,term))
            if count:row_hits[index]=row_hits.get(index,0)+weight*count
    return score,matched,sorted(reasons),row_hits


def normalize_selector_version(value:str)->str:
    if value not in SELECTOR_PAGE_LIMITS:
        raise DomainError('Reference page-selector version is unsupported.',409)
    return value


def select_pages(db:Database,run:dict,question:str,*,selector_version:str=SELECTOR_VERSION,
                 max_pages:int|None=None,max_bytes:int|None=None,
                 scope_question:str|None=None)->PageSelection:
    """Select a bounded, inspectable set of source pages without semantic preprocessing."""
    selector_version=normalize_selector_version(selector_version)
    complete=selector_version in COMPLETE_SELECTOR_VERSIONS
    if max_bytes is None and not complete:max_bytes=DEFAULT_MAX_BYTES
    if max_pages is None:max_pages=SELECTOR_PAGE_LIMITS[selector_version]
    if run.get('status') not in ('PARTIAL','COMPLETED'):
        raise DomainError('Page selection requires a completed or partial saved run.',409)
    if type(max_pages) is not int or not 1<=max_pages<=12:
        raise ValueError('page selection max_pages is outside the supported bound')
    if complete and max_bytes is not None:
        raise ValueError('Complete-page selection does not support byte truncation')
    if not complete and (type(max_bytes) is not int or not 1_000<=max_bytes<=96_000):
        raise ValueError('page selection max_bytes is outside the supported bound')
    terms,identifiers=_query_parts(question);source_rows,conflicts=_source_rows(db,run)
    scope_question=question if scope_question is None else scope_question
    if not isinstance(scope_question,str):
        raise ValueError('page selection scope question is invalid')
    scoped_versions=(SCOPED_SELECTOR_VERSION,ROW_FOCUS_SELECTOR_VERSION,SELECTOR_VERSION,
                     COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION)
    question_entities=(_building_entities(scope_question)
                       if selector_version in scoped_versions else set())
    for entity in sorted(question_entities):
        term=f'building {entity.casefold()}'
        if term not in terms and len(terms)<24:terms.insert(0,term)
        if term in terms and term not in identifiers:identifiers.append(term)
    row_terms=(_v6_query_terms(terms)
               if selector_version in (ROW_FOCUS_SELECTOR_VERSION,SELECTOR_VERSION,
                                       COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION) else terms)
    drawing_cover_intent=_drawing_cover_intent(question)
    grouped={}
    for row in source_rows:
        value=row['value'];locator=value.get('locator') if isinstance(value.get('locator'),dict) else {}
        page_number=locator.get('page_number')
        if type(page_number) is not int or page_number<1:page_number=None
        key=(row['document_id'],page_number)
        group=grouped.setdefault(key,{
            'document_id':row['document_id'],'file_name':row['file_name'],'page_number':page_number,
            'rows':[],'locator_text':[],'dates':set(),'revisions':set(),
        })
        group['rows'].append(row)
        group['locator_text'].extend(str(locator.get(name) or '') for name in ('sheet','section','paragraph'))
        if isinstance(value.get('internal_revision_date'),str):group['dates'].add(value['internal_revision_date'])
        if isinstance(value.get('revision_label'),str):group['revisions'].add(value['revision_label'])
    candidates=[];excluded_entity_pages=0
    for group in grouped.values():
        group_entities=set()
        for row in group['rows']:
            group_entities.update(_building_entities(row['value']['raw_text']))
        score,matched,reasons,row_hits=_page_score(group,terms,identifiers,row_terms)
        if (drawing_cover_intent and group['page_number']==1
                and _DRAWING_FILE.search(group['file_name'])):
            score+=96;reasons=sorted(set(reasons)|{'DRAWING_COVER_INTENT'})
        if score<=0:continue
        if question_entities and group_entities and group_entities.isdisjoint(question_entities):
            excluded_entity_pages+=1
            continue
        if question_entities.intersection(group_entities):
            score+=128;reasons=sorted(set(reasons)|{'QUESTION_ENTITY_MATCH'})
        candidates.append({**group,'score':score,'matched':matched,'reasons':reasons,'row_hits':row_hits})
    candidates.sort(key=lambda item:(-item['score'],item['file_name'].casefold(),
                                     item['page_number'] is None,item['page_number'] or 0,
                                     item['document_id']))
    candidate_count=len(candidates)
    excluded_candidates=0 if complete else max(0,candidate_count-MAX_CANDIDATE_PAGES)
    if not complete:candidates=candidates[:MAX_CANDIDATE_PAGES]
    # Expand only literal sheet or section/paragraph scopes in the same document.
    # Unknown relationships remain unknown; file-name similarity is not an edge.
    requested_scopes={}
    if complete:
        for match in _EXPLICIT_QUERY_ID.finditer(question):
            for name,field in (('sheet','sheet'),('spec','section'),('paragraph','paragraph')):
                if match.group(name):
                    requested_scopes.setdefault(field,set()).add(
                        ' '.join(match.group(name).casefold().split()).strip('._/#-'))
    def scope_keys(candidate):
        keys=set()
        if not requested_scopes:return keys
        groups=[]
        if 'sheet' in requested_scopes:groups.append(('sheet',))
        spec_fields=tuple(field for field in ('section','paragraph') if field in requested_scopes)
        if spec_fields:groups.append(spec_fields)
        for row in candidate['rows']:
            loc=row['value'].get('locator') or {}
            # Cross-document sheet and clause groups can be separate sources.
            # A known contradictory dimension must never disappear when a
            # different dimension matches; incomplete clause groups stay narrow.
            present={field for field in requested_scopes if isinstance(loc.get(field),str)}
            if any(not any(_literal_count(loc[field].casefold(),value)
                           for value in requested_scopes[field]) for field in present):continue
            matched=[field for group in groups if all(field in present for field in group)
                     for field in group]
            if matched:
                keys.add((candidate['document_id'],tuple((field,loc[field]) for field in sorted(matched)),
                          loc.get('section') if 'paragraph' in matched else None))
        return keys
    continuation_keys=set()
    if complete:
        # Explicitly named sources are anchors in their own right. Otherwise
        # four high-scoring spec pages can hide a separately requested drawing.
        for candidate in candidates:continuation_keys.update(scope_keys(candidate))
    pages=[];evidence=[];used=0;excluded_page=0;excluded_bytes=0;excluded_entity_rows=0
    for candidate in candidates:
        if len(pages)>=max_pages and not (complete and continuation_keys.intersection(scope_keys(candidate))):
            excluded_page+=1;continue
        if max_bytes is not None and max_bytes-used<=0:excluded_bytes+=1;continue
        ordered=sorted(enumerate(candidate['rows']),key=lambda pair:(
            0 if complete else -candidate['row_hits'].get(pair[0],0),pair[1]['storage_order']))
        selected=[];layout_values=[];selected_bytes=0
        source_bytes=sum(len(row['value']['raw_text'].encode('utf-8')) for row in candidate['rows'])
        for _,row in ordered:
            value=row['value'];text=value['raw_text']
            row_entities=_building_entities(text)
            if question_entities and row_entities and row_entities.isdisjoint(question_entities):
                excluded_entity_rows+=1
                continue
            remaining=None if max_bytes is None else max_bytes-used-selected_bytes
            if remaining is not None and remaining<=0:break
            clipped=text if complete else _clip_utf8(text,remaining)
            if not clipped:continue
            locator=value.get('locator') if isinstance(value.get('locator'),dict) else {}
            item={
                'evidence_id':value['evidence_id'],'document_id':row['document_id'],
                'file_name':row['file_name'],'raw_text':clipped,'prompt_text':clipped,
                'source_order':row['storage_order'],
                'locator':{
                    'page_number':candidate['page_number'],'sheet':locator.get('sheet'),
                    'section':locator.get('section'),'paragraph':locator.get('paragraph'),
                    'bbox':locator.get('bbox'),'coordinate_system':locator.get('coordinate_system'),
                },
            }
            if selector_version==LAYOUT_BOUND_SELECTOR_VERSION:
                # v9 keeps only parser-produced geometry metadata; it never
                # synthesizes an evidence relation or changes source text.
                item['extraction_method']=value.get('extraction_method')
                item['text_map']=value.get('text_map')
            if selector_version in (SCOPED_SELECTOR_VERSION,ROW_FOCUS_SELECTOR_VERSION):
                layout=_layout_lines(value,clipped)
                if layout:item['layout_lines']=layout
            selected.append(item)
            layout_values.append((value,clipped))
            selected_bytes+=len(clipped.encode('utf-8'))
        if not selected:excluded_bytes+=1;continue
        if selector_version in (SELECTOR_VERSION,COMPLETE_SELECTOR_VERSION):
            layout=_page_layout_columns(layout_values,max_bytes=None if complete else MAX_LAYOUT_BYTES)
            if layout:selected[0]['layout_lines']=layout
        page_key='PG-'+hashlib.sha256(dumps((run['id'],candidate['document_id'],
                                             candidate['page_number'])).encode()).hexdigest()[:24]
        preview=_clip_utf8('\n'.join(item['raw_text'] for item in selected),480)
        page=SelectedPage(
            page_key,candidate['document_id'],candidate['file_name'],candidate['page_number'],
            candidate['score'],tuple(candidate['matched']),tuple(candidate['reasons']),
            tuple(item['evidence_id'] for item in selected),source_bytes,selected_bytes,
            tuple(sorted(candidate['dates'])),tuple(sorted(candidate['revisions'])),preview)
        pages.append(page);evidence.extend(selected);used+=selected_bytes
    lineages={}
    for page in pages:
        lineages.setdefault(_lineage(page.file_name),{})[page.document_id]=page
    for values in lineages.values():
        if len(values)<=1:continue
        identities={(item.issue_dates,item.revision_labels) for item in values.values()}
        if len(identities)>1:
            names=', '.join(sorted({item.file_name for item in values.values()},key=str.casefold))
            conflicts.append(f'Multiple matching document versions retained: {names}')
    identity=(selector_version,run['id'],run['snapshot_id'],' '.join(question.split()),
              sorted(question_entities),
              [(item.page_key,item.score,list(item.source_evidence_ids),item.selected_text_bytes)
               for item in pages])
    selection_id='SEL-'+hashlib.sha256(dumps(identity).encode()).hexdigest()[:32]
    return PageSelection(
        run['id'],run['snapshot_id'],question,tuple(row_terms),tuple(pages),tuple(evidence),
        candidate_count,excluded_candidates,excluded_page,excluded_bytes,used,max_pages,max_bytes,
        tuple(conflicts),tuple(sorted(question_entities)),excluded_entity_pages,
        excluded_entity_rows,selector_version,selection_id)
