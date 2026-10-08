"""Compare legacy and QA V2 retrieval on one immutable database without model calls."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.evidence_bundle import build_evidence_bundle
from scripts.benchmark_question_retrieval import (
    EXPECTATIONS,ReadOnlyDatabase,benchmark_database,compare,score_question)


def benchmark_v2_database(path:Path,questions:list[dict],run_id:str)->dict:
    database=ReadOnlyDatabase(path);before=path.stat();started=time.perf_counter()
    database.canonical_search_available=bool(database.one(
        "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='content_search'",
        required=False))
    run=database.one('SELECT * FROM runs WHERE id=?',(run_id,))
    calls_before=database.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    rows=[];conflicts=0
    for item in questions:
        bundle=build_evidence_bundle(database,run,item['question'])
        conflicts+=len(bundle.source_set.conflicts)
        evidence=[];seen=set()
        for block in bundle.blocks:
            for citation in block.citations:
                if citation.evidence_id in seen:continue
                seen.add(citation.evidence_id)
                evidence.append({'evidence_id':citation.evidence_id,'raw_text':citation.quote,
                                 'locator':{'section':citation.path,'paragraph':None,'sheet':None}})
        rows.append({'id':item['id'],**score_question(item['id'],evidence),
                     'bundle_bytes':bundle.byte_count,'source_conflicts':len(bundle.source_set.conflicts)})
    calls_after=database.one('SELECT COUNT(*) AS n FROM model_calls')['n'];after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise RuntimeError('benchmark database changed during read-only measurement')
    return {
        'label':'qa_v2','database_file':path.name,'run_id':run_id,
        'elapsed_ms':round((time.perf_counter()-started)*1000,3),
        'evidence_count':database.one('SELECT COUNT(*) AS n FROM evidence')['n'],
        'model_calls_before':calls_before,'model_calls_after':calls_after,'paid_calls_made':0,
        'effective_source_conflicts':conflicts,
        'questions_complete':sum(row['complete'] for row in rows),'questions_total':len(rows),
        'atoms_found':sum(row['atoms_found'] for row in rows),
        'atoms_total':sum(row['atoms_total'] for row in rows),'questions':rows,
    }


def main()->int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id')
    args=parser.parse_args()
    payload=json.loads(args.questions.read_text(encoding='utf-8'))
    questions=payload.get('questions');run_id=args.run_id or payload.get('live_analysis_run_id')
    if not isinstance(questions,list) or not questions or not isinstance(run_id,str):
        parser.error('question file is malformed')
    if [item.get('id') for item in questions]!=list(EXPECTATIONS):
        parser.error('question IDs do not match the locked benchmark')
    legacy=benchmark_database(args.database,questions,run_id,'legacy')
    candidate=benchmark_v2_database(args.database,questions,run_id)
    report=compare(legacy,candidate)
    report['version']='2.0';report['scope']='legacy_vs_qa_v2_retrieval_same_immutable_database'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({
        'output':str(args.output),'legacy_atoms':legacy['atoms_found'],
        'qa_v2_atoms':candidate['atoms_found'],
        'atom_delta':report['aggregate_delta']['atoms_found'],
        'legacy_complete':legacy['questions_complete'],
        'qa_v2_complete':candidate['questions_complete'],
        'provider_calls_made':0,'raw_evidence_in_report':False,
    },ensure_ascii=False,separators=(',',':')))
    return 0


if __name__=='__main__':raise SystemExit(main())
