"""Bounded project-file retrieval and evidence-grounded answers."""
from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from typing import TYPE_CHECKING

from app.db import Database, DomainError
from app.verification import citation, exact_quote, statement_span
from app.workflows import normalize_identifier
from contracts.runtime_rules import validate_schema

if TYPE_CHECKING:
    from app.gateway import Gateway

_WORD = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", re.I)
_TOKEN = re.compile(r"[a-z0-9]+", re.I)
_SIMPLE_TERM = re.compile(r"[a-z0-9]+\Z", re.I)
_STOP = frozenset({
    'a','an','and','are','as','at','be','by','can','do','does','for','from','how','i','in',
    'is','it','of','on','or','project','show','tell','that','the','this','to','was','were',
    'what','when','where','which','who','why','with','would',
})
_MAX_QUERY_TERMS = 10
_MAX_SEARCH_TERMS = 24
_MAX_CANDIDATES = 160
_MAX_FAMILY_CANDIDATES = 12
_MAX_IDENTIFIER_CANDIDATES = 12
_MAX_RESULTS = 8
_MAX_FRAGMENT_CHARS = 2600
_MAX_CONTEXT_CHARS = 18000
_EQUIVALENT_GROUPS = (
    ('accept','accepted','acceptance','approve','approved','approval'),
    ('answer','answered','reply','replied','response','responded'),
    ('close','closed','resolve','resolved'),
    ('email','e-mail','message','correspondence'),
    ('send','sent','sender','from'),
    ('receive','received','recipient'),
    ('spec','specification','specifications'),
    ('qty','quantity','quantities','count'),
    ('dimension','dimensions','size','sizes'),
    ('require','required','requirement','requirements','shall'),
    ('test','tests','testing'),
    ('inspect','inspected','inspection','inspections'),
    ('material','materials'),
    ('revision','revisions','rev'),
    ('drawing','drawings','dwg'),
)
_TERM_ALIASES = {
    term:tuple(value for value in group if value != term)
    for group in _EQUIVALENT_GROUPS for term in group
}
_PHRASE_ALIASES = (
    (re.compile(r'\brequest\s+for\s+information\b',re.I),
     ('request','information'),('request for information','rfi')),
    (re.compile(r'\bshop\s+drawing(?:s)?\b',re.I),
     ('shop','drawing','drawings'),('shop drawing','shop drawings','submittal')),
    (re.compile(r'\bproduct\s+data\b',re.I),
     ('product','data'),('product data','submittal')),
)
_COMPARISON_INTENT = re.compile(
    r'\b(?:compare|comparison|difference|differences|differ|changed|changes|versus|vs|across)\b',re.I)
_QUERY_SOURCE_PATTERNS = (
    ('SPECIFICATION',re.compile(r'\b(?:spec|specification|specifications)\b',re.I)),
    ('RFI',re.compile(r'\b(?:rfi|request\s+for\s+information)\b',re.I)),
    ('SUBMITTAL',re.compile(r'\b(?:submittal|shop\s+drawing|product\s+data)\b',re.I)),
    ('EMAIL',re.compile(r'\b(?:email|e-mail|message|correspondence)\b',re.I)),
)
_RFI_HEADER = re.compile(r'(?im)^\s*(?:rfi|request\s+for\s+information)\b')
_SUBMITTAL_HEADER = re.compile(r'(?im)^\s*(?:submittal|submission)\b')
_EMAIL_HEADER = re.compile(r'(?im)^\s*(?:from|to|cc|subject):')
_CSI_SECTION = re.compile(r'(?i)^\s*(?:section\s+)?\d{2}(?:\s+\d{2}){1,2}\b')
_NUMERIC_RFI = re.compile(
    r'(?i)\b(?:rfi|request\s+for\s+information)\s*'
    r'(?:(?:no\.?|number)\s*)?[#:-]?\s*(\d+)(?![a-z0-9._/-])')
_WORKFLOW_MARKER = re.compile(
    r'(?i)\b(?P<kind>request\s+for\s+information|rfi|submittal|submission)\b'
    r'(?P<tail>[^\r\n]{0,120})')
_STATUS_INTENT = re.compile(
    r'(?i)\b(?:status|disposition|approved|rejected|pending|open|closed|answered|'
    r'reviewed|void|revise\s+(?:and|/)\s*resubmit)\b')
_NUMERIC_VALUE = re.compile(
    r'(?<!\w)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?!\w)')
