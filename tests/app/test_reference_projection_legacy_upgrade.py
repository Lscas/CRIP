"""Offline cross-layer preservation of a real named-v9 legacy result through schema27."""
from __future__ import annotations

import shutil
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.reference_results import ReferenceResultStore
from app.reference_text_profiles import profile
from app.settings import ROOT, Settings
from tests.app.test_reference_v9_input_auth import QUESTION, _real_result


_OLD_COLUMNS = (
    "id", "result_key", "project_id", "run_id", "snapshot_id", "qa_version",
    "question", "question_key", "status", "answer_basis", "provider", "model",
    "result_hash", "result_json", "review_status", "review_version",
    "review_event_id", "created_at", "updated_at",
)


@pytest.fixture(autouse=True)
def _isolate_schema28_from_the_historical_26_to_27_fixture(monkeypatch):
    """Keep this module's live source and reopened target at the schema27 boundary."""
    import app.db as db_module

    monkeypatch.setattr(db_module, "install_projection_human_review", lambda _connection: None)


def _old_row(connection, result_id):
    columns = ",".join(_OLD_COLUMNS)
    types = ",".join(f"typeof({name})" for name in _OLD_COLUMNS)
    return tuple(connection.execute(
        f"SELECT rowid,{columns},{types} FROM reference_results WHERE id=?", (result_id,)
    ).fetchone())


def _ledger(db):
    columns = [row["name"] for row in db.all("PRAGMA table_info(model_calls)")]
    types = ",".join(f"typeof({name})" for name in columns)
    return db.all(f"SELECT rowid,*,{types} FROM model_calls ORDER BY rowid")


def _clone_data_directory(source, target):
    target.mkdir(parents=True)
    for item in source.iterdir():
        if item.name in {"cirp.sqlite3", "cirp.sqlite3-wal", "cirp.sqlite3-shm", "server.lock"}:
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)
    source_connection = sqlite3.connect(source / "cirp.sqlite3")
    target_connection = sqlite3.connect(target / "cirp.sqlite3")
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _rebuild_clone_as_exact_schema26(path):
    """Test-fixture-only reversal of the cloned parent table; never production code."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        legacy_sql = (ROOT / "migrations/011_reference_results.sql").read_text(encoding="utf-8")
        create = legacy_sql[legacy_sql.index("CREATE TABLE"):].split(";", 1)[0]
        create = create.replace(
            "CREATE TABLE IF NOT EXISTS reference_results",
            "CREATE TABLE reference_results__legacy_upgrade",
            1,
        )
        columns = ",".join(_OLD_COLUMNS)
        connection.execute(create)
        connection.execute(
            f"INSERT INTO reference_results__legacy_upgrade (rowid,{columns}) "
            f"SELECT rowid,{columns} FROM reference_results ORDER BY rowid"
        )
        connection.execute("DROP TABLE reference_results")
        connection.execute("ALTER TABLE reference_results__legacy_upgrade RENAME TO reference_results")
        indexes = legacy_sql.split(";")[1:3]
        for index in indexes:
            connection.execute(index)
        connection.execute("DELETE FROM schema_migrations WHERE version=27")
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.close()


def _assert_exact_schema26(path):
    connection = sqlite3.connect(path)
    try:
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(reference_results)"))
        assert columns == _OLD_COLUMNS
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=11").fetchone()
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone()
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 26
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_real_named_v9_legacy_record_survives_actual_schema26_to_27_database_reopen(
        client, project, tmp_path, monkeypatch):
    """A synthetic record, not a historical binary replay, exercises real v9 persistence."""
    source_db, run, _document, sent, value = _real_result(client, project)
    source_store = ReferenceResultStore(source_db, client.app.state.uploads)
    saved = source_store.save(run, QUESTION, value, "deepseek", profile("FLASH_NONE")["text_model"])
    fixed_time = "2026-10-06T00:00:00+00:00"
    import app.reference_results as reference_results_module
    monkeypatch.setattr(reference_results_module, "now", lambda: fixed_time)
    source_row = source_db.one("SELECT * FROM reference_results WHERE id=?", (saved["result_id"],))
    source_raw = sqlite3.connect(source_db.path)
    try:
        before_old = _old_row(source_raw, saved["result_id"])
        assert source_raw.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 27
        assert not source_raw.execute("SELECT 1 FROM schema_migrations WHERE version=28").fetchone()
    finally:
        source_raw.close()
    before_get = source_store.get(saved["result_id"])
    before_list = source_store.list(project["id"], run["id"])
    before_export = source_store.export(project["id"], run["id"])
    before_history = source_store.history(saved["result_id"])
    before_ledger = _ledger(source_db)
    before_sent = list(sent)

    target_data = tmp_path / "legacy-upgrade"
    _clone_data_directory(source_db.path.parent, target_data)
    _rebuild_clone_as_exact_schema26(target_data / "cirp.sqlite3")
    _assert_exact_schema26(target_data / "cirp.sqlite3")
    raw_legacy = sqlite3.connect(target_data / "cirp.sqlite3")
    try:
        assert _old_row(raw_legacy, saved["result_id"]) == before_old
    finally:
        raw_legacy.close()

    app = create_app(Settings(target_data, start_worker=False))
    target_http = []
    def fail_target_model_call(*_args, **_kwargs):
        raise AssertionError("schema migration/read must not invoke target gateway")
    monkeypatch.setattr(app.state.gateway, "_request_timed", fail_target_model_call)
    app.state.gateway.client.close()
    app.state.gateway.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: target_http.append(request) or (_ for _ in ()).throw(
            AssertionError("schema migration/read must not send target HTTP"))))
    with TestClient(app, headers={"X-CIRP-Client": "browser"}) as upgraded_client:
        upgraded_db = upgraded_client.app.state.db
        upgraded_store = upgraded_client.app.state.reference_results
        assert upgraded_db.one("SELECT MAX(version) AS version FROM schema_migrations")["version"] == 27
        assert not upgraded_db.one(
            "SELECT 1 AS found FROM schema_migrations WHERE version=28", required=False
        )
        upgraded_row = upgraded_db.one("SELECT * FROM reference_results WHERE id=?", (saved["result_id"],))
        assert upgraded_row["result_kind"] == "REFERENCE_QA_RESULT"
        raw_upgraded = sqlite3.connect(upgraded_db.path)
        try:
            assert _old_row(raw_upgraded, saved["result_id"]) == before_old
        finally:
            raw_upgraded.close()
        assert upgraded_store.get(saved["result_id"]) == before_get
        assert upgraded_store.list(project["id"], run["id"]) == before_list
        assert upgraded_store.export(project["id"], run["id"]) == before_export
        assert upgraded_store.history(saved["result_id"]) == before_history
        assert upgraded_store.authenticate_saved_result(run, upgraded_row)[0]["question"] == QUESTION
        assert upgraded_client.get(f"/api/reference-results/{saved['result_id']}").json() == before_get
        assert upgraded_client.get(
            f"/api/projects/{project['id']}/reference-results", params={"run_id": run["id"]}
        ).json() == before_list
        assert upgraded_client.get(
            f"/api/projects/{project['id']}/reference-results/export.json", params={"run_id": run["id"]}
        ).json() == before_export
        assert upgraded_client.get(f"/api/reference-results/{saved['result_id']}/history").json() == before_history
        assert _ledger(upgraded_db) == before_ledger
        assert target_http == []
    assert sent == before_sent
    assert target_http == []
    assert source_row["result_json"] == before_old[14]
    assert source_row["result_hash"] == before_old[13]
    assert source_row["result_key"] == before_old[2]
