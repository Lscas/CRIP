"""Build a validated canonical-graph staging database without changing the source database."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.canonical import BUILDER_VERSION,CanonicalGraph
from app.db import Database,dumps


def _resolved_distinct(source: Path,output: Path) -> tuple[Path,Path]:
    source=source.resolve();output=output.resolve()
    if source==output:raise ValueError('Source and staging database paths must be different')
    if not source.is_file():raise ValueError('Source database does not exist')
    output.parent.mkdir(parents=True,exist_ok=True)
    return source,output


def rebuild(source: Path,output: Path,*,replace: bool=False) -> dict:
    source,output=_resolved_distinct(source,output)
    if output.exists() and not replace:raise ValueError('Staging database already exists; use --replace explicitly')
    descriptor,temp_name=tempfile.mkstemp(prefix=output.name+'.building-',suffix='.sqlite3',dir=output.parent)
    os.close(descriptor);temp=Path(temp_name)
    try:
        # SQLite backup supplies one consistent read snapshot and never writes the source.
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src:
            with closing(sqlite3.connect(temp)) as dst:src.backup(dst)
        db=Database(temp)
        with db.connect(True) as connection:
            connection.execute('DELETE FROM content_nodes')
            connection.execute('DELETE FROM canonical_builds')
        runs=db.all('SELECT id FROM runs ORDER BY created_at,id')
        results=[]
        for row in runs:
            if db.one('SELECT 1 FROM document_results WHERE run_id=?',(row['id'],),False):
                results.append(CanonicalGraph(db).rebuild_run(row['id']))
        with db.connect() as connection:
            integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
            foreign_keys=[tuple(item) for item in connection.execute('PRAGMA foreign_key_check').fetchall()]
        failures=[item['run_id'] for item in results if not item['validation']['ok']]
        if integrity!='ok' or foreign_keys or failures:
            raise RuntimeError('Canonical staging validation failed')
        summary={'builder_version':BUILDER_VERSION,'source_database':str(source),
                 'staging_database':str(output),'run_count':len(results),
                 'integrity_check':integrity,'foreign_key_errors':len(foreign_keys),
                 'runs':[{'run_id':item['run_id'],**item['validation']['counts']}
                         for item in results]}
        with db.connect() as connection:connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        os.replace(temp,output)
        return summary
    finally:
        for item in (temp,Path(str(temp)+'-wal'),Path(str(temp)+'-shm')):
            item.unlink(missing_ok=True)


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--replace',action='store_true')
    args=parser.parse_args()
    try:result=rebuild(args.source,args.output,replace=args.replace)
    except (OSError,sqlite3.Error,ValueError,RuntimeError) as exc:
        parser.exit(2,str(exc)+'\n')
    print(dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