_DISPOSITION_PHRASES = (
    (re.compile(r'(?i)\bnot\s+yet\s+approved\b'),'NOT_APPROVED',
     frozenset({'NOT_APPROVED'})),
    (re.compile(r'(?i)\b(?:not\s+approved|rejected|disapproved|denied)\b'),
     'REJECTED',frozenset({'REJECTED'})),
    (re.compile(r'(?i)\bnot\s+closed\b'),'NOT_CLOSED',frozenset({'NOT_CLOSED'})),
    (re.compile(r'(?i)\bnot\s+open\b'),'NOT_OPEN',frozenset({'NOT_OPEN'})),
    (re.compile(r'(?i)\bnot\s+pending\b'),'NOT_PENDING',frozenset({'NOT_PENDING'})),
    (re.compile(r'(?i)\bnot\s+rejected\b'),'NOT_REJECTED',frozenset({'NOT_REJECTED'})),
    (re.compile(r'(?i)\b(?:not\s+answered|unanswered)\b'),'NOT_ANSWERED',
     frozenset({'NOT_ANSWERED'})),
    (re.compile(r'(?i)\bnot\s+submitted\b'),'NOT_SUBMITTED',
     frozenset({'NOT_SUBMITTED'})),
    (re.compile(r'(?i)\bnot\s+draft\b'),'NOT_DRAFT',frozenset({'NOT_DRAFT'})),
    (re.compile(r'(?i)\bnot\s+(?:void(?:ed)?|cancelled|canceled)\b'),
     'NOT_VOID',frozenset({'NOT_VOID'})),
    (re.compile(
        r'(?i)\b(?:approved\s+(?:as\s+noted|with\s+comments)|'
        r'make\s+corrections\s+noted|reviewed\s+as\s+noted|'
        r'furnish\s+as\s+corrected)\b'),
     'APPROVED_AS_NOTED',frozenset({'APPROVED','APPROVED_AS_NOTED'})),
    (re.compile(
        r'(?i)\b(?:revise\s*(?:and|/)\s*resubmit|amend\s+and\s+resubmit|'
        r'returned\s+for\s+correction)\b'),
     'REVISE_AND_RESUBMIT',frozenset({'REVISE_AND_RESUBMIT'})),
    (re.compile(
        r'(?i)\b(?:approved(?:\s+as\s+submitted)?|accepted|'
        r'furnish\s+as\s+submitted|no\s+exceptions\s+taken)\b'),
     'APPROVED',frozenset({'APPROVED'})),
    (re.compile(
        r'(?i)\bopen\s+(?:for\s+(?:manager|review|coordinator)|in\s+review|'
        r'waiting\s+for\s+submission)\b'),
     'OPEN_PENDING',frozenset({'OPEN','PENDING'})),
    (re.compile(
        r'(?i)\b(?:under\s+review|for\s+review|waiting\s+for\s+submission|pending)\b'),
     'PENDING',frozenset({'PENDING'})),
    (re.compile(r'(?i)\bsubmitted\b'),'SUBMITTED',frozenset({'SUBMITTED','PENDING'})),
    (re.compile(r'(?i)\bopen\s+answered\b'),'OPEN_ANSWERED',
     frozenset({'OPEN','ANSWERED'})),
    (re.compile(r'(?i)\bclosed\s*-\s*draft\b'),'CLOSED_DRAFT',
     frozenset({'CLOSED','DRAFT'})),
    (re.compile(r'(?i)\bclosed\s*-\s*revised\b'),'CLOSED_REVISED',
     frozenset({'CLOSED'})),
    (re.compile(r'(?i)\b(?:closed|resolved)\b'),'CLOSED',frozenset({'CLOSED'})),
    (re.compile(r'(?i)\b(?:open|unresolved|outstanding)\b'),'OPEN',
     frozenset({'OPEN'})),
    (re.compile(r'(?i)\b(?:answered|response\s+issued|official\s+response)\b'),
     'ANSWERED',frozenset({'ANSWERED'})),
    (re.compile(r'(?i)\breviewed\b(?!\s+as\s+noted)'),'REVIEWED',
     frozenset({'REVIEWED'})),
    (re.compile(r'(?i)\b(?:void(?:ed)?|cancelled|canceled)\b'),'VOID',
     frozenset({'VOID'})),
    (re.compile(r'(?i)\bdraft\b'),'DRAFT',frozenset({'DRAFT'})),
)
_DISPOSITION_GROUPS = (
    ('APPROVED','APPROVED_AS_NOTED','REVISE_AND_RESUBMIT','REJECTED',
     'PENDING','SUBMITTED','REVIEWED'),
    ('OPEN','OPEN_ANSWERED','OPEN_PENDING','CLOSED','CLOSED_DRAFT','CLOSED_REVISED',
     'ANSWERED','DRAFT','SUBMITTED','REJECTED','VOID','PENDING'),
)
_CONFLICTING_DISPOSITION_PAIRS = frozenset(
    frozenset({left,right}) for group in _DISPOSITION_GROUPS
    for index,left in enumerate(group) for right in group[index+1:]
) | frozenset({
    frozenset({'APPROVED','NOT_APPROVED'}),
    frozenset({'APPROVED_AS_NOTED','NOT_APPROVED'}),
    frozenset({'CLOSED','NOT_CLOSED'}),
    frozenset({'CLOSED_DRAFT','NOT_CLOSED'}),
    frozenset({'CLOSED_REVISED','NOT_CLOSED'}),
    frozenset({'OPEN','NOT_OPEN'}),
    frozenset({'OPEN_ANSWERED','NOT_OPEN'}),
    frozenset({'OPEN_PENDING','NOT_OPEN'}),
    frozenset({'PENDING','NOT_PENDING'}),
    frozenset({'OPEN_PENDING','NOT_PENDING'}),
    frozenset({'SUBMITTED','NOT_PENDING'}),
    frozenset({'REJECTED','NOT_REJECTED'}),
    frozenset({'ANSWERED','NOT_ANSWERED'}),
    frozenset({'OPEN_ANSWERED','NOT_ANSWERED'}),
    frozenset({'SUBMITTED','NOT_SUBMITTED'}),
    frozenset({'DRAFT','NOT_DRAFT'}),
    frozenset({'CLOSED_DRAFT','NOT_DRAFT'}),
    frozenset({'VOID','NOT_VOID'}),
})
_FAMILY_SQL_FILTERS = {
    'EMAIL':'''(LOWER(d.name) LIKE '%.eml' OR LOWER(d.name) LIKE '%.msg'
        OR LOWER(COALESCE(json_extract(e.payload,'$.locator.section'),'')) LIKE 'email >%'
        OR (LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.raw_text'),''))) LIKE 'from:%'
            AND INSTR(LOWER(COALESCE(json_extract(e.payload,'$.raw_text'),'')),'subject:')>0))''',
    'RFI':'''(LOWER(COALESCE(json_extract(e.payload,'$.locator.section'),'')) LIKE '%rfi%'
        OR LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.raw_text'),''))) LIKE 'rfi %'
        OR LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.raw_text'),''))) LIKE 'request for information %'
        OR LOWER(d.name) LIKE '%rfi%')''',
    'SUBMITTAL':'''(LOWER(COALESCE(json_extract(e.payload,'$.locator.section'),'')) LIKE '%submittal%'
        OR LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.raw_text'),''))) LIKE 'submittal %'
        OR LOWER(d.name) LIKE '%submittal%')''',
    'SPECIFICATION':'''(LOWER(d.name) LIKE '%spec%'
        OR LOWER(COALESCE(json_extract(e.payload,'$.locator.section'),'')) LIKE '%spec%'
        OR LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.locator.section'),''))) GLOB '[0-9][0-9] [0-9][0-9]*'
        OR LOWER(LTRIM(COALESCE(json_extract(e.payload,'$.locator.section'),''))) GLOB 'section [0-9][0-9] [0-9][0-9]*')''',
}


