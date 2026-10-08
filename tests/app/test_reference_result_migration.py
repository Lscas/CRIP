"""Offline tests for the formal schema26 -> schema27 reference-results upgrade."""
from __future__ import annotations

import sqlite3

import pytest

from app.db import Database
from app import reference_result_migration as migration


_SEEDED_TABLES = (
    "projects", "runs", "documents", "reference_evaluations", "reference_results",
    "reference_result_citations", "reference_result_review_events", "reference_evaluation_items",
    "reference_evaluation_failures", "reference_evaluation_jobs", "reference_evaluation_adjudications",
    "reference_evaluation_adjudication_events", "reference_cases", "reference_case_followups",
    "reference_case_events", "reference_case_attachments",
)


def _legacy_database(tmp_path, monkeypatch) -> Database:
    """Build a genuine schema26 database through Database, not a copied DDL fixture."""
    path = tmp_path / "schema26.sqlite3"
    monkeypatch.setattr(Database, "_install_reference_projection_results", staticmethod(lambda _c: None))
    db = Database(path)
    monkeypatch.undo()
    with db.connect() as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=26").fetchone()
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone()
    return db


def _seed(connection):
    h, now = "a" * 64, "2026-10-06T00:00:00Z"
    connection.execute("INSERT INTO projects VALUES('P1','migration test',?)", (now,))
    connection.execute("INSERT INTO runs VALUES('R1','P1','mock','S1','[]','PARTIAL','DONE','',?,0,0,'{}','{}',0)", (now,))
    connection.execute("INSERT INTO documents VALUES('D1','P1','synthetic.pdf',1,?,'x',?)", (h, now))
    connection.executemany("INSERT INTO reference_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("RR1", "key1", "P1", "R1", "S1", "3", "alpha?", h, "ANSWERED", "LEGACY", "mock", "model", h, "{}", "ACCEPTED", 2, "REV1", now, now),
        ("RR2", "key2", "P1", "R1", "S1", "3", "bravo?", h, "CANNOT_ANSWER", "LEGACY", "mock", "model", h, "{}", "NOT_APPLICABLE", 0, None, now, now),
    ])
    connection.execute("INSERT INTO reference_result_citations VALUES('RR1',0,0,0,'TEXT','EV1',NULL,'D1',1,'{}')")
    connection.execute("INSERT INTO reference_result_citations VALUES('RR1',1,0,1,'IMAGE_REGION',NULL,'V1','D1',1,'{}')")
    connection.execute("INSERT INTO reference_result_review_events VALUES('REV1','RR1','human','ACCEPTED','{}','{}','ok',?)", (now,))
    connection.execute("INSERT INTO reference_evaluations VALUES('EV1','P1','R1','S1','eval',?,'{}',?,?, 'literal-page-selector-9')", (h, now, now))
    connection.execute("INSERT INTO reference_evaluation_items VALUES('EI1','EV1',0,'alpha?',?,'RR1',?,?)", (h, now, now))
    connection.execute("INSERT INTO reference_evaluation_failures VALUES('FAIL1','EV1','EI1','MODEL_OUTPUT_REJECTED','EXECUTION','synthetic',?,NULL,NULL)", (now,))
    connection.execute("INSERT INTO reference_evaluation_jobs VALUES('JOB1','EV1','P1','COMPLETED','COMPLETED',0,1,1,0,NULL,?,?,?,NULL,?)", (now, now, now, now))
    connection.execute("INSERT INTO reference_evaluation_adjudications VALUES('EI1','EV1','PARTIAL',0,'synthetic',1,'AE1',?,?)", (now, now))
    connection.execute("INSERT INTO reference_evaluation_adjudication_events VALUES('AE1','EV1','EI1','human','{}','{}',?)", (now,))
    connection.execute("INSERT INTO reference_cases VALUES('C1','src','P1','R1','S1','alpha?',?,'RR1',NULL,NULL,'ANSWERED','OPEN','','',0,?,?)", (h, now, now))
    connection.execute("INSERT INTO reference_case_followups VALUES('F1','C1','R1','S1','RR1','ANSWERED','{}',?,?)", (h, now))
    connection.execute("INSERT INTO reference_case_events VALUES('CE1','C1','human','CREATED','{}','{}','',?)", (now,))
    connection.execute("INSERT INTO reference_case_attachments VALUES('C1','D1',?)", (now,))
    connection.commit()


