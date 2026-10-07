"""Full synthetic schema25 preservation, backup and interrupted schema26 migration."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import Database,dumps,now,uid
from app.main import create_app
from app.reference_results import ReferenceResultStore
from app.settings import Settings
from .test_reference_cases import _evaluation_item
from .test_reference_results import _public_result,_saved_run


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _snapshot(connection, old_columns=None):
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    columns = {name: [tuple(row) for row in connection.execute(
        'PRAGMA table_info(' + _quoted(name) + ')')] for name in names}
    if old_columns is not None:
        assert set(columns) == set(old_columns)
    chosen = old_columns or columns
    values = {}
    for name, fields in chosen.items():
        query = 'SELECT ' + ','.join(_quoted(field[1]) for field in fields) + ' FROM ' + _quoted(name)
        if name == 'schema_migrations':
            query += ' WHERE version<=25'
        values[name] = sorted(json.dumps(list(row), ensure_ascii=True,
            default=lambda value: {'blob': value.hex()}) for row in connection.execute(query))
    indexes = [tuple(row) for row in connection.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' ORDER BY name")]
    foreign_keys = {name: [tuple(row) for row in connection.execute(
        'PRAGMA foreign_key_list(' + _quoted(name) + ')')] for name in names}
    unchanged_schema = [tuple(row) for row in connection.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_master
           WHERE type IN ('table','trigger','view') AND name NOT LIKE 'sqlite_%'
           AND name!='reference_evaluation_failures' ORDER BY type,name""")]
    return {'columns': columns, 'values': values, 'indexes': indexes,
            'foreign_keys': foreign_keys, 'unchanged_schema': unchanged_schema}


def _historical_result(db,run,question):
    """Seed a real schema25 legacy row without invoking the schema27 reader."""
    result=_public_result(run,question)
    raw=dumps(result);stamp=now();result_id=uid('QAR')
    normalized=' '.join(question.split());question_key=hashlib.sha256(normalized.encode()).hexdigest()
    db.execute('''INSERT INTO reference_results(
        id,result_key,project_id,run_id,snapshot_id,qa_version,question,question_key,
        status,answer_basis,provider,model,result_hash,result_json,review_status,
        review_version,review_event_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            result_id,hashlib.sha256(('schema25:'+result_id).encode()).hexdigest(),
            run['project_id'],run['id'],run['snapshot_id'],'3',question,question_key,
            result['status'],result['answer_basis'],'mock','mock-no-network',
            hashlib.sha256(raw.encode()).hexdigest(),raw,'PENDING',0,None,stamp,stamp))
    citations=ReferenceResultStore(db)._citations(run,result)
    for item in citations:
        db.execute('''INSERT INTO reference_result_citations(
            result_id,ordinal,claim_index,citation_index,citation_type,evidence_id,region_id,
            document_id,page_number,citation_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',(
                result_id,item['ordinal'],item['claim_index'],item['citation_index'],
                item['citation_type'],item['evidence_id'],item['region_id'],item['document_id'],
                item['page_number'],dumps(item['citation'])))
    assert citations
    return result_id


