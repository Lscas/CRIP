"""Offline commitment identity and atomic-settlement tests; no provider calls."""
import copy
from decimal import Decimal
import json
import sqlite3

import pytest

from app.db import Database, DomainError
from app.reference_input_commitment import VERSION, require_ledger_profile, sha256
from app.reference_text_profiles import profile
from .test_gateway_budget import context  # reusable isolated database fixture


def receipt():
    return {'cached': False, 'initial_evidence_manifest_sha256': '1' * 64,
            'profile_neutral_input_sha256': '6' * 64,
            'ordered_evidence_manifest_sha256': '2' * 64, 'model_call_id': 'CALL-synthetic',
            'request_hash': '3' * 64, 'max_output_tokens': 2600, 'inference_mode': 'disabled',
            'evidence_inputs': [{'evidence_id': 'E1', 'layout_sha256': '4' * 64, 'layout_bytes': 12},
                                {'evidence_id': 'E2', 'layout_sha256': '5' * 64, 'layout_bytes': 34}]}


def test_canonical_commitment_ignores_object_order_and_replay_flag_but_not_array_order():
    route = profile('FLASH_NONE')
    value = receipt()
    expected = sha256(route, value)
    reordered = dict(reversed(list(value.items())))
    reordered['cached'] = True
    reordered['evidence_inputs'] = [dict(reversed(list(row.items()))) for row in value['evidence_inputs']]
    assert sha256(dict(reversed(list(route.items()))), reordered) == expected
    reordered['evidence_inputs'].reverse()
    assert sha256(route, reordered) != expected


@pytest.mark.parametrize('field', ['initial_evidence_manifest_sha256', 'ordered_evidence_manifest_sha256',
                                 'profile_neutral_input_sha256', 'request_hash', 'max_output_tokens',
                                 'inference_mode', 'layout_sha256', 'layout_bytes'])
def test_commitment_binds_each_material_input_field(field):
    value = receipt()
    changed = copy.deepcopy(value)
    if field.startswith('layout_'):
        changed['evidence_inputs'][0][field] = 99 if field.endswith('bytes') else '9' * 64
    else:
        changed[field] = 8000 if field == 'max_output_tokens' else 'different'
    assert sha256(profile('FLASH_NONE'), changed) != sha256(profile('FLASH_NONE'), value)


def test_commitment_rejects_noncanonical_profile_tuple():
    changed = profile('FLASH_NONE')
    changed['max_output_tokens'] = 8000
    with pytest.raises(DomainError, match='canonical'):
        sha256(changed, receipt())


@pytest.mark.parametrize(('version', 'digest', 'profile_id', 'valid'), [
    (None, None, None, True),
    (VERSION, 'a' * 64, 'FLASH_NONE', True),
    (None, None, 'FLASH_NONE', False),
    (VERSION, 'a' * 64, None, False),
    (VERSION, None, None, False),
    (VERSION, None, 'FLASH_NONE', False),
    (None, 'a' * 64, None, False),
    (None, 'a' * 64, 'FLASH_NONE', False),
    ('unknown-version', 'a' * 64, 'FLASH_NONE', False),
    (VERSION, 'invalid', 'FLASH_NONE', False),
])
def test_ledger_not_external_profile_decides_legacy_vs_named(version, digest, profile_id, valid):
    call = {'reference_input_commitment_version': version, 'reference_input_commitment_sha256': digest}
    if valid:
        require_ledger_profile(call, profile_id)
    else:
        with pytest.raises(DomainError) as error:
            require_ledger_profile(call, profile_id)
        assert error.value.code == 409


def reserve(context):
    db, run, _ = context
    call_id = db.reserve(run['project_id'], run['id'], 'synthetic-commitment', Decimal('0'),
                         'deepseek-flash', 'f' * 64, Decimal('0'), Decimal('0'))
    return db, call_id


