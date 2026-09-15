"""Safety checks for the manual visual-task requeue script."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import Database, DomainError
from scripts.requeue_failed_visual_tasks import REQUEUE_REASON, main, requeue_failed_contract_tasks
from .conftest import upload


def _paused_run_with_visual_tasks(client, project):
    document = upload(client, project["id"], "drawing.pdf", b"not-a-real-pdf")
    run = client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    client.app.state.db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run["id"],))
    summary = {
        "warnings": [],
        "visual_tasks": [
            {"page": 1, "status": "FAILED_CONTRACT", "error": "InvalidModelOutput"},
            {"page": 2, "status": "VISION_EXTRACTED", "request_id": "existing"},
            {"page": 3, "status": "FAILED_RENDER", "error": "ValueError"},
        ],
    }
    client.app.state.db.execute(
        "INSERT INTO document_results VALUES(?,?,?,?)",
        (run["id"], document["document_id"], "PARTIAL", json.dumps(summary)),
    )
    return run["id"], document["document_id"]


def test_requeue_dry_run_then_apply_preserves_history_and_model_calls(client, project):
    run_id, document_id = _paused_run_with_visual_tasks(client, project)
    db = client.app.state.db
    before = db.one("SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run_id, document_id))["summary"]
    assert requeue_failed_contract_tasks(db, run_id) == {
        "run_id": run_id,
        "mode": "DRY_RUN",
        "requeued_count": 1,
        "tasks": [{"document_id": document_id, "page": 1, "billing_generation": 1}],
        "guarantees": [
            "只处理 FAILED_CONTRACT 的 visual_tasks",
            "未修改 model_calls 或预算账户",
            "每项重排均保留旧状态、错误、付费代次、时间和原因",
            "每次显式重排生成新的付费任务代次，累计最多三次",
            "未修改运行状态",
        ],
    }
    assert db.one("SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run_id, document_id))["summary"] == before

    result = requeue_failed_contract_tasks(db, run_id, apply=True)
    assert result["mode"] == "APPLIED" and result["requeued_count"] == 1
    stored = json.loads(db.one("SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run_id, document_id))["summary"])
    task = stored["visual_tasks"][0]
    assert task["status"] == "PENDING" and task["billing_generation"] == 1 and "error" not in task
    assert task["attempt_history"] == [{
        "status": "FAILED_CONTRACT", "error": "InvalidModelOutput",
        "billing_generation": 0,
        "recorded_at": task["requeued_at"], "reason": REQUEUE_REASON,
    }]
    assert stored["visual_tasks"][1]["status"] == "VISION_EXTRACTED"
    assert stored["visual_tasks"][2]["status"] == "FAILED_RENDER"
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE run_id=?", (run_id,))["n"] == 0
    assert db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] == "PAUSED"


def test_requeue_generation_is_bounded_and_preserves_prior_history(client, project):
    run_id, document_id = _paused_run_with_visual_tasks(client, project)
    db = client.app.state.db
    row = db.one("SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run_id, document_id))
    summary = json.loads(row["summary"])
    task = summary["visual_tasks"][0]
    task["billing_generation"] = 2
    task["attempt_history"] = [{"billing_generation": 0}, {"billing_generation": 1}]
    db.execute("UPDATE document_results SET summary=? WHERE run_id=? AND document_id=?",
               (json.dumps(summary), run_id, document_id))

    with pytest.raises(DomainError, match="三次累计调用上限"):
        requeue_failed_contract_tasks(db, run_id, apply=True)
    unchanged = json.loads(db.one(
        "SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run_id, document_id))["summary"])
    assert unchanged == summary


def test_requeue_accepts_every_runner_resumable_paused_state(client, project):
    run_id, _ = _paused_run_with_visual_tasks(client, project)
    db = client.app.state.db
    for status in ("PAUSED", "PAUSED_PROVIDER", "PAUSED_BUDGET", "INTERRUPTED"):
        db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        assert requeue_failed_contract_tasks(db, run_id)["requeued_count"] == 1


def test_requeue_rejects_nonresumable_run_and_unresolved_calls(client, project):
    run_id, _ = _paused_run_with_visual_tasks(client, project)
    db = client.app.state.db
    db.execute("UPDATE runs SET status='CANCELLED' WHERE id=?", (run_id,))
    with pytest.raises(DomainError, match="暂停"):
        requeue_failed_contract_tasks(db, run_id, apply=True)

    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run_id,))
    db.reserve(project["id"], run_id, "VISION-test", Decimal("1"), "model", "hash", Decimal("1"), Decimal("1"))
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run_id,))
    with pytest.raises(DomainError, match="待对账"):
        requeue_failed_contract_tasks(db, run_id, apply=True)


def test_partial_run_requires_explicit_atomic_reopen(client, project):
    run_id, _ = _paused_run_with_visual_tasks(client, project)
    db = client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run_id,))
    with pytest.raises(DomainError, match="reopen_partial"):
        requeue_failed_contract_tasks(db, run_id, apply=True)

    preview = requeue_failed_contract_tasks(db, run_id, reopen_partial=True)
    assert preview["requeued_count"] == 1
    assert db.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"] == "PARTIAL"
    applied = requeue_failed_contract_tasks(db, run_id, apply=True, reopen_partial=True)
    assert applied["requeued_count"] == 1 and any("PARTIAL" in value for value in applied["guarantees"])
    reopened = db.one("SELECT status,message FROM runs WHERE id=?", (run_id,))
    assert reopened["status"] == "PAUSED" and "明确重开" in reopened["message"]


@pytest.mark.parametrize("apply", [False, True])
def test_cli_wrong_data_directory_never_creates_database_or_wal(tmp_path, monkeypatch, capsys, apply):
    missing_data_dir = tmp_path / "does-not-exist"
    command = ["requeue_failed_visual_tasks.py", "--run-id", "RUN-missing", "--data-dir", str(missing_data_dir)]
    if apply:
        command.append("--apply")
    monkeypatch.setattr(sys, "argv", command)

    assert main() == 2
    assert not missing_data_dir.exists()
    assert "未创建任何文件" in capsys.readouterr().out


def test_cli_dry_run_opens_existing_database_without_wal_or_schema_write(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "existing"
    data_dir.mkdir()
    database_path = data_dir / "cirp.sqlite3"
    Database(database_path)  # Test setup creates the known-good database before the dry-run.
    before = database_path.read_bytes()
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(database_path) + suffix).exists()

    monkeypatch.setattr(sys, "argv", [
        "requeue_failed_visual_tasks.py", "--run-id", "RUN-absent", "--data-dir", str(data_dir),
    ])
    assert main() == 2

    assert database_path.read_bytes() == before
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(database_path) + suffix).exists()
    assert "未找到分析运行" in capsys.readouterr().out