def question_terms(question: str) -> list[str]:
    terms=[]
    for value in _WORD.findall(question.casefold()):
        if value in _STOP or (len(value) < 2 and not value.isdigit()) or value in terms:
            continue
        terms.append(value)
    return terms[:_MAX_QUERY_TERMS]


def _numeric_rfi_identifiers(text: str) -> list[tuple[str,str]]:
    found=[];seen=set()
    for match in _NUMERIC_RFI.finditer(text):
        raw=match.group(1)
        canonical=normalize_identifier('RFI',raw)
        if canonical is None or not canonical.isdigit() or canonical in seen:continue
        seen.add(canonical);found.append((raw,canonical))
    return found


def numeric_rfi_search_terms(question: str) -> list[str]:
    identifiers=_numeric_rfi_identifiers(question)
    numbers=[]
    for raw,canonical in identifiers:
        for value in (raw,canonical):
            if value not in numbers:numbers.append(value)
    for zeros in range(1,_MAX_SEARCH_TERMS):
        for _,canonical in identifiers:
            value='0'*zeros+canonical
            if value not in numbers:numbers.append(value)
    values=[]
    prefixes=('rfi ','rfi no ','rfi number ',
              'request for information ','request for information no ',
              'request for information number ')
    for number in numbers:
        for prefix in prefixes:
            values.append(prefix+number)
            if len(values)>=_MAX_SEARCH_TERMS:return values
    return values


