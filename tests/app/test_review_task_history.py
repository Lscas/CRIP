"""Bounded lookup regressions for durable paid-task history."""
from __future__ import annotations

import time
import tracemalloc
import json

import pytest

from app.db import Database, now
from .conftest import upload


def _run(client, project):
    upload(client, project['id'], 'synthetic-history.txt', b'DEMO_MATERIAL|T-1|Synthetic|-')
    response = client.post(f'/api/projects/{project["id"]}/analysis-runs')
    assert response.status_code == 202, response.text
    return response.json()['id']


def _insert_calls(db, project_id, run_id, rows):
    with db.connect(True) as connection:
        connection.executemany('''INSERT INTO model_calls(
            id,project_id,run_id,task_key,model,state,reserved_units,actual_units,
            input_rate,output_rate,request_hash,response,error,created_at,updated_at)
            VALUES(?,?,?,?,'synthetic-model','SETTLED',1,1,'0','0','synthetic',?,?,?,?)''', rows)


def test_family_history_is_sql_bounded_and_keeps_exact_legacy_members(client, project):
    db = client.app.state.db
    run_id = _run(client, project)
    family = 'verify:' + 'a' * 64
    other = 'verify:' + 'a' * 63 + 'b'
    blob = '{"synthetic":"' + 'x' * 4096 + '"}'
    rows = [
        (f'CALL-noise-{index:05d}', project['id'], run_id, f'answer:{index:064x}', blob,
         None, f'2026-01-01T00:00:00.{index:06d}+00:00', f'2026-01-01T00:00:00.{index:06d}+00:00')
        for index in range(8501)
    ]
    rows.extend([
        ('CALL-base', project['id'], run_id, family, blob, None, '2026-01-02T00:00:00+00:00', '2026-01-02T00:00:00+00:00'),
        ('CALL-r1', project['id'], run_id, family + ':manual-requeue:1', None, None, '2026-01-02T00:00:01+00:00', '2026-01-02T00:00:01+00:00'),
        ('CALL-r2', project['id'], run_id, family + ':manual-requeue:2', blob, None, '2026-01-02T00:00:02+00:00', '2026-01-02T00:00:02+00:00'),
        ('CALL-r9', project['id'], run_id, family + ':manual-requeue:9', blob, None, '2026-01-02T00:00:09+00:00', '2026-01-02T00:00:09+00:00'),
        ('CALL-job', project['id'], run_id, family + ':job:' + 'c' * 24, blob, None, '2026-01-02T00:00:10+00:00', '2026-01-02T00:00:10+00:00'),
        ('CALL-bad-manual', project['id'], run_id, family + ':manual-requeue:0', blob, None, '2026-01-02T00:00:10.1+00:00', '2026-01-02T00:00:10.1+00:00'),
        ('CALL-bad-job', project['id'], run_id, family + ':job:' + 'not-a-legacy-job', blob, None, '2026-01-02T00:00:10.2+00:00', '2026-01-02T00:00:10.2+00:00'),
        ('CALL-answer-job', project['id'], run_id, 'answer:' + 'd' * 64 + ':job:' + 'c' * 24, blob, None, '2026-01-02T00:00:10.3+00:00', '2026-01-02T00:00:10.3+00:00'),
        ('CALL-other', project['id'], run_id, other, blob, None, '2026-01-02T00:00:11+00:00', '2026-01-02T00:00:11+00:00'),
    ])
    _insert_calls(db, project['id'], run_id, rows)

    trace = []
    with db.connect() as connection:
        connection.set_trace_callback(trace.append)
        direct = Database._family_calls(connection, run_id, family)
        connection.set_trace_callback(None)
    production_sql = next(statement for statement in trace if 'FROM model_calls' in statement)
    with db.connect() as connection:
        plan = '\n'.join(row['detail'] for row in connection.execute(
            'EXPLAIN QUERY PLAN ' + production_sql))

    tracemalloc.start()
    start = time.perf_counter()
    calls = db.family_calls(run_id, family)
    elapsed = time.perf_counter() - start
    _, bounded_peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    legacy_start = time.perf_counter()
    legacy_rows = db.all('SELECT id,task_key,state,actual_units,response FROM model_calls WHERE run_id=? ORDER BY created_at,id',
                         (run_id,))
    legacy_elapsed = time.perf_counter() - legacy_start
    _, legacy_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert [call['id'] for call in calls] == ['CALL-base', 'CALL-r1', 'CALL-r2', 'CALL-r9', 'CALL-job']
    assert calls == direct
    assert all('response' not in call for call in calls)
    assert [call['has_response'] for call in calls] == [True, False, True, True, True]
    assert len(legacy_rows) == 8510 and legacy_peak > bounded_peak * 5
    print('TASK_HISTORY_BENCHMARK='+json.dumps({
        'rows':len(legacy_rows),'new_seconds':round(elapsed,6),'new_peak_bytes':bounded_peak,
        'legacy_seconds':round(legacy_elapsed,6),'legacy_peak_bytes':legacy_peak,
    },sort_keys=True))
    with db.connect() as connection:
        indexes = {row['name'] for row in connection.execute("PRAGMA index_list('model_calls')")}
        columns = [row['name'] for row in connection.execute("PRAGMA index_info('ix_model_calls_run_task_key')")]
    assert 'ix_model_calls_run_task_key' in indexes and columns == ['run_id', 'task_key']
    assert plan.count('ix_model_calls_run_task_key') == 3


