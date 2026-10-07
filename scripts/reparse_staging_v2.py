"""Reparse stored objects into a new run inside a staging database only.

This command never opens the active source database and never processes visual
tasks, extraction tasks, verification tasks or provider calls.  It exists to
measure parser and Canonical graph changes before any production cutover.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.canonical import BUILDER_VERSION,CanonicalGraph
from app.db import Database,dumps,now
from app.parsers import PARSER_VERSION
from app.runner import Runner
from app.settings import Settings
from app.uploads import Uploads


def _run_id(source_run_id: str) -> str:
    digest=hashlib.sha256(f'{source_run_id}|{PARSER_VERSION}|{BUILDER_VERSION}'.encode()).hexdigest()
    return 'RUN-REPARSE-'+digest[:20]


def _source_fingerprint(documents: list[dict]) -> str:
    values=[(row['id'],row['sha256']) for row in documents]
    return 'SN-REPARSE-'+hashlib.sha256(dumps(values).encode()).hexdigest()[:24]


def reparse_staging(database_path: Path,object_data_dir: Path,source_run_id: str,
                    *,local_workers: int=2,parser_timeout: int=900) -> dict:
    database_path=database_path.resolve();object_data_dir=object_data_dir.resolve()
    active=(object_data_dir/'cirp.sqlite3').resolve()
    if database_path==active:
        raise ValueError('Refusing to reparse the active source database; provide a separate staging copy')
    if not database_path.is_file():raise ValueError('Staging database does not exist')
    if not (object_data_dir/'objects').is_dir():raise ValueError('Stored object directory does not exist')
    if local_workers not in (1,2,4):raise ValueError('local_workers must be 1, 2 or 4')
    if not 30<=parser_timeout<=3600:raise ValueError('parser_timeout must be 30-3600 seconds')

    database=Database(database_path)
    source=database.one('SELECT * FROM runs WHERE id=?',(source_run_id,))
    document_ids=json.loads(source['document_ids'])
    documents=[]
    for document_id in document_ids:
        row=database.one('SELECT id,project_id,sha256,object_key FROM documents WHERE id=?',(document_id,))
        if row['project_id']!=source['project_id']:raise ValueError('Document project scope mismatch')
        object_path=(object_data_dir/'objects'/row['object_key']).resolve()
        if (object_data_dir/'objects').resolve() not in object_path.parents or not object_path.is_file():
            raise ValueError('Stored source object is unavailable')
        documents.append(row)

    run_id=_run_id(source_run_id);snapshot=_source_fingerprint(documents)
    before_calls=database.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    existing=database.one('SELECT id,status FROM runs WHERE id=?',(run_id,),False)
    if existing and existing['status'] in {'COMPLETED','PARTIAL'}:
        build=database.one("SELECT state,builder_version FROM canonical_builds WHERE run_id=?",(run_id,),False)
        if build and build['state']=='READY' and build['builder_version']==BUILDER_VERSION:
            return _summary(database,run_id,before_calls,before_calls,reused=True)
    elif not existing:
        timestamp=time.time()
        capabilities={'local_workers':local_workers,'parse_only_staging':True,
                      'provider_calls_allowed':False,'parser_version':PARSER_VERSION}
        with database.connect(True) as connection:
            connection.execute('''INSERT INTO runs
                (id,project_id,provider,snapshot_id,document_ids,status,stage,message,created_at,
                 started_epoch,deadline_epoch,coverage,capabilities,stop_requested)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
                (run_id,source['project_id'],'mock',snapshot,dumps(document_ids),'RUNNING',
                 '解析文件','Staging parse-only rebuild; no provider work is permitted.',now(),
                 timestamp,timestamp+max(3600,parser_timeout*max(1,len(document_ids))),
                 '{}',dumps(capabilities)))
    else:
        database.execute("UPDATE runs SET status='RUNNING',stage='解析文件',stop_requested=0 WHERE id=?",
                         (run_id,))

    settings=Settings(data_dir=object_data_dir,provider='mock',vision_enabled=False,
                      live_enabled=False,start_worker=False,parser_timeout=parser_timeout)
    uploads=Uploads(database,settings)
    runner=Runner(database,settings,uploads,None)
    with tempfile.TemporaryDirectory(prefix='cirp-reparse-') as temporary:
        runner.parse_dir=Path(temporary)
        run=runner.get(run_id)
        runner.parse_documents(run)

    completed=database.one('SELECT COUNT(*) AS n FROM document_results WHERE run_id=?',(run_id,))['n']
    if completed!=len(document_ids):raise RuntimeError('Not every staging document produced a parse result')
    run=runner.get(run_id)
    canonical=CanonicalGraph(database).rebuild_run(run)
    if not canonical['validation']['ok']:raise RuntimeError('Canonical graph validation failed')
    statuses=database.all('SELECT status,COUNT(*) AS n FROM document_results WHERE run_id=? GROUP BY status',
                          (run_id,))
    state='COMPLETED' if statuses and all(row['status']=='SUCCESS' for row in statuses) else 'PARTIAL'
    evidence_count=database.one('SELECT COUNT(*) AS n FROM evidence WHERE run_id=?',(run_id,))['n']
    coverage={'documents_total':len(document_ids),'documents_completed':completed,
              'fragments_total':evidence_count,'parse_only_staging':True,
              'provider_calls_made':0,'document_statuses':{row['status']:row['n'] for row in statuses}}
    database.execute('UPDATE runs SET status=?,stage=?,message=?,coverage=? WHERE id=?',
                     (state,'STAGING_PARSE_ONLY_COMPLETE',
                      'Staging parser and Canonical graph rebuild complete; no provider work was run.',
                      dumps(coverage),run_id))
    after_calls=database.one('SELECT COUNT(*) AS n FROM model_calls')['n']
    if after_calls!=before_calls:raise RuntimeError('Model-call ledger changed during parse-only staging rebuild')
    return _summary(database,run_id,before_calls,after_calls,reused=False)


def _summary(database: Database,run_id: str,before_calls: int,after_calls: int,*,reused: bool) -> dict:
    run=database.one('SELECT status,coverage FROM runs WHERE id=?',(run_id,))
    build=database.one('''SELECT state,builder_version,node_count,edge_count,identifier_count,span_count
                          FROM canonical_builds WHERE run_id=?''',(run_id,))
    with database.connect() as connection:
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=len(connection.execute('PRAGMA foreign_key_check').fetchall())
    return {
        'run_id':run_id,'status':run['status'],'parser_version':PARSER_VERSION,
        'canonical_build':build,'coverage':json.loads(run['coverage'] or '{}'),
        'model_calls_before':before_calls,'model_calls_after':after_calls,
        'provider_calls_made':0,'integrity_check':integrity,
        'foreign_key_errors':foreign_keys,'reused':reused,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--object-data-dir',type=Path,required=True)
    parser.add_argument('--source-run-id',required=True)
    parser.add_argument('--local-workers',type=int,choices=(1,2,4),default=2)
    parser.add_argument('--parser-timeout',type=int,default=900)
    args=parser.parse_args()
    result=reparse_staging(args.database,args.object_data_dir,args.source_run_id,
                           local_workers=args.local_workers,parser_timeout=args.parser_timeout)
    print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    return 0


if __name__=='__main__':raise SystemExit(main())