def _query_concepts(question: str, terms: list[str]) -> list[tuple[str,tuple[str,...]]]:
    phrases=[];consumed=set()
    for pattern,source_terms,variants in _PHRASE_ALIASES:
        if pattern.search(question):
            phrases.append((variants[0],variants));consumed.update(source_terms)
    for raw,canonical in _numeric_rfi_identifiers(question):
        phrases.append((f'rfi {canonical}',(f'rfi {canonical}',)))
        consumed.update(('rfi',raw.casefold()))
    concepts=[(term,(term,*_TERM_ALIASES.get(term,())))
              for term in terms if term not in consumed]
    return [*concepts,*phrases]


def expanded_query_terms(question: str, terms: list[str] | None = None) -> list[str]:
    """Add only audited construction/workflow equivalents for local retrieval."""
    originals=question_terms(question) if terms is None else list(terms)
    concepts=_query_concepts(question,originals)
    expanded=[]
    for _,variants in concepts:
        if variants[0] not in expanded:expanded.append(variants[0])
    for depth in range(1,max((len(variants) for _,variants in concepts),default=0)):
        for _,variants in concepts:
            if depth<len(variants) and variants[depth] not in expanded:
                expanded.append(variants[depth])
                if len(expanded)>=_MAX_SEARCH_TERMS:return expanded
    return expanded


def search_query_terms(question: str, terms: list[str] | None = None) -> list[str]:
    """Build the bounded primary FTS query without crowding out audited aliases."""
    originals=question_terms(question) if terms is None else list(terms)
    expanded=expanded_query_terms(question,originals)
    values=[]
    for value in (*originals,*expanded):
        if value not in values:values.append(value)
        if len(values)>=_MAX_SEARCH_TERMS:break
    return values


def requires_source_diversity(question: str) -> bool:
    if _COMPARISON_INTENT.search(question):return True
    return len(_requested_source_families(question))>=2


def _requested_source_families(question: str) -> tuple[str,...]:
    return tuple(family for family,pattern in _QUERY_SOURCE_PATTERNS if pattern.search(question))


@lru_cache(maxsize=256)
def _term_pattern(term: str) -> re.Pattern:
    return re.compile(r'(?<![a-z0-9])'+re.escape(term)+r'(?![a-z0-9])',re.I)


def _concept_match(searchable: str, token_counts: Counter,
                   variants: tuple[str,...]) -> tuple[str,bool,int] | None:
    for index,value in enumerate(variants):
        if _SIMPLE_TERM.fullmatch(value):
            count=token_counts.get(value,0)
            if count:return value,index==0,min(3,count)
            continue
        matches=_term_pattern(value).finditer(searchable)
        if next(matches,None) is not None:
            return value,index==0,1+sum(1 for _ in zip(range(2),matches))
    return None


def _workflow_search_aliases(searchable: str) -> str:
    aliases=' '.join(f'rfi {canonical}'
                     for _,canonical in _numeric_rfi_identifiers(searchable))
    return searchable+' '+aliases if aliases else searchable


def _workflow_identities(text: str) -> set[tuple[str,str]]:
    identities=set()
    for match in _WORKFLOW_MARKER.finditer(text):
        workflow=('RFI' if match.group('kind').casefold().startswith(('rfi','request'))
                  else 'SUBMITTAL')
        identifier=normalize_identifier(workflow,match.group('tail'))
        if identifier:identities.add((workflow,identifier))
    return identities


def requires_workflow_status_index(question: str) -> bool:
    return bool(_STATUS_INTENT.search(question) and _workflow_identities(question))


def _workflow_index_status_conflicts(question: str, workflow_index: dict | None) -> list[str]:
    """Find exact requested workflow IDs with multiple explicit indexed statuses."""
    if not requires_workflow_status_index(question) or not isinstance(workflow_index,dict):return []
    targets=_workflow_identities(question)
    if not targets:return []
    conflicts=[]
    for item in workflow_index.get('items',[]):
        if not isinstance(item,dict):continue
        kind=item.get('kind')
        identifier=normalize_identifier(kind,item.get('identifier'))
        if (kind,identifier) not in targets:continue
        statuses=set()
        for member in item.get('members',[]):
            if not isinstance(member,dict) or member.get('source')!='PRIMARY':continue
            status=member.get('status')
            if isinstance(status,str):
                statuses.update(value.strip().upper() for value in status.split(' / ')
                                if value.strip())
        if len(statuses)>1:conflicts.append(f'{kind} {identifier}')
    return conflicts