def test_task_history_index_upgrades_old_database_idempotently(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    Database(path)
    db = Database(path)
    project = db.create_project('Synthetic legacy index project')
    timestamp = now()
    with db.connect(True) as connection:
        connection.execute('''INSERT INTO runs(id,project_id,provider,snapshot_id,document_ids,status,
                          stage,message,created_at,started_epoch,deadline_epoch,coverage,capabilities,stop_requested)
                          VALUES('RUN-index-legacy',?,'mock','SNAP-index','[]','PAUSED','synthetic','',?,0,1,'{}','{}',0)''',
                          (project['id'], timestamp))
        connection.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
                          actual_units,input_rate,output_rate,request_hash,response,created_at,updated_at)
                          VALUES('CALL-index-legacy',?,'RUN-index-legacy','verify:legacy','mock','SETTLED',1,1,
                          '0','0','legacy-hash','{"legacy":true}',?,?)''',(project['id'],timestamp,timestamp))
    before = db.all('SELECT version,applied_at FROM schema_migrations ORDER BY version')
    old_call = db.one("SELECT * FROM model_calls WHERE id='CALL-index-legacy'")
    db.execute("DROP INDEX ix_model_calls_run_task_key")
    upgraded = Database(path)
    Database(path)
    with upgraded.connect() as connection:
        assert [row['name'] for row in connection.execute(
            "PRAGMA index_info('ix_model_calls_run_task_key')")] == ['run_id', 'task_key']
    assert upgraded.all('SELECT version,applied_at FROM schema_migrations ORDER BY version') == before
    assert upgraded.one("SELECT * FROM model_calls WHERE id='CALL-index-legacy'") == old_call


@pytest.mark.parametrize('ddl', [
    'CREATE UNIQUE INDEX ix_model_calls_run_task_key ON model_calls(run_id,task_key)',
    'CREATE INDEX ix_model_calls_run_task_key ON model_calls(task_key,run_id)',
    "CREATE INDEX ix_model_calls_run_task_key ON model_calls(run_id,task_key) WHERE state='SETTLED'",
    'CREATE TABLE index_shadow(value TEXT); CREATE INDEX ix_model_calls_run_task_key ON index_shadow(value)',
])
def test_task_history_index_fails_closed_on_same_name_drift(tmp_path, ddl):
    path = tmp_path / 'drift.sqlite3'
    db = Database(path)
    db.execute('DROP INDEX ix_model_calls_run_task_key')
    with db.connect(True) as connection:
        connection.executescript(ddl)
    with pytest.raises(RuntimeError, match='task-history index drift'):
        Database(path)


def test_schema28_preflight_still_rejects_damage_when_task_index_exists(tmp_path):
    path = tmp_path / 'damaged-schema28.sqlite3'
    db = Database(path)
    assert db.one("SELECT name FROM sqlite_master WHERE name='ix_model_calls_run_task_key'", required=False)
    db.execute('DROP TABLE reference_cases')
    with pytest.raises(RuntimeError):
        Database(path)
