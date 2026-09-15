"""Safety checks for the manual extraction-failure requeue script."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.db import DomainError, paid_task_key
from scripts.requeue_failed_extraction_tasks import (
    CONTRACT_EVIDENCE_ERROR,
    HISTORY_KEY,
    INPUT_BUDGET_EVIDENCE_ERROR,
    REQUEUE_REASON,
    requeue_failed_extraction_tasks,
)
from .conftest import upload


def _paused_run_with_evidence(client, project, *, error: str = CONTRACT_EVIDENCE_ERROR):
    document = upload(client, project["id"], "spec.txt", b"source")
    run = client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    db = client.app.state.db
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run["id"],))
    evidence = json.loads(Path("examples/evidence.json").read_text(encoding="utf-8"))[0]
    evidence.update(
        evidence_id="EV-extraction-requeue",
        tenant_id="local",
        project_id=project["id"],
        input_snapshot_id=run["snapshot_id"],
        document_id=document["document_id"],
        raw_text="bounded synthetic fixture",
        extraction_method="TEXT_LAYER",
    )
    storage_id = run["id"] + ":" + evidence["evidence_id"]
    db.execute(
        "INSERT INTO evidence(id,run_id,project_id,document_id,payload,status,error) VALUES(?,?,?,?,?,'NEEDS_REVIEW',?)",
        (storage_id, run["id"], project["id"], document["document_id"], json.dumps(evidence), error),
    )
    db.execute(
        "INSERT INTO document_results VALUES(?,?,?,?)",
        (run["id"], document["document_id"], "PARTIAL", json.dumps({"warnings": [], "visual_tasks": []})),
    )
    return db, run, document["document_id"], storage_id, evidence["evidence_id"]


def _settled_contract_error(db, run, task_key: str, *, exception: str = "ValueError") -> str:
    previous = db.one("SELECT status,stop_requested FROM runs WHERE id=?", (run["id"],))
    db.execute("UPDATE runs SET status='RUNNING',stop_requested=0 WHERE id=?", (run["id"],))
    call_id = db.reserve(
        run["project_id"], run["id"], task_key, Decimal("0.1"), "model", "hash-" + task_key,
        Decimal("1"), Decimal("2"),
    )
    db.finalize_model_call(
        call_id, Decimal("0.01"), {"prompt_tokens": 10, "completion_tokens": 20}, None,
        diagnostic={"kind": "CONTRACT_ERROR", "class": "EXTRACTION_RESULT", "exception": exception},
    )
    db.execute("UPDATE runs SET status=?,stop_requested=? WHERE id=?",
               (previous["status"], previous["stop_requested"], run["id"]))
    return call_id


def test_contract_dry_run_then_apply_preserves_call_and_adds_explicit_generation(client, project):
    db, run, document_id, storage_id, family = _paused_run_with_evidence(client, project)
    call_id = _settled_contract_error(db, run, family)
    before_call = db.one("SELECT * FROM model_calls WHERE id=?", (call_id,))
    before_cost = db.cost(project["id"])

    preview = requeue_failed_extraction_tasks(db, run["id"])
    assert preview["mode"] == "DRY_RUN" and preview["requeued_count"] == 1
    assert preview["tasks"] == [{
        "document_id": document_id,
        "page_number": 1,
        "extraction_method": "TEXT_LAYER",
        "failure_type": "BILLED_CONTRACT_ERROR",
        "current_generation": 0,
        "next_generation": 1,
        "billable_retry": True,
    }]
    assert db.one("SELECT status,error FROM evidence WHERE id=?", (storage_id,)) == {
        "status": "NEEDS_REVIEW", "error": CONTRACT_EVIDENCE_ERROR,
    }
    assert db.one("SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],))["n"] == 0

    applied = requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert applied["mode"] == "APPLIED" and applied["tasks"] == preview["tasks"]
    assert db.one("SELECT status,error FROM evidence WHERE id=?", (storage_id,)) == {
        "status": "PENDING", "error": "",
    }
    assert db.one("SELECT * FROM model_calls WHERE id=?", (call_id,)) == before_call
    assert db.cost(project["id"]) == before_cost
    assert db.authorized_generation(run["id"], family) == 1
    event = db.one("SELECT call_id,payload FROM call_reconciliation_events WHERE run_id=?", (run["id"],))
    payload = json.loads(event["payload"])
    assert event["call_id"] == call_id
    assert payload["event_type"] == "RECOVERY_GENERATION_AUTHORIZED"
    assert payload["source_state"] == "SETTLED_ERROR"
    assert payload["next_task_key"] == paid_task_key(family, 1)
    assert payload["authorization_reason"] == REQUEUE_REASON
    summary = json.loads(db.one(
        "SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run["id"], document_id)
    )["summary"])
    history = summary[HISTORY_KEY]
    assert len(history) == 1 and history[0]["source_call_id"] == call_id
    assert history[0]["diagnostic"]["exception"] == "ValueError"
    assert history[0]["next_generation"] == 1 and history[0]["recovery_event_id"]


def test_pre_request_input_reject_requeues_generation_zero_without_call_or_event(client, project):
    db, run, document_id, storage_id, family = _paused_run_with_evidence(
        client, project, error=INPUT_BUDGET_EVIDENCE_ERROR,
    )
    before_cost = db.cost(project["id"])
    result = requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert result["tasks"] == [{
        "document_id": document_id,
        "page_number": 1,
        "extraction_method": "TEXT_LAYER",
        "failure_type": "PRE_REQUEST_INPUT_BUDGET",
        "current_generation": 0,
        "next_generation": 0,
        "billable_retry": False,
    }]
    assert db.one("SELECT status,error FROM evidence WHERE id=?", (storage_id,)) == {
        "status": "PENDING", "error": "",
    }
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE run_id=?", (run["id"],))["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],))["n"] == 0
    assert db.authorized_generation(run["id"], family) == 0
    assert db.cost(project["id"]) == before_cost
    summary = json.loads(db.one(
        "SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run["id"], document_id)
    )["summary"])
    assert summary[HISTORY_KEY][0]["failure_type"] == "PRE_REQUEST_INPUT_BUDGET"
    assert summary[HISTORY_KEY][0]["source_call_id"] is None


def test_contract_generations_are_contiguous_bounded_and_history_is_append_only(client, project):
    db, run, document_id, storage_id, family = _paused_run_with_evidence(client, project)
    first_call = _settled_contract_error(db, run, family)
    requeue_failed_extraction_tasks(db, run["id"], apply=True)

    second_key = paid_task_key(family, 1)
    second_call = _settled_contract_error(db, run, second_key, exception="ValidationError")
    db.execute(
        "UPDATE evidence SET status='NEEDS_REVIEW',error=? WHERE id=?",
        (CONTRACT_EVIDENCE_ERROR, storage_id),
    )
    second = requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert second["tasks"][0]["current_generation"] == 1
    assert second["tasks"][0]["next_generation"] == 2
    assert db.authorized_generation(run["id"], family) == 2
    summary = json.loads(db.one(
        "SELECT summary FROM document_results WHERE run_id=? AND document_id=?", (run["id"], document_id)
    )["summary"])
    assert [item["source_call_id"] for item in summary[HISTORY_KEY]] == [first_call, second_call]

    _settled_contract_error(db, run, paid_task_key(family, 2))
    db.execute(
        "UPDATE evidence SET status='NEEDS_REVIEW',error=? WHERE id=?",
        (CONTRACT_EVIDENCE_ERROR, storage_id),
    )
    before_events = db.one(
        "SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],)
    )["n"]
    with pytest.raises(DomainError, match="三次累计调用上限"):
        requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert db.one("SELECT status FROM evidence WHERE id=?", (storage_id,))["status"] == "NEEDS_REVIEW"
    assert db.one(
        "SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],)
    )["n"] == before_events


@pytest.mark.parametrize("exception", ["KeyError", "TimeoutError", ""])
def test_unknown_contract_diagnostic_fails_closed_atomically(client, project, exception):
    db, run, _, storage_id, family = _paused_run_with_evidence(client, project)
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run["id"],))
    call_id = db.reserve(
        project["id"], run["id"], family, Decimal("0.1"), "model", "hash", Decimal("1"), Decimal("2")
    )
    diagnostic = {"kind": "CONTRACT_ERROR", "class": "EXTRACTION_RESULT"}
    if exception:
        diagnostic["exception"] = exception
    db.finalize_model_call(
        call_id, Decimal("0.01"), {"prompt_tokens": 1, "completion_tokens": 1}, None,
        diagnostic=diagnostic,
    )
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run["id"],))
    with pytest.raises(DomainError, match="不是已知抽取契约错误"):
        requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert db.one("SELECT status FROM evidence WHERE id=?", (storage_id,))["status"] == "NEEDS_REVIEW"
    assert db.one("SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],))["n"] == 0


def test_requeue_requires_paused_run_and_no_unresolved_calls(client, project):
    db, run, _, storage_id, family = _paused_run_with_evidence(client, project)
    _settled_contract_error(db, run, family)
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run["id"],))
    with pytest.raises(DomainError, match="暂停"):
        requeue_failed_extraction_tasks(db, run["id"], apply=True)
    db.reserve(project["id"], run["id"], "unresolved-other-task", Decimal("0.1"),
               "model", "other-hash", Decimal("1"), Decimal("2"))
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run["id"],))
    with pytest.raises(DomainError, match="待对账"):
        requeue_failed_extraction_tasks(db, run["id"], apply=True)
    assert db.one("SELECT status FROM evidence WHERE id=?", (storage_id,))["status"] == "NEEDS_REVIEW"


def test_apply_is_idempotent_after_evidence_returns_to_pending(client, project):
    db, run, _, _, family = _paused_run_with_evidence(client, project)
    _settled_contract_error(db, run, family)
    assert requeue_failed_extraction_tasks(db, run["id"], apply=True)["requeued_count"] == 1
    assert requeue_failed_extraction_tasks(db, run["id"], apply=True)["requeued_count"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM call_reconciliation_events WHERE run_id=?", (run["id"],))["n"] == 1