def _historical_case(db,project,run,question,result_id):
    stamp=now();case_id=uid('QACASE');question_key=hashlib.sha256(' '.join(question.split()).encode()).hexdigest()
    db.execute('''INSERT INTO reference_cases(
        id,source_key,project_id,run_id,snapshot_id,question,question_key,result_id,evaluation_id,
        evaluation_item_id,source_status,status,assignee,resolution,version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            case_id,hashlib.sha256(('schema25-case:'+case_id).encode()).hexdigest(),project['id'],run['id'],
            run['snapshot_id'],question,question_key,result_id,None,None,'ANSWERED','OPEN','', '',0,stamp,stamp))
    db.execute('''INSERT INTO reference_case_events(id,case_id,actor,action,before_json,after_json,note,created_at)
                  VALUES(?,?,?,?,?,?,?,?)''',(
        uid('QACEVT'),case_id,'synthetic-migration','CREATED','{}',
        json.dumps({'status':'OPEN','result_id':result_id}), '',stamp))


@contextmanager
def _schema25(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(Database, '_install_reference_failure_execution', staticmethod(lambda _: None))
        patch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
        app = create_app(Settings(tmp_path / 'schema25', start_worker=False))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client:
            project = client.post('/api/projects', json={'name': 'Synthetic migration26 project'}).json()
            db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
            question = 'What approved color applies?'
            result_id = _historical_result(db,run,question)
            _evaluation_item(db, project, run, question, result_id=result_id)
            evaluation_id, item_id = _evaluation_item(db, project, run, question)
            # Seed a historical row using only the real schema25 columns. The
            # new application reader is intentionally not used before migration.
            db.execute('UPDATE reference_evaluations SET profile_json=?',
                (json.dumps(client.app.state.reference_evaluations.profile(Settings(tmp_path))),))
            db.execute('''INSERT INTO reference_evaluation_failures(
                id,evaluation_id,item_id,code,stage,detail,created_at,execution_receipt_json)
                VALUES('QAEFAIL-OLD',?,?,'MODEL_OUTPUT_REJECTED','EXECUTION',
                       'Synthetic historical rejection','2026-10-04T00:00:00Z',NULL)''',
                (evaluation_id, item_id))
            _historical_case(db,project,run,question,result_id)
            assert db.one('SELECT MAX(version) AS v FROM schema_migrations')['v'] == 25
            assert db.all('PRAGMA foreign_key_check') == []
            assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_failures')['n'] == 1
            assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n'] == 1
            assert db.one('SELECT COUNT(*) AS n FROM reference_result_citations')['n'] > 0
            assert db.one('SELECT COUNT(*) AS n FROM reference_cases')['n'] == 1
            yield db


def _assert_preserved(connection, before):
    after = _snapshot(connection, before['columns'])
    assert after['values'] == before['values']
    assert after['indexes'] == before['indexes']
    assert after['foreign_keys'] == before['foreign_keys']
    assert after['unchanged_schema'] == before['unchanged_schema']
    for name, columns in before['columns'].items():
        if name == 'reference_evaluation_failures':
            assert after['columns'][name] == columns + [
                (len(columns), 'failure_execution_json', 'TEXT', 0, None, 0)]
        else:
            assert after['columns'][name] == columns
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert connection.execute(
        'SELECT COUNT(*) FROM reference_evaluation_failures WHERE failure_execution_json IS NOT NULL'
    ).fetchone()[0] == 0
    assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=26').fetchone()[0] == 1


def test_schema25_to_26_preserves_every_old_column_and_human_history_with_separate_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
    with _schema25(tmp_path, monkeypatch) as db:
        path = db.path
        with db.connect() as source, sqlite3.connect(tmp_path / 'backup25.sqlite3') as backup:
            before = _snapshot(source)
            source.backup(backup)
    for _ in range(2):
        upgraded = Database(path)
        with upgraded.connect() as connection:
            _assert_preserved(connection, before)
    # Restore to a distinct temporary file, never overwrite the working copy.
    with sqlite3.connect(tmp_path / 'backup25.sqlite3') as backup, \
            sqlite3.connect(tmp_path / 'restored25.sqlite3') as restored:
        backup.backup(restored)
        assert _snapshot(restored) == before
        assert restored.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 25
        assert restored.execute('PRAGMA foreign_key_check').fetchall() == []
        assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('interrupt', ['alter', 'marker'])
def test_schema26_interruption_rolls_back_column_and_marker_then_restarts(tmp_path, monkeypatch, interrupt):
    monkeypatch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
    with _schema25(tmp_path, monkeypatch) as db:
        path = db.path
        with db.connect() as connection:
            before = _snapshot(connection)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        def authorizer(action, first, second, *_):
            denied = ((interrupt == 'alter' and action == sqlite3.SQLITE_ALTER_TABLE
                       and second == 'reference_evaluation_failures')
                      or (interrupt == 'marker' and action == sqlite3.SQLITE_INSERT
                          and first == 'schema_migrations'))
            return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK
        connection.set_authorizer(authorizer)
        with pytest.raises(sqlite3.DatabaseError):
            Database._install_reference_failure_execution(connection)
        connection.set_authorizer(None)
        # The production installer, not test cleanup, must have rolled back.
        assert connection.in_transaction is False
        assert _snapshot(connection) == before
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=26').fetchone()[0] == 0
    upgraded = Database(path)
    with upgraded.connect() as connection:
        _assert_preserved(connection, before)
