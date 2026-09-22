"""Measure complete-scan fallback versus local FTS5 question candidates; no API calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.db import Database
from app.questions import retrieve_evidence


TARGET = 'RFI 0042 response: Domestic water service pipe shall be 2 inch Type L copper.'
QUESTION = 'What does RFI 42 require for the water service pipe material and size?'
COMPARISON_QUESTION = 'Compare the specification water pipe requirements and RFI 42 response.'


def best_ms(load, iterations: int) -> float:
    best=float('inf')
    for _ in range(3):
        started=time.perf_counter()
        for _ in range(iterations):load()
        best=min(best,(time.perf_counter()-started)*1000/iterations)
    return best


def measure(rows: int, iterations: int) -> dict:
    with tempfile.TemporaryDirectory(prefix='cirp-question-search-') as temporary:
        database=Database(Path(temporary)/'benchmark.sqlite3')
        with database.connect(True) as connection:
            connection.execute("INSERT INTO projects VALUES('P-benchmark','Benchmark','now')")
            connection.execute("INSERT INTO documents VALUES('D-benchmark','P-benchmark','fixture.txt',1,'sha','object','now')")
            connection.execute('''INSERT INTO runs VALUES(
                'R-benchmark','P-benchmark','mock','SN-benchmark','["D-benchmark"]',
                'COMPLETED','DONE','','now',0,1,'{}','{}',0)''')
            values=[]
            for index in range(1,rows+1):
                text=TARGET if index==rows else f'General water coordination note {index}.'
                payload=json.dumps({'evidence_id':f'EV-{index}','project_id':'P-benchmark',
                                    'document_id':'D-benchmark','raw_text':text,
                                    'locator':{'section':f'Section {index}'}})
                values.append((f'E-{index}','R-benchmark','P-benchmark','D-benchmark',
                               payload,'EXTRACTED',None,''))
            started=time.perf_counter()
            connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',values)
            index_ms=(time.perf_counter()-started)*1000
        run={'id':'R-benchmark'}

        def fallback():
            database.evidence_search_available=False
            return retrieve_evidence(database,run,QUESTION)

        def full_text():
            database.evidence_search_available=True
            return retrieve_evidence(database,run,QUESTION)

        def full_text_comparison():
            database.evidence_search_available=True
            return retrieve_evidence(database,run,COMPARISON_QUESTION)

        scan_rows=fallback();fts_rows=full_text();comparison_rows=full_text_comparison()
        result={
            'fixture_rows':rows,
            'iterations':iterations,
            'index_and_insert_ms':round(index_ms,1),
            'complete_scan_results':len(scan_rows),
            'fts_results':len(fts_rows),
            'complete_scan_ms_per_query':round(best_ms(fallback,iterations),3),
            'fts_ms_per_query':round(best_ms(full_text,iterations),3),
            'fts_comparison_ms_per_query':round(best_ms(full_text_comparison,iterations),3),
            'complete_scan_found_exact':bool(scan_rows and scan_rows[0]['evidence_id']==f'EV-{rows}'),
            'fts_found_exact':bool(fts_rows and fts_rows[0]['evidence_id']==f'EV-{rows}'),
            'fts_comparison_found_exact':any(
                item['evidence_id']==f'EV-{rows}' for item in comparison_rows),
            'paid_api_calls':0,
        }
        result['query_speedup']=round(
            result['complete_scan_ms_per_query']/max(result['fts_ms_per_query'],0.000001),2)
        return result


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--rows',type=int,default=12_000)
    parser.add_argument('--iterations',type=int,default=20)
    args=parser.parse_args()
    if not 161<=args.rows<=100_000:parser.error('rows must be 161..100000')
    if not 1<=args.iterations<=1_000:parser.error('iterations must be 1..1000')
    print(json.dumps(measure(args.rows,args.iterations),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