@pytest.mark.parametrize('failure', [False, True])
def test_commitment_settles_atomically_and_repeat_requires_identical_commitment(context, failure):
    db, call_id = reserve(context)
    terminal = ({'diagnostic': {'kind': 'CONTRACT_ERROR', 'class': 'PROJECT_EVIDENCE_DECISION'}}
                if failure else {'response': {'synthetic': True}})
    usage = {'prompt_tokens': 1, 'completion_tokens': 1}
    commitment = (VERSION, 'a' * 64)
    for _ in range(2):
        db.finalize_model_call(call_id, Decimal('0'), usage, 'synthetic-provider',
                               reference_input_commitment=commitment, **terminal)
    row = db.one('SELECT * FROM model_calls WHERE id=?', (call_id,))
    assert row['state'] == ('SETTLED_ERROR' if failure else 'SETTLED')
    assert row['reference_input_commitment_version'] == VERSION
    assert row['reference_input_commitment_sha256'] == 'a' * 64
    for changed in (None, (VERSION, 'b' * 64)):
        with pytest.raises(DomainError, match='重复终态'):
            db.finalize_model_call(call_id, Decimal('0'), usage, 'synthetic-provider',
                                   reference_input_commitment=changed, **terminal)
    assert db.one('SELECT * FROM model_calls WHERE id=?', (call_id,)) == row


