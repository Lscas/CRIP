"""Real schema24 -> 25 upgrade rehearsal in temporary databases only."""
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


def _snapshot(connection):
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    values = {}
    for name in names:
        query = 'SELECT * FROM "' + name.replace('"', '""') + '"'
        if name == 'schema_migrations': query += ' WHERE version<=24'
        values[name] = sorted(json.dumps(list(row), ensure_ascii=True,
            default=lambda item: {'blob': item.hex()}) for row in connection.execute(query))
    indexes = [tuple(row) for row in connection.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name")]
    foreign_keys = {name: [tuple(row) for row in connection.execute(
        'PRAGMA foreign_key_list("' + name.replace('"', '""') + '")')] for name in names}
    columns = {name: [tuple(row) for row in connection.execute(
        'PRAGMA table_info("' + name.replace('"', '""') + '")')] for name in names}
    return values, indexes, foreign_keys, columns


def _historical_result(db,run,question):
    """Seed a real schema24 row without calling the current schema27 reader."""
    result=_public_result(run,question)
    raw=dumps(result);stamp=now();result_id=uid('QAR')
    normalized=' '.join(question.split());question_key=hashlib.sha256(normalized.encode()).hexdigest()
    db.execute('''INSERT INTO reference_results(
        id,result_key,project_id,run_id,snapshot_id,qa_version,question,question_key,
        status,answer_basis,provider,model,result_hash,result_json,review_status,
        review_version,review_event_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            result_id,hashlib.sha256(('schema24:'+result_id).encode()).hexdigest(),
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


def _historical_case(db,project,run,question,result_id,evaluation_id,item_id):
    stamp=now();case_id=uid('QACASE');question_key=hashlib.sha256(' '.join(question.split()).encode()).hexdigest()
    db.execute('''INSERT INTO reference_cases(
        id,source_key,project_id,run_id,snapshot_id,question,question_key,result_id,evaluation_id,
        evaluation_item_id,source_status,status,assignee,resolution,version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            case_id,hashlib.sha256(('schema24-case:'+case_id).encode()).hexdigest(),project['id'],run['id'],
            run['snapshot_id'],question,question_key,result_id,evaluation_id,item_id,'ANSWERED','OPEN','', '',0,stamp,stamp))
    db.execute('''INSERT INTO reference_case_events(id,case_id,actor,action,before_json,after_json,note,created_at)
                  VALUES(?,?,?,?,?,?,?,?)''',(
        uid('QACEVT'),case_id,'synthetic-migration','CREATED','{}',
        json.dumps({'status':'OPEN','result_id':result_id}), '',stamp))


@contextmanager
def _schema24(tmp_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(Database, '_install_reference_selector_v9', staticmethod(lambda _: None))
        patch.setattr(Database, '_install_reference_failure_execution', staticmethod(lambda _: None))
        patch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
        app = create_app(Settings(tmp_path / 'schema24', start_worker=False))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client:
            project = client.post('/api/projects', json={'name': 'Synthetic migration 25'}).json()
            db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
            question = 'What approved color applies?'
            result = _historical_result(db,run,question)
            for version in range(3, 9):
                evaluation_id, item_id = _evaluation_item(db, project, run, question, result_id=result)
                db.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
                           (f'literal-page-selector-{version}', evaluation_id))
            _historical_case(db,project,run,question,result,evaluation_id,item_id)
            assert db.one('SELECT MAX(version) AS v FROM schema_migrations')['v'] == 24
            assert db.one('SELECT COUNT(*) AS n FROM reference_result_citations')['n'] > 0
            yield db, evaluation_id


def test_schema25_preserves_all_old_values_indexes_foreign_keys_and_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(Database, '_install_reference_failure_execution', staticmethod(lambda _: None))
    monkeypatch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
    with _schema24(tmp_path, monkeypatch) as (old, evaluation_id):
        path = old.path
        with old.connect() as connection, sqlite3.connect(tmp_path / 'backup24.sqlite3') as backup:
            before = _snapshot(connection)
            connection.backup(backup)
    for _ in range(2):
        upgraded = Database(path)
        with upgraded.connect() as connection:
            assert _snapshot(connection) == before
            assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=25').fetchone()[0] == 1
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('SELECT COUNT(*) FROM model_calls').fetchone()[0] == 0
    upgraded.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
                     ('literal-page-selector-9', evaluation_id))
    for illegal in ('literal-page-selector-10', 'arbitrary-selector', ''):
        with pytest.raises(sqlite3.IntegrityError):
            upgraded.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?', (illegal, evaluation_id))
    with sqlite3.connect(tmp_path / 'backup24.sqlite3') as backup, \
            sqlite3.connect(tmp_path / 'restored24.sqlite3') as restored:
        backup.backup(restored)
        assert _snapshot(restored) == before
        assert restored.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 24
        with pytest.raises(sqlite3.IntegrityError):
            restored.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?', ('literal-page-selector-9', evaluation_id))


@pytest.mark.parametrize('interrupt', ['drop', 'rename', 'marker'])
def test_interrupted_migration_keeps_table_and_marker_atomic_and_can_restart(tmp_path, monkeypatch, interrupt):
    monkeypatch.setattr(Database, '_install_reference_failure_execution', staticmethod(lambda _: None))
    monkeypatch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
    with _schema24(tmp_path, monkeypatch) as (old, _):
        path = old.path
        with old.connect() as connection: before = _snapshot(connection)
    with sqlite3.connect(path) as connection:
        def authorizer(action, first, second, *_):
            denied = ((interrupt == 'drop' and action == sqlite3.SQLITE_DROP_TABLE and first == 'reference_evaluations')
                      or (interrupt == 'rename' and action == sqlite3.SQLITE_ALTER_TABLE and second == 'reference_evaluations_v9')
                      or (interrupt == 'marker' and action == sqlite3.SQLITE_INSERT and first == 'schema_migrations'))
            return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK
        connection.set_authorizer(authorizer)
        with pytest.raises(sqlite3.DatabaseError): Database._install_reference_selector_v9(connection)
        connection.set_authorizer(None)
        connection.rollback()
        assert _snapshot(connection) == before
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=25').fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='reference_evaluations_v9'").fetchone()[0] == 0
    upgraded = Database(path)
    with upgraded.connect() as connection:
        assert _snapshot(connection) == before
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=25').fetchone()[0] == 1
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