def _evidence_workflow_identities(evidence: dict) -> set[tuple[str,str]]:
    locator=evidence.get('locator') if isinstance(evidence.get('locator'),dict) else {}
    return _workflow_identities(
        str(locator.get('section') or '')+'\n'+str(evidence.get('raw_text') or ''))


def _evidence_disposition_text(evidence: dict) -> str:
    locator=evidence.get('locator') if isinstance(evidence.get('locator'),dict) else {}
    return str(locator.get('section') or '')+'\n'+str(evidence.get('raw_text') or '')


def _source_family(evidence: dict) -> str:
    name=str(evidence.get('file_name') or '').casefold()
    locator=evidence.get('locator') if isinstance(evidence.get('locator'),dict) else {}
    section=str(locator.get('section') or '').casefold()
    text=str(evidence.get('raw_text') or '')
    if name.endswith(('.eml','.msg')) or section.startswith('email >'):
        return 'EMAIL'
    if _term_pattern('rfi').search(section) or _RFI_HEADER.search(text):return 'RFI'
    if _term_pattern('submittal').search(section) or _SUBMITTAL_HEADER.search(text):
        return 'SUBMITTAL'
    if _EMAIL_HEADER.search(text) and re.search(r'(?im)^\s*subject:',text):return 'EMAIL'
    if (_term_pattern('rfi').search(name)
            or re.search(r'\brequest[ ._-]+for[ ._-]+information\b',name)):
        return 'RFI'
    if (_term_pattern('submittal').search(name)
            or re.search(r'\b(?:shop[ ._-]+drawing|product[ ._-]+data)\b',name)):
        return 'SUBMITTAL'
    if (_term_pattern('spec').search(name) or _term_pattern('specification').search(name)
            or _term_pattern('spec').search(section)
            or _term_pattern('specification').search(section) or _CSI_SECTION.search(section)):
        return 'SPECIFICATION'
    return 'OTHER'


def _source_key(evidence: dict) -> tuple[str,str]:
    document=str(evidence.get('_source_document_id') or evidence.get('document_id')
                 or evidence.get('file_name') or evidence.get('evidence_id'))
    return document,_source_family(evidence)


def _numeric_values(text: str) -> set[str]:
    """Canonicalize explicit numeric literals without inferring conversions."""
    values=set()
    for match in _NUMERIC_VALUE.finditer(text):
        token=match.group().replace(',','')
        parts=token.split('/')
        normalized=[]
        for part in parts:
            if part.count('.')<=1:
                whole,dot,fraction=part.partition('.')
                whole=str(int(whole))
                fraction=fraction.rstrip('0')
                normalized.append(whole+(dot+fraction if fraction else ''))
            else:
                normalized.append(part)
        values.add('/'.join(normalized))
    return values


def _require_numeric_support(claim: str, quotes: list[str], label: str) -> None:
    if _numeric_values(claim)-_numeric_values(' '.join(quotes)):
        raise ValueError(label+' contains a numeric claim absent from its citations')


def _disposition_matches(text: str) -> list[tuple[str,frozenset[str]]]:
    """Return longest non-overlapping bounded disposition phrases."""
    matches=[]
    for priority,(pattern,primary,values) in enumerate(_DISPOSITION_PHRASES):
        for match in pattern.finditer(text):
            matches.append((match.start(),-(match.end()-match.start()),priority,
                            match.end(),primary,values))
    occupied=[];found=[]
    for start,_,_,end,primary,values in sorted(matches):
        if any(start<used_end and end>used_start for used_start,used_end in occupied):continue
        occupied.append((start,end));found.append((primary,values))
    return found


def _disposition_values(text: str) -> set[str]:
    """Return supported claim values while preserving phrase specificity separately."""
    found=set()
    for _,values in _disposition_matches(text):found.update(values)
    return found


def _require_disposition_support(claim: str, quotes: list[str], label: str) -> None:
    if _disposition_values(claim)-_disposition_values(' '.join(quotes)):
        raise ValueError(label+' contains a workflow disposition absent from its citations')


