"""DEV-132: parse-only staging rebuild never mutates the source database or calls a provider."""
import json
import sqlite3

from app.db import Database,dumps,now
from app.settings import Settings
from app.uploads import Uploads
from scripts.reparse_staging_v2 import reparse_staging


def test_reparse_staging_adds_a_new_canonical_run_without_touching_source(tmp_path):
    data_dir=tmp_path/'source-data';settings=Settings(data_dir=data_dir,provider='mock',start_worker=False)
    source=Database(data_dir/'cirp.sqlite3');project=source.create_project('Synthetic staging test')
    upload=Uploads(source,settings).import_bytes(
        project['id'],'spec.txt',b'SECTION 01100\n1.04 Building G first floor: 4 months.')
    document_id=upload['document_id'];source_run='RUN-SOURCE-FIXTURE';timestamp=1.0
    source.execute('''INSERT INTO runs
        (id,project_id,provider,snapshot_id,document_ids,status,stage,message,created_at,
         started_epoch,deadline_epoch,coverage,capabilities,stop_requested)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (source_run,project['id'],'mock','SN-SOURCE',dumps([document_id]),'COMPLETED','done','',
         now(),timestamp,timestamp+3600,'{}',dumps({'local_workers':1})))
    staging=tmp_path/'candidate.sqlite3'
    with sqlite3.connect(source.path) as original,sqlite3.connect(staging) as candidate:
        original.backup(candidate)

    result=reparse_staging(staging,data_dir,source_run,local_workers=1,parser_timeout=60)

    assert result['status']=='COMPLETED' and result['provider_calls_made']==0
    assert result['model_calls_before']==result['model_calls_after']==0
    assert result['integrity_check']=='ok' and result['foreign_key_errors']==0
    assert result['canonical_build']['state']=='READY'
    assert result['canonical_build']['builder_version']=='canonical-graph-2'
    assert Database(staging).one('SELECT COUNT(*) AS n FROM evidence WHERE run_id=?',
                                 (result['run_id'],))['n']>0
    assert source.one('SELECT COUNT(*) AS n FROM evidence')['n']==0
    assert source.one('SELECT COUNT(*) AS n FROM runs')['n']==1


def test_reparse_staging_refuses_the_active_database(tmp_path):
    data_dir=tmp_path/'data';database=Database(data_dir/'cirp.sqlite3')
    try:
        reparse_staging(database.path,data_dir,'RUN-MISSING')
    except ValueError as exc:
        assert 'active source database' in str(exc)
    else:
        raise AssertionError('active database was not rejected')