def _snapshot(connection):
    def rows(table):
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        typed = ",".join(f"typeof({name})" for name in columns)
        return [tuple(row) for row in connection.execute(f"SELECT rowid,*,{typed} FROM {table} ORDER BY rowid")]
    nonempty = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        if connection.execute(f"SELECT 1 FROM {row[0]} LIMIT 1").fetchone()
    }
    return {
        "graph": {name: rows(name) for name in _SEEDED_TABLES},
        "migrations": [tuple(row) for row in connection.execute(
            "SELECT rowid,*,typeof(version),typeof(applied_at) FROM schema_migrations ORDER BY rowid"
        )],
        "nonempty": nonempty,
        "result_sql": connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reference_results'").fetchone()[0],
        "foreign_keys": connection.execute("PRAGMA foreign_key_check").fetchall(),
    }


class _FaultConnection:
    """Real SQLite delegation with a fault at one verified migration node."""
    def __init__(self, connection, token=None, *, commit=False, mutate_staging=False, fk_fault=False):
        self._connection = connection
        self._token = token
        self._commit = commit
        self._mutate_staging = mutate_staging
        self._fk_fault = fk_fault
        self._mutated = False

    @property
    def in_transaction(self):
        return self._connection.in_transaction

    def execute(self, sql, parameters=()):
        if self._token and self._token in " ".join(sql.split()):
            raise sqlite3.OperationalError("injected migration node failure")
        if self._fk_fault and " ".join(sql.split()) == "PRAGMA foreign_key_check":
            return type("_Cursor", (), {"fetchall": lambda _: [("reference_results", 1, "projects", 0)]})()
        result = self._connection.execute(sql, parameters)
        if self._mutate_staging and not self._mutated and sql.startswith("INSERT INTO reference_results__migration27"):
            self._mutated = True
            self._connection.execute("UPDATE reference_results__migration27 SET answer_basis='tampered'")
        return result

    def commit(self):
        if self._commit:
            raise sqlite3.OperationalError("injected commit-before-durable failure")
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()


def _seeded_legacy(tmp_path, monkeypatch):
    db = _legacy_database(tmp_path, monkeypatch)
    with db.connect() as connection:
        _seed(connection)
    return db


def test_database_reopens_real_schema26_as_27_preserving_rich_history_and_rowids(tmp_path, monkeypatch):
    db = _legacy_database(tmp_path, monkeypatch)
    with db.connect() as connection:
        _seed(connection)
        connection.execute("UPDATE reference_results SET rowid=17 WHERE id='RR2'")
        connection.commit()
        before = _snapshot(connection)
        assert set(_SEEDED_TABLES) <= before["nonempty"]
    Database(db.path)
    with db.connect() as connection:
        after = _snapshot(connection)
        old_result = before["graph"]["reference_results"]
        legacy = [tuple(row) for row in connection.execute(
            "SELECT rowid,id,result_key,project_id,run_id,snapshot_id,qa_version,question,question_key,status,answer_basis,provider,model,result_hash,result_json,review_status,review_version,review_event_id,created_at,updated_at,"
            "typeof(id),typeof(result_key),typeof(project_id),typeof(run_id),typeof(snapshot_id),typeof(qa_version),typeof(question),typeof(question_key),typeof(status),typeof(answer_basis),typeof(provider),typeof(model),typeof(result_hash),typeof(result_json),typeof(review_status),typeof(review_version),typeof(review_event_id),typeof(created_at),typeof(updated_at) FROM reference_results ORDER BY rowid"
        )]
        assert legacy == old_result
        for table in _SEEDED_TABLES:
            if table != "reference_results":
                assert after["graph"][table] == before["graph"][table]
        # Schema28 follows the completed schema27 upgrade; both additive
        # markers are new, while every historical marker remains identical.
        assert after["migrations"][:-2] == before["migrations"]
        assert after["migrations"][-2][1:] == (27, after["migrations"][-2][2], "integer", "text")
        assert after["migrations"][-1][1:] == (28, after["migrations"][-1][2], "integer", "text")
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=27").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=28").fetchone()[0] == 1
        assert {row[0] for row in connection.execute("SELECT result_kind FROM reference_results")} == {"REFERENCE_QA_RESULT"}
        assert after["foreign_keys"] == []
    Database(db.path)