def _require_unambiguous_dispositions(quotes: list[str], label: str) -> None:
    values={primary for primary,_ in _disposition_matches(' '.join(quotes))}
    if any(pair.issubset(values) for pair in _CONFLICTING_DISPOSITION_PAIRS):
        raise ValueError(label+' cites conflicting workflow dispositions without precedence')


def _retrieved_scope(evidence: list[dict], *, source_type: str | None = None,
                     file_name: str | None = None,
                     identity: tuple[str,str] | None = None) -> list[str]:
    scoped=[]
    for item in evidence:
        if source_type is not None and _source_family(item)!=source_type:continue
        if file_name is not None and item.get('file_name')!=file_name:continue
        if identity is not None and identity not in _evidence_workflow_identities(item):continue
        scoped.append(_evidence_disposition_text(item))
    return scoped


def _selection_order(ranked: list[tuple], diversify: bool) -> list[tuple]:
    if not diversify:return ranked
    distinct=[];remaining=[];seen=set()
    for item in ranked:
        key=_source_key(item[3])
        if key in seen:remaining.append(item)
        else:seen.add(key);distinct.append(item)
    return [*distinct,*remaining]


def _fts_query(terms: list[str]) -> str:
    return ' OR '.join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _candidate_rows(db: Database, run_id: str, terms: list[str],
                    diversify: bool = False, source_families: tuple[str,...] = (),
                    identifier_terms: tuple[str,...] = ()) -> list[dict]:
    if getattr(db,'evidence_search_available',False):
        query='''SELECT e.id AS evidence_row_id,e.payload,d.name AS file_name,
                        e.document_id AS source_document_id,
                                bm25(evidence_search,2,1.5,1) AS search_rank
                         FROM evidence_search
                         JOIN evidence e ON e.rowid=evidence_search.rowid
                         JOIN documents d ON d.id=e.document_id
                         WHERE evidence_search MATCH ? AND e.run_id=?
                           AND COALESCE(json_extract(e.payload,'$.content_basis'),'')!='MODEL_VISION_OUTPUT'
                           AND COALESCE(json_extract(e.payload,'$.extraction_method'),'')!='VISION'
                         {family_filter}
                         ORDER BY search_rank,e.rowid LIMIT ?'''
        match=_fts_query(terms)
        rows=db.all(query.format(family_filter=''),[match,run_id,_MAX_CANDIDATES])
        seen={row['evidence_row_id'] for row in rows}
        if identifier_terms:
            extra=db.all(query.format(family_filter=''),
                         [_fts_query(list(identifier_terms)),run_id,_MAX_IDENTIFIER_CANDIDATES])
            for row in extra:
                if row['evidence_row_id'] not in seen:
                    rows.append(row);seen.add(row['evidence_row_id'])
        if not diversify:return rows
        families=(source_families if len(source_families)>=2
                  else tuple(_FAMILY_SQL_FILTERS))
        for family in families:
            family_filter=_FAMILY_SQL_FILTERS[family]
            extra=db.all(query.format(family_filter='AND '+family_filter),
                         [match,run_id,_MAX_FAMILY_CANDIDATES])
            for row in extra:
                if row['evidence_row_id'] not in seen:
                    rows.append(row);seen.add(row['evidence_row_id'])
        return rows
    # FTS5 is optional.  The compatibility path favors complete results over
    # row-order truncation.  One local scan is also cheaper than repeating
    # JSON extraction for every expanded term and locator field.
    return db.all('''SELECT e.id AS evidence_row_id,e.payload,d.name AS file_name,
                            e.document_id AS source_document_id
                     FROM evidence e
                     JOIN documents d ON d.id=e.document_id
                     WHERE e.run_id=?
                       AND COALESCE(json_extract(e.payload,'$.content_basis'),'')!='MODEL_VISION_OUTPUT'
                       AND COALESCE(json_extract(e.payload,'$.extraction_method'),'')!='VISION' ''',[run_id])


