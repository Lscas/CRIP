"""Measure full versus workflow-only parser-summary reads; never calls a model API."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.workflows import WORKFLOW_SUMMARY_SQL_PATHS,projected_workflow_summary


def fixture(pages: int) -> str:
    value={'document_type':'EMAIL',
           'workflow_contexts':[{'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':None}],
           'workflow_references':[],
           'email_content':{'current_body_chars':120,'quoted_history_chars':20},
           'email_thread':{'message_key':'MSG-'+'a'*24,'parent_message_key':None,'reference_keys':[]},
           'pages':[{'page':number,'status':'TEXT_EXTRACTED','payload':'x'*400}
                    for number in range(1,pages+1)],
           'geometry_summaries':[{'payload':'y'*400} for _ in range(max(1,pages//4))]}
    return json.dumps(value,separators=(',',':'))


def best_ms(load,iterations: int) -> float:
    best=float('inf')
    for _ in range(3):
        started=time.perf_counter()
        for _ in range(iterations):load()
        best=min(best,(time.perf_counter()-started)*1000)
    return best


def measure(pages: int,iterations: int) -> dict:
    database=sqlite3.connect(':memory:');database.execute('CREATE TABLE results(summary TEXT NOT NULL)')
    database.execute('INSERT INTO results VALUES(?)',(fixture(pages),))
    full_query='SELECT summary FROM results'
    projected_query=f'SELECT json_extract(summary,{WORKFLOW_SUMMARY_SQL_PATHS}) FROM results'
    full=database.execute(full_query).fetchone()[0]
    projected=database.execute(projected_query).fetchone()[0]
    load_full=lambda:json.loads(database.execute(full_query).fetchone()[0])
    load_projected=lambda:projected_workflow_summary(database.execute(projected_query).fetchone()[0])
    full_ms=best_ms(load_full,iterations);projected_ms=best_ms(load_projected,iterations)
    database.close()
    return {'fixture_pages':pages,'iterations':iterations,'full_bytes':len(full.encode()),
            'projected_bytes':len(projected.encode()),
            'byte_reduction_percent':round((1-len(projected)/len(full))*100,2),
            'full_query_decode_ms':round(full_ms,1),'projected_query_decode_ms':round(projected_ms,1),
            'speedup':round(full_ms/projected_ms,2),'paid_api_calls':0}


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--pages',type=int,default=2000)
    parser.add_argument('--iterations',type=int,default=300)
    args=parser.parse_args()
    if not 1<=args.pages<=10_000:parser.error('pages must be 1..10000')
    if not 1<=args.iterations<=10_000:parser.error('iterations must be 1..10000')
    print(json.dumps(measure(args.pages,args.iterations),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
