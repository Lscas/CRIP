import json
import sqlite3

import pytest

from scripts.audit_reference_context import ReadSnapshot,audit
from app.page_selector import COMPLETE_SELECTOR_VERSION,SELECTOR_VERSION
from .test_page_selector import _raw_run


def test_context_audit_distinguishes_selected_rows_from_actual_input_without_exposing_text(client,project):
    fragments=[{'page':1,'sheet':'A5.01','text':f'Private source clause {i} '+('text '*100)} for i in range(100)]
    db,run,_=_raw_run(client,project,fragments)
    report=audit(db,run,[{'id':'Q1','question':'What applies to Sheet A5.01?'}])
    legacy=report['records'][0]['selectors'][SELECTOR_VERSION]
    current=report['records'][0]['selectors'][COMPLETE_SELECTOR_VERSION]
    assert legacy['selected_rows_omitted_from_input']>0
    assert current['selected_rows_omitted_from_input']==current['input_rows_clipped']==0
    assert current['input_rows']==100 and current['source_bytes']==current['input_source_bytes']
    assert current['every_alias_grouped_once'] is True
    assert 'Private source clause' not in json.dumps(report)
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_read_snapshot_includes_wal_and_rejects_writes(tmp_path):
    path=tmp_path/'source.sqlite3'
    with sqlite3.connect(path) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE sample(value TEXT)')
        writer.execute("INSERT INTO sample VALUES('committed WAL row')")
        writer.commit()
        reader=ReadSnapshot(path)
        try:
            assert reader.one('SELECT value FROM sample')['value']=='committed WAL row'
            with pytest.raises(sqlite3.OperationalError):reader.one("DELETE FROM sample")
            writer.execute("INSERT INTO sample VALUES('later row')");writer.commit()
            assert len(reader.all('SELECT value FROM sample'))==1
        finally:reader.close()