def _window(text: str, terms: list[str]) -> str:
    if len(text) <= _MAX_FRAGMENT_CHARS:
        return text
    positions=[match.start() for term in terms if (match:=_term_pattern(term).search(text))]
    anchor=min(positions,default=0)
    start=max(0,anchor-_MAX_FRAGMENT_CHARS//3)
    end=min(len(text),start+_MAX_FRAGMENT_CHARS)
    start=max(0,end-_MAX_FRAGMENT_CHARS)
    return text[start:end]


def retrieve_evidence(db: Database, run: dict, question: str) -> list[dict]:
    """Use run-scoped full-text candidates, then deterministic local ranking.

    SQLite FTS5 avoids another service or vector store.  Environments without
    FTS5 use a complete local scan rather than silently losing later evidence.
    Prompt windows stay bounded while citations are checked against the full
    immutable evidence text.
    """
    terms=question_terms(question)
    if not terms:
        return []
    concepts=_query_concepts(question,terms)
    identifier_terms=tuple(numeric_rfi_search_terms(question))
    search_terms=search_query_terms(question,terms)
    source_families=_requested_source_families(question)
    diversify=requires_source_diversity(question)
    rows=_candidate_rows(
        db,run['id'],search_terms,diversify,source_families,identifier_terms)
    ranked=[]
    for row in rows:
        try:evidence=json.loads(row['payload'])
        except (TypeError,ValueError,json.JSONDecodeError):continue
        if evidence.get('content_basis')=='MODEL_VISION_OUTPUT' or evidence.get('extraction_method')=='VISION':
            continue
        text=evidence.get('raw_text')
        if not isinstance(text,str) or not text.strip():continue
        locator=evidence.get('locator') if isinstance(evidence.get('locator'),dict) else {}
        locator_text=' '.join(str(locator.get(key) or '')
                              for key in ('section','sheet','page','paragraph'))
        searchable=_workflow_search_aliases(
            ' '.join((text,row['file_name'],locator_text)).casefold())
        token_counts=Counter(_TOKEN.findall(searchable))
        concept_hits=[(label,_concept_match(searchable,token_counts,variants))
                      for label,variants in concepts]
        concept_hits=[(label,match) for label,match in concept_hits if match is not None]
        if not concept_hits:continue
        actual_matches=[];score=0
        for label,match in concept_hits:
            actual,direct,count=match
            if actual not in actual_matches:actual_matches.append(actual)
            score+=(12 if label.startswith('rfi ') and any(char.isdigit() for char in label)
                    else 4 if any(char.isdigit() for char in label)
                    else 2 if direct else 1)
            score+=count
        score+=min(6,len(concept_hits))
        if len(concept_hits)==len(concepts):score+=8
        evidence={**evidence,'file_name':row['file_name'],
                  'prompt_text':_window(text,actual_matches),'_question_terms':actual_matches,
                  '_source_document_id':row['source_document_id']}
        ranked.append((score,len(concept_hits),evidence['evidence_id'],evidence))
    ranked.sort(key=lambda item:(-item[0],-item[1],item[2]))
    selected=[];used=0
    for _,_,_,evidence in _selection_order(ranked,diversify):
        size=len(evidence['prompt_text'])
        if selected and used+size>_MAX_CONTEXT_CHARS:continue
        selected.append(evidence);used+=size
        if len(selected)>=_MAX_RESULTS:break
    return selected


def validate_answer_model(value: dict, evidence: list[dict], question: str = '') -> None:
    validate_schema('project-answer',value)
    allowed={item['evidence_id']:item for item in evidence}
    if value['status']=='ANSWERED' and not value['citations'] and not value['source_findings']:
        raise ValueError('an answered response requires at least one citation')
    top_level_quotes=[]
    for item in value['citations']:
        source=allowed.get(item['evidence_id'])
        if source is None:raise ValueError('citation is outside the retrieved evidence scope')
        exact_quote(source,item['quote'])
        top_level_quotes.append(item['quote'])
    answer_quotes=list(top_level_quotes)
    finding_sources=[];question_targets=_workflow_identities(question)
    status_intent=bool(_STATUS_INTENT.search(question) or _disposition_values(value['answer']))
    for finding in value['source_findings']:
        key=(finding['source_type'],finding['file_name'])
        if key in finding_sources:raise ValueError('comparison contains a duplicate source finding')
        finding_sources.append(key)
        finding_quotes=[]
        for item in finding['citations']:
            source=allowed.get(item['evidence_id'])
            if source is None:raise ValueError('source finding is outside the retrieved evidence scope')
            if source.get('file_name')!=finding['file_name']:
                raise ValueError('source finding file name does not match its evidence')
            if _source_family(source)!=finding['source_type']:
                raise ValueError('source finding type does not match its evidence')
            exact_quote(source,item['quote'])
            finding_quotes.append(item['quote'])
        if value['status']=='ANSWERED':
            _require_unambiguous_dispositions(finding_quotes,'source finding')
            if status_intent or _disposition_values(finding['statement']):
                targets=_workflow_identities(finding['statement']) or {
                    target for target in question_targets if target[0]==finding['source_type']}
                retrieved=_retrieved_scope(
                    evidence,source_type=finding['source_type'],file_name=finding['file_name'])
                if targets:
                    retrieved=[text for text in retrieved
                               if _workflow_identities(text)&targets]
                if retrieved:
                    _require_unambiguous_dispositions(retrieved,'retrieved source finding')
            _require_numeric_support(finding['statement'],finding_quotes,'source finding')
            _require_disposition_support(finding['statement'],finding_quotes,'source finding')
        answer_quotes.extend(finding_quotes)
    if value['status']=='ANSWERED':
        if top_level_quotes:_require_unambiguous_dispositions(top_level_quotes,'answer')
        if not value['source_findings'] and status_intent:
            targets=_workflow_identities(question) or _workflow_identities(value['answer'])
            if targets:
                for target in targets:
                    retrieved=_retrieved_scope(evidence,identity=target)
                    if retrieved:_require_unambiguous_dispositions(retrieved,'retrieved answer')
            else:
                families=_requested_source_families(question)
                retrieved=(_retrieved_scope(evidence,source_type=families[0])
                           if len(families)==1 else _retrieved_scope(evidence))
                if retrieved:_require_unambiguous_dispositions(retrieved,'retrieved answer')
        _require_numeric_support(value['answer'],answer_quotes,'answer')
        _require_disposition_support(value['answer'],answer_quotes,'answer')
    if value['status']=='ANSWERED' and requires_source_diversity(question):
        if not value['source_findings']:
            raise ValueError('a comparison answer requires source findings')
        requested=set(_requested_source_families(question))
        represented={source_type for source_type,_ in finding_sources}
        if len(requested)>=2 and not requested.issubset(represented):
            raise ValueError('comparison answer omitted a requested source family')
        if len(requested)<2 and len(finding_sources)<2:
            raise ValueError('a comparison answer requires at least two distinct sources')


def _context_citation(evidence: dict) -> dict:
    text=evidence['raw_text']
    hits=[(match.start(),match.end()) for term in evidence.get('_question_terms',[])
          if (match:=_term_pattern(term).search(text))]
    position,end=min(hits,default=(0,min(1,len(text))))
    start,end=statement_span(text,position,end)
    return citation(evidence,start,end,role='CONTEXT')


class ProjectQuestions:
    def __init__(self, db: Database, gateway: Gateway):self.db=db;self.gateway=gateway

    def ask(self, run: dict, question: str, workflow_index: dict | None = None) -> dict:
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise DomainError('Select a completed or partial analysis run before asking a question.',409)
        evidence=retrieve_evidence(self.db,run,question)
        conflicts=_workflow_index_status_conflicts(question,workflow_index)
        if conflicts:
            return {'run_id':run['id'],'question':question,'status':'INSUFFICIENT_EVIDENCE',
                    'answer':('The current workflow status cannot be established because the complete '
                              'project workflow index contains conflicting explicit statuses for '
                              +', '.join(conflicts)+'. Review the original source sections.'),
                    'citations':[],'source_findings':[],
                    'retrieved_count':len(evidence),'cached':False}
        if not evidence:
            return {'run_id':run['id'],'question':question,'status':'INSUFFICIENT_EVIDENCE',
                    'answer':'The analyzed project files do not contain enough matching evidence to answer this question.',
                    'citations':[],'source_findings':[],'retrieved_count':0,'cached':False}
        if self.gateway.s.provider=='mock':
            return {'run_id':run['id'],'question':question,'status':'MODEL_DISABLED',
                    'answer':'Mock mode retrieved possible source passages but did not generate an answer. Configure a live API or local model to answer from these files.',
                    'citations':[_context_citation(item) for item in evidence[:3]],
                    'source_findings':[],'retrieved_count':len(evidence),'cached':False}
        result=self.gateway.answer(run,question,evidence)
        validate_answer_model(result.data,evidence,question)
        by_id={item['evidence_id']:item for item in evidence}
        citations=[exact_quote(by_id[item['evidence_id']],item['quote']) for item in result.data['citations']]
        findings=[]
        for item in result.data['source_findings']:
            findings.append({
                'source_type':item['source_type'],'file_name':item['file_name'],
                'statement':item['statement'],
                'citations':[exact_quote(by_id[citation_item['evidence_id']],citation_item['quote'])
                             for citation_item in item['citations']],
            })
        return {'run_id':run['id'],'question':question,'status':result.data['status'],
                'answer':result.data['answer'],'citations':citations,'source_findings':findings,
                'retrieved_count':len(evidence),'cached':result.cached}