def test_failed_cache_write_rolls_back_settlement_and_commitment_together(context):
    db, call_id = reserve(context)
    with db.connect(True) as connection:
        connection.execute("""CREATE TRIGGER reject_commitment_cache BEFORE INSERT ON cache
                              BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic failure'):
        db.finalize_model_call(call_id, Decimal('0'), {'prompt_tokens': 1}, None,
                               response={'synthetic': True}, cache_key='synthetic-cache',
                               reference_input_commitment=(VERSION, 'a' * 64))
    row = db.one('SELECT * FROM model_calls WHERE id=?', (call_id,))
    assert row['state'] == 'RESERVED' and row['actual_units'] is None and row['response'] is None
    assert row['reference_input_commitment_version'] is None
    assert row['reference_input_commitment_sha256'] is None
    assert db.one('SELECT key FROM cache WHERE key=?', ('synthetic-cache',), False) is None


def test_legacy_settlement_retains_null_commitment(context):
    db, call_id = reserve(context)
    db.finalize_model_call(call_id, Decimal('0'), {'prompt_tokens': 1}, None, response={'legacy': True})
    row = db.one('SELECT * FROM model_calls WHERE id=?', (call_id,))
    assert row['state'] == 'SETTLED'
    assert row['reference_input_commitment_version'] is None
    assert row['reference_input_commitment_sha256'] is None


@pytest.mark.parametrize('partial', [False, True])
def test_additive_migration_preserves_legacy_rows_and_recovers_partial_install(partial):
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)')
        connection.execute('CREATE TABLE model_calls(id TEXT PRIMARY KEY, state TEXT)')
        connection.execute("INSERT INTO model_calls VALUES('CALL-legacy','SETTLED')")
        if partial:
            connection.execute('ALTER TABLE model_calls ADD COLUMN reference_input_commitment_version TEXT')
        for _ in range(2):
            Database._install_reference_input_commitment(connection)
        row = dict(connection.execute('SELECT * FROM model_calls').fetchone())
        assert row == {'id': 'CALL-legacy', 'state': 'SETTLED',
                       'reference_input_commitment_version': None, 'reference_input_commitment_sha256': None}
        assert connection.execute('SELECT count(*) FROM schema_migrations WHERE version=24').fetchone()[0] == 1


def test_full_schema23_upgrade_preserves_old_values_and_backup_restores_old_schema(tmp_path, monkeypatch):
    """Exercise real initializer + SQLite backup without touching a runtime DB."""
    # This historical rehearsal specifically verifies 23 -> 24. Isolate later
    # migrations here; their own tests exercise the current initializer.
    monkeypatch.setattr(Database, '_install_reference_selector_v9', staticmethod(lambda _: None))
    monkeypatch.setattr(Database, '_install_reference_failure_execution', staticmethod(lambda _: None))
    monkeypatch.setattr(Database, '_install_reference_projection_results', staticmethod(lambda _: None))
    path = tmp_path / 'synthetic-schema23.sqlite3'
    with monkeypatch.context() as patch:
        patch.setattr(Database, '_install_reference_input_commitment', staticmethod(lambda _: None))
        old = Database(path)
    project = old.create_project('Synthetic migration rehearsal')
    with old.connect(True) as connection:
        connection.execute('''INSERT INTO runs
            (id,project_id,provider,snapshot_id,document_ids,status,stage,created_at,
             started_epoch,deadline_epoch,capabilities)
            VALUES(?,?,'mock-no-network','SN-synthetic','[]','PARTIAL','DONE','synthetic',0,1,'{}')''',
            ('RUN-synthetic', project['id']))
        connection.execute('INSERT INTO budget_accounts VALUES(?,300000000,7,1)', (project['id'],))
        connection.execute('''INSERT INTO records
            (id,run_id,project_id,kind,envelope,review_version,logical_key)
            VALUES('REC-synthetic','RUN-synthetic',?,'MATERIAL','{"synthetic":"reviewed"}',3,'synthetic')''',
            (project['id'],))
        connection.execute('''INSERT INTO review_events VALUES
            ('REV-synthetic','REC-synthetic','local-user','ACCEPT','{}','{}','Human note','synthetic')''')
        for index, state in enumerate(('SETTLED', 'SETTLED_ERROR', 'RESERVED', 'UNKNOWN')):
            connection.execute('''INSERT INTO model_calls
                (id,project_id,run_id,task_key,model,state,reserved_units,actual_units,
                 input_rate,output_rate,request_hash,usage,response,error,created_at,updated_at)
                VALUES(?,?,'RUN-synthetic',?,'synthetic-model',?,1,?,'0','0',?,'{}',?,?,
                       'synthetic','synthetic')''',
                (f'CALL-synthetic-{index}', project['id'], f'synthetic-{index}', state,
                 1 if state.startswith('SETTLED') else None, str(index) * 64,
                 '{"synthetic":true}' if state == 'SETTLED' else None,
                 '{"kind":"CONTRACT_ERROR"}' if state == 'SETTLED_ERROR' else None))

    def snapshot(connection, columns=None):
        # Compare common old columns; new NULL columns are intentionally additive.
        if columns is None:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            columns = {name: [row[1] for row in connection.execute(
                'PRAGMA table_info("' + name.replace('"', '""') + '")')] for name in names}
        values = {}
        for name, fields in columns.items():
            quote = lambda value: '"' + value.replace('"', '""') + '"'
            query = 'SELECT ' + ','.join(map(quote, fields)) + ' FROM ' + quote(name)
            if name == 'schema_migrations':
                query += ' WHERE version<=23'
            values[name] = sorted(json.dumps(list(row), ensure_ascii=True,
                default=lambda value: {'sqlite_blob_hex': value.hex()}) for row in connection.execute(query))
        return columns, values

    backup_path = tmp_path / 'before-schema24.sqlite3'
    with old.connect() as source, sqlite3.connect(backup_path) as backup:
        assert source.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 23
        columns, before = snapshot(source)
        assert 'reference_input_commitment_version' not in columns['model_calls']
        source.backup(backup)

    upgraded = Database(path)
    for _ in range(2):
        with upgraded.connect() as connection:
            assert snapshot(connection, columns)[1] == before
            assert connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 24
            assert connection.execute('''SELECT COUNT(*) FROM model_calls WHERE
                reference_input_commitment_version IS NOT NULL OR
                reference_input_commitment_sha256 IS NOT NULL''').fetchone()[0] == 0
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        upgraded = Database(path)

    # Restore into a separate file, never overwrite the upgraded database.
    restored_path = tmp_path / 'restored-schema23.sqlite3'
    with sqlite3.connect(backup_path) as backup, sqlite3.connect(restored_path) as restored:
        backup.backup(restored)
        restored_columns, restored_values = snapshot(restored)
        assert restored_columns == columns and restored_values == before
        assert restored.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0] == 23
        assert restored.execute('PRAGMA foreign_key_check').fetchall() == []
        assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