@pytest.mark.parametrize("mutation", ["missing_project_index", "missing_run_index", "trigger"])
def test_preflight_rejects_schema26_drift_before_011_can_repair_it(tmp_path, monkeypatch, mutation):
    db = _legacy_database(tmp_path, monkeypatch)
    with db.connect() as connection:
        if mutation == "trigger":
            connection.execute("CREATE TRIGGER rr_drift AFTER INSERT ON reference_results BEGIN SELECT 1; END")
        else:
            connection.execute("DROP INDEX ix_reference_results_project" if mutation == "missing_project_index" else "DROP INDEX ix_reference_results_run")
        connection.commit()
    with pytest.raises(RuntimeError):
        Database(db.path)
    with db.connect() as connection:
        if mutation == "trigger":
            assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='rr_drift'").fetchone()
        else:
            name = "ix_reference_results_project" if mutation == "missing_project_index" else "ix_reference_results_run"
            assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()


@pytest.mark.parametrize("mutation", ["missing_project_index", "missing_run_index", "trigger", "staging", "missing11", "fk"])
def test_preflight_rejects_marked_schema27_drift_before_011(tmp_path, mutation):
    db = Database(tmp_path / "schema27.sqlite3")
    with db.connect() as connection:
        if mutation == "trigger":
            connection.execute("CREATE TRIGGER rr_drift AFTER INSERT ON reference_results BEGIN SELECT 1; END")
        elif mutation == "staging":
            connection.execute("CREATE TABLE reference_results__migration27(x)")
        elif mutation == "missing11":
            connection.execute("DELETE FROM schema_migrations WHERE version=11")
            assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone()
            assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone()
        elif mutation == "fk":
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("INSERT INTO reference_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("BAD", "bad", "NO_PROJECT", "NO_RUN", "S1", "3", "broken?", "b" * 64, "REFERENCE_QA_RESULT", "ANSWERED", "x", "m", "m", "h" * 64, "{}", "PENDING", 0, None, "t", "t"))
            connection.execute("PRAGMA foreign_keys=ON")
        else:
            connection.execute("DROP INDEX ix_reference_results_project" if mutation == "missing_project_index" else "DROP INDEX ix_reference_results_run")
        connection.commit()
    with pytest.raises(RuntimeError):
        Database(db.path)


def test_preflight_rejects_schema26_result_kind_lookalike_without_27_and_preserves_database(tmp_path, monkeypatch):
    db = _seeded_legacy(tmp_path, monkeypatch)
    with db.connect() as connection:
        connection.execute("ALTER TABLE reference_results ADD COLUMN result_kind TEXT")
        connection.commit()
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone()
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone()
        before = _snapshot(connection)
    with pytest.raises(RuntimeError, match="schema26"):
        Database(db.path)
    with db.connect() as connection:
        assert _snapshot(connection) == before


def test_preflight_rejects_markerless_partial_reference_results_state(tmp_path):
    path = tmp_path / "partial.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE reference_results(x)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="markerless"):
        Database(path)


def test_schema27_default_and_projection_constraints(tmp_path):
    db = Database(tmp_path / "constraints.sqlite3")
    with db.connect() as connection:
        now, h = "t", "b" * 64
        connection.execute("INSERT INTO projects VALUES('P1','test',?)", (now,))
        connection.execute("INSERT INTO runs VALUES('R1','P1','mock','S1','[]','PARTIAL','DONE','',?,0,0,'{}','{}',0)", (now,))
        legacy = ("RR3", "key3", "P1", "R1", "S1", "3", "charlie?", h, "ANSWERED", "LEGACY", "mock", "model", h, "{}", "PENDING", 0, None, now, now)
        connection.execute("INSERT INTO reference_results(id,result_key,project_id,run_id,snapshot_id,qa_version,question,question_key,status,answer_basis,provider,model,result_hash,result_json,review_status,review_version,review_event_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", legacy)
        assert connection.execute("SELECT result_kind FROM reference_results WHERE id='RR3'").fetchone()[0] == "REFERENCE_QA_RESULT"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE reference_results SET result_kind='PROJECTION_LOOP_OUTCOME' WHERE id='RR3'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO reference_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("RR4", "key4", "P1", "R1", "S1", "3", "delta?", "c" * 64, "PROJECTION_LOOP_OUTCOME", "REVIEW_REQUIRED", "x", "m", "model", "d" * 64, "{}", "ACCEPTED", 0, None, now, now))


