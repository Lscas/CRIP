"""Bounded project-file retrieval and evidence-grounded answers."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.db import Database, DomainError
from app.verification import citation, exact_quote, statement_span
from contracts.runtime_rules import validate_schema

if TYPE_CHECKING:
    from app.gateway import Gateway

_WORD = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", re.I)
_STOP = frozenset({
    'a','an','and','are','as','at','be','by','can','do','does','for','from','how','i','in',
    'is','it','of','on','or','project','show','tell','that','the','this','to','was','were',
    'what','when','where','which','who','why','with','would',
})
_MAX_QUERY_TERMS = 10
_MAX_CANDIDATES = 160
_MAX_RESULTS = 8
_MAX_FRAGMENT_CHARS = 2600
_MAX_CONTEXT_CHARS = 18000


def question_terms(question: str) -> list[str]:
    terms=[]
    for value in _WORD.findall(question.casefold()):
        if value in _STOP or (len(value) < 2 and not value.isdigit()) or value in terms:
            continue
        terms.append(value)
    return terms[:_MAX_QUERY_TERMS]


def _like(value: str) -> str:
    return '%' + value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'


def _fts_query(terms: list[str]) -> str:
    return ' OR '.join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _candidate_rows(db: Database, run_id: str, terms: list[str]) -> list[dict]:
    if getattr(db,'evidence_search_available',False):
        return db.all('''SELECT e.payload,d.name AS file_name,
                                bm25(evidence_search,2,1.5,1) AS search_rank
                         FROM evidence_search
                         JOIN evidence e ON e.rowid=evidence_search.rowid
                         JOIN documents d ON d.id=e.document_id
                         WHERE evidence_search MATCH ? AND e.run_id=?
                           AND COALESCE(json_extract(e.payload,'$.content_basis'),'')!='MODEL_VISION_OUTPUT'
                           AND COALESCE(json_extract(e.payload,'$.extraction_method'),'')!='VISION'
                         ORDER BY search_rank,e.rowid LIMIT ?''',
                      [_fts_query(terms),run_id,_MAX_CANDIDATES])
    clauses=[];args=[]
    for term in terms:
        fields=["json_extract(e.payload,'$.raw_text')","d.name"]
        fields.extend(f"json_extract(e.payload,'$.locator.{key}')"
                      for key in ('section','sheet','page','paragraph'))
        clauses.extend(f"LOWER({field}) LIKE ? ESCAPE '\\'" for field in fields)
        args.extend([_like(term)]*len(fields))
    # FTS5 is optional.  The compatibility path favors complete results over
    # speed and deliberately does not truncate in row order.
    return db.all(f'''SELECT e.payload,d.name AS file_name FROM evidence e
                      JOIN documents d ON d.id=e.document_id
                      WHERE e.run_id=?
                        AND COALESCE(json_extract(e.payload,'$.content_basis'),'')!='MODEL_VISION_OUTPUT'
                        AND COALESCE(json_extract(e.payload,'$.extraction_method'),'')!='VISION'
                        AND ({' OR '.join(clauses)})''',[run_id,*args])


def _window(text: str, terms: list[str]) -> str:
    if len(text) <= _MAX_FRAGMENT_CHARS:
        return text
    folded=text.casefold();positions=[folded.find(term) for term in terms]
    anchor=min((position for position in positions if position >= 0),default=0)
    start=max(0,anchor-_MAX_FRAGMENT_CHARS//3)
    end=min(len(text),start+_MAX_FRAGMENT_CHARS)
    start=max(0,end-_MAX_FRAGMENT_CHARS)
    return text[start:end]


def retrieve_evidence(db: Database, run: dict, question: str) -> list[dict]:
    """Use run-scoped full-text candidates, then deterministic local ranking.

    SQLite FTS5 avoids another service or vector store.  Environments without
    FTS5 use a complete LIKE scan rather than silently losing later evidence.
    Prompt windows stay bounded while citations are checked against the full
    immutable evidence text.
    """
    terms=question_terms(question)
    if not terms:
        return []
    rows=_candidate_rows(db,run['id'],terms)
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
        searchable=' '.join((text,row['file_name'],locator_text)).casefold()
        matched=[term for term in terms if term in searchable]
        if not matched:continue
        score=sum(4 if any(char.isdigit() for char in term) else 2 for term in matched)
        score+=min(6,sum(min(3,searchable.count(term)) for term in matched))
        if all(term in searchable for term in terms):score+=8
        evidence={**evidence,'file_name':row['file_name'],'prompt_text':_window(text,matched),
                  '_question_terms':matched}
        ranked.append((score,len(matched),evidence['evidence_id'],evidence))
    ranked.sort(key=lambda item:(-item[0],-item[1],item[2]))
    selected=[];used=0
    for _,_,_,evidence in ranked:
        size=len(evidence['prompt_text'])
        if selected and used+size>_MAX_CONTEXT_CHARS:continue
        selected.append(evidence);used+=size
        if len(selected)>=_MAX_RESULTS:break
    return selected


def validate_answer_model(value: dict, evidence: list[dict]) -> None:
    validate_schema('project-answer',value)
    allowed={item['evidence_id']:item for item in evidence}
    if value['status']=='ANSWERED' and not value['citations']:
        raise ValueError('an answered response requires at least one citation')
    for item in value['citations']:
        source=allowed.get(item['evidence_id'])
        if source is None:raise ValueError('citation is outside the retrieved evidence scope')
        exact_quote(source,item['quote'])


def _context_citation(evidence: dict) -> dict:
    text=evidence['raw_text'];folded=text.casefold()
    position=min((folded.find(term) for term in evidence.get('_question_terms',[]) if term in folded),default=0)
    end=min(len(text),position+max(1,len(next((term for term in evidence.get('_question_terms',[]) if term in folded),''))))
    start,end=statement_span(text,position,end)
    return citation(evidence,start,end,role='CONTEXT')


class ProjectQuestions:
    def __init__(self, db: Database, gateway: Gateway):self.db=db;self.gateway=gateway

    def ask(self, run: dict, question: str) -> dict:
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise DomainError('Select a completed or partial analysis run before asking a question.',409)
        evidence=retrieve_evidence(self.db,run,question)
        if not evidence:
            return {'run_id':run['id'],'question':question,'status':'INSUFFICIENT_EVIDENCE',
                    'answer':'The analyzed project files do not contain enough matching evidence to answer this question.',
                    'citations':[],'retrieved_count':0,'cached':False}
        if self.gateway.s.provider=='mock':
            return {'run_id':run['id'],'question':question,'status':'MODEL_DISABLED',
                    'answer':'Mock mode retrieved possible source passages but did not generate an answer. Configure a live API or local model to answer from these files.',
                    'citations':[_context_citation(item) for item in evidence[:3]],
                    'retrieved_count':len(evidence),'cached':False}
        result=self.gateway.answer(run,question,evidence)
        validate_answer_model(result.data,evidence)
        by_id={item['evidence_id']:item for item in evidence}
        citations=[exact_quote(by_id[item['evidence_id']],item['quote']) for item in result.data['citations']]
        return {'run_id':run['id'],'question':question,'status':result.data['status'],
                'answer':result.data['answer'],'citations':citations,
                'retrieved_count':len(evidence),'cached':result.cached}