@pytest.mark.parametrize("token", [
    "DROP TABLE reference_results",
    "ALTER TABLE reference_results__migration27 RENAME TO reference_results",
    "CREATE INDEX ix_reference_results_project",
    "CREATE INDEX ix_reference_results_run",
    "INSERT INTO schema_migrations",
])
def test_named_migration_nodes_roll_back_rich_legacy_graph_and_retry(tmp_path, monkeypatch, token):
    db = _seeded_legacy(tmp_path, monkeypatch)
    with db.connect() as connection:
        before = _snapshot(connection)
        with pytest.raises(sqlite3.OperationalError):
            migration.install_reference_projection_results(_FaultConnection(connection, token))
        assert _snapshot(connection) == before
        migration.install_reference_projection_results(connection)


@pytest.mark.parametrize("kind", ["commit", "shape", "foreign_key_check"])
def test_precommit_verification_and_commit_failures_roll_back_and_retry(tmp_path, monkeypatch, kind):
    db = _seeded_legacy(tmp_path, monkeypatch)
    with db.connect() as connection:
        before = _snapshot(connection)
        proxy = _FaultConnection(connection, commit=kind == "commit", fk_fault=kind == "foreign_key_check")
        if kind == "shape":
            real_shape = migration._shape
            monkeypatch.setattr(migration, "_shape", lambda c, version: False if version == 27 else real_shape(c, version))
        with pytest.raises((sqlite3.OperationalError, RuntimeError)):
            migration.install_reference_projection_results(proxy)
        assert _snapshot(connection) == before
        if kind == "shape":
            monkeypatch.setattr(migration, "_shape", real_shape)
        migration.install_reference_projection_results(connection)


def test_copy_tamper_is_rejected_without_losing_rich_history_and_retry(tmp_path, monkeypatch):
    db = _seeded_legacy(tmp_path, monkeypatch)
    with db.connect() as connection:
        before = _snapshot(connection)
        with pytest.raises(RuntimeError, match="copied legacy values inconsistently"):
            migration.install_reference_projection_results(_FaultConnection(connection, mutate_staging=True))
        assert _snapshot(connection) == before
        migration.install_reference_projection_results(connection)


@pytest.mark.parametrize("replace", [
    ("'REFERENCE_QA_RESULT'", "'reference_qa_result'"),
    ("DEFAULT 'REFERENCE_QA_RESULT'", "DEFAULT 'REFERENCE_QA_result'"),
    ("'REVIEW_REQUIRED'", "'review_required'"),
])
def test_schema27_literal_default_and_check_drift_are_rejected_then_exact_restore_passes(tmp_path, replace):
    db = Database(tmp_path / "shape.sqlite3")
    with db.connect() as connection:
        source = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reference_results'").fetchone()[0]
        indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='reference_results' AND sql IS NOT NULL")]
        mutated = source.replace(*replace)
        assert replace[0] in source and mutated != source
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE reference_results")
        connection.execute(mutated)
        for index in indexes:
            connection.execute(index)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.commit()
    with pytest.raises(RuntimeError):
        Database(db.path)
    with db.connect() as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE reference_results")
        connection.execute(source)
        for index in indexes:
            connection.execute(index)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.commit()
    Database(db.path)


def test_install_preserves_sparse_rowids_and_is_idempotent(tmp_path, monkeypatch):
    db = _seeded_legacy(tmp_path, monkeypatch)
    with db.connect() as connection:
        connection.execute("UPDATE reference_results SET rowid=17 WHERE id='RR2'")
        connection.commit()
        before = [tuple(row) for row in connection.execute("SELECT rowid,id FROM reference_results ORDER BY rowid")]
        migration.install_reference_projection_results(connection)
        assert [tuple(row) for row in connection.execute("SELECT rowid,id FROM reference_results ORDER BY rowid")] == before
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=27").fetchone()[0] == 1
        migration.install_reference_projection_results(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=27").fetchone()[0] == 1
