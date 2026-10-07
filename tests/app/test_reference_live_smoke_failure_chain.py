"""Offline runner boundary checks for safe, complete named-v9 failure metadata."""
from copy import deepcopy

import httpx
import pytest

from scripts import reference_live_smoke as candidate
from tests.app.test_reference_live_smoke_v9 import _fixture, _handler, _proof, _receipt


def _chain(count=2, cached=False):
    receipts = []
    for index in range(count):
        receipt = _receipt("FLASH_NONE")
        receipt.update(round=index + 1, model_call_id="CALL-" + str(index + 1) * 32,
                       request_hash=str(index + 1) * 64,
                       cached=(cached[index] if isinstance(cached, tuple) else cached))
        receipts.append(receipt)
    return {"failure_execution_version": "reference-failure-execution-1",
            "receipt_scope": "COMPLETE_CHAIN", "execution_receipts": receipts,
            "supplement_round_count": count - 1,
            "accepted_supplement_request_count": count - 1}


def _executed(chain, *, replayed=False):
    receipts = chain["execution_receipts"]
    fresh = sum(not receipt["cached"] for receipt in receipts)
    return {"replayed": replayed, "failure_execution": chain,
            "execution_telemetry": {"scope": "CURRENT_EXECUTE",
                "model_call_count": 0 if replayed else fresh,
                "decision_count": 0 if replayed else len(receipts),
                "cached_decision_count": 0 if replayed else len(receipts) - fresh}}


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("replayed", [False, True])
def test_complete_failure_counts_separate_saved_and_current_execution(count, cached, replayed):
    chain = _chain(count, cached)
    counts = candidate._complete_failure_counts(
        _executed(chain, replayed=replayed), chain["execution_receipts"][-1], _proof("FLASH_NONE"))
    assert counts["model_call_count"] == (0 if cached else count)
    assert counts["new_calls_this_run"] == (0 if replayed or cached else count)
    assert counts["receipt_count"] == counts["model_decisions"] == count
    assert counts["supplement_rounds"] == counts["supplement_requests"] == count - 1
    assert counts["requested_supplement_rounds"] == counts["accepted_supplement_rounds"] == count - 1
    assert counts["requested_supplement_requests"] == counts["accepted_supplement_requests"] == count - 1
    assert counts["model_call_count_exact"] is counts["new_calls_this_run_exact"] is True
    assert counts["receipt_scope"] == "COMPLETE_CHAIN"
    assert "execution_receipts" not in counts


def test_mixed_cached_failure_chain_and_missing_current_telemetry():
    chain = _chain(3, (True, False, False))
    executed = _executed(chain)
    counts = candidate._complete_failure_counts(executed, chain["execution_receipts"][-1], _proof("FLASH_NONE"))
    assert counts["model_call_count"] == counts["new_calls_this_run"] == 2
    del executed["execution_telemetry"]
    unknown = candidate._complete_failure_counts(executed, chain["execution_receipts"][-1], _proof("FLASH_NONE"))
    assert unknown["model_call_count"] == 2 and unknown["model_call_count_exact"] is True
    assert unknown["new_calls_this_run"] is None and unknown["new_calls_this_run_exact"] is False


def test_absent_historical_chain_is_not_fabricated():
    assert candidate._complete_failure_counts({"replayed": True}, _receipt("FLASH_NONE"), _proof("FLASH_NONE")) is None


@pytest.mark.parametrize("value", [None, {}, [], "invalid", {"query": "do not retain"}])
def test_present_malformed_metadata_never_downgrades_to_unknown(value):
    with pytest.raises(ValueError, match="failure"):
        candidate._complete_failure_counts({"failure_execution": value}, _receipt("FLASH_NONE"), _proof("FLASH_NONE"))


@pytest.mark.parametrize("field,value", [
    ("supplement_round_count", True), ("supplement_round_count", 0),
    ("accepted_supplement_request_count", False), ("accepted_supplement_request_count", 0),
    ("accepted_supplement_request_count", 3),
    ("failure_execution_version", "unrecognized"), ("receipt_scope", "TERMINAL_ONLY"),
    ("query", "private query"), ("source", "private document"),
    ("output", "rejected private output"), ("reasoning", "private reasoning"),
])
def test_complete_failure_unknown_fields_and_invalid_counts_rejected(field, value):
    chain = _chain()
    chain[field] = value
    with pytest.raises(ValueError, match="failure"):
        candidate._complete_failure_counts(_executed(chain), chain["execution_receipts"][-1], _proof("FLASH_NONE"))


@pytest.mark.parametrize("field,value", [
    ("round", 2), ("model_call_id", "CALL-" + "2" * 32),
    ("question_hash", "0" * 64), ("reasoning", "must not leak"),
])
def test_prior_receipt_order_identity_duplicate_and_unknown_content_rejected(field, value):
    chain = _chain()
    chain["execution_receipts"][0][field] = value
    with pytest.raises(ValueError, match="receipt"):
        candidate._complete_failure_counts(_executed(chain), chain["execution_receipts"][-1], _proof("FLASH_NONE"))


def test_chain_terminal_must_equal_the_authenticated_failure_receipt():
    chain = _chain()
    terminal = deepcopy(chain["execution_receipts"][-1])
    terminal["cached"] = True
    with pytest.raises(ValueError, match="terminal"):
        candidate._complete_failure_counts(_executed(chain), terminal, _proof("FLASH_NONE"))


@pytest.mark.parametrize("count", [0, 4])
def test_failure_chain_has_one_to_three_decisions(count):
    chain = _chain(count)
    with pytest.raises(ValueError, match="receipt chain"):
        candidate._complete_failure_counts(_executed(chain), _receipt("FLASH_NONE"), _proof("FLASH_NONE"))


def test_runner_consumes_complete_failure_without_copying_receipts_or_source_text():
    chain = _chain(3, (True, False, False))
    calls, original = _handler(failure=True, receipt_change=chain["execution_receipts"][-1])
    def handler(request):
        response = original(request)
        if request.url.path.endswith("/execute"):
            body = response.json()
            body.update(_executed(chain, replayed=body["replayed"]))
            return httpx.Response(200, json=body)
        return response
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        first = candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                              profile_id="FLASH_NONE", selector_version=candidate.V9)
        resumed = candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                                evaluation_id="E1", profile_id="FLASH_NONE", selector_version=candidate.V9)
    row, replay = first["items"][0], resumed["items"][0]
    assert row["model_call_count"] == row["new_calls_this_run"] == 2
    assert row["supplement_rounds"] == row["supplement_requests"] == 2
    assert replay["model_call_count"] == 2 and replay["new_calls_this_run"] == 0
    assert "new_calls_this_run_lower_bound" not in row
    assert "failure_execution" not in row and "execution_receipts" not in row
    assert "must-not-save" not in repr(first)
    assert first["report_version"] == "reference-live-smoke-3"
    assert sum(path.endswith("/execute") for _, path, _ in calls) == 2


@pytest.mark.parametrize("basis,decisions,requested,accepted", [
    ("MODEL_QA_V3_EVIDENCE_LOOP", 1, 0, 0),
    ("MODEL_QA_V3_EVIDENCE_LOOP", 2, 1, 1),
    ("MODEL_QA_V3_EVIDENCE_LOOP", 3, 2, 2),
    ("QA_V3_NO_NEW_EVIDENCE", 1, 1, 1),
    ("QA_V3_NO_NEW_EVIDENCE", 2, 2, 2),
    ("QA_V3_ROUND_LIMIT", 3, 3, 2),
])
def test_v9_requested_and_accepted_supplements_have_separate_meanings(basis, decisions, requested, accepted):
    steps = [{"status": "NEED_EVIDENCE", "round": index + 1,
              "requests": [{"tool": "SEARCH_TEXT", "query": "private search text"}] * 2}
             for index in range(requested)]
    if basis == "QA_V3_NO_NEW_EVIDENCE":
        steps.append({"status": "CANNOT_ANSWER", "requests": [], "reason_code": "NO_NEW_EVIDENCE"})
    result = {"answer_basis": basis, "decision_trace": steps}
    counts = candidate._v9_supplement_counts(result, _chain(decisions)["execution_receipts"])
    assert counts["requested_supplement_rounds"] == requested
    assert counts["requested_supplement_requests"] == requested * 2
    assert counts["accepted_supplement_rounds"] == counts["supplement_rounds"] == accepted
    assert counts["accepted_supplement_requests"] == counts["supplement_requests"] == accepted * 2
    assert "private search text" not in repr(counts)


@pytest.mark.parametrize("basis,decisions,requested", [
    ("MODEL_QA_V3_EVIDENCE_LOOP", 2, 0),
    ("MODEL_QA_V3_EVIDENCE_LOOP", 2, 2),
    ("QA_V3_NO_NEW_EVIDENCE", 1, 0),
    ("QA_V3_NO_NEW_EVIDENCE", 3, 3),
    ("QA_V3_ROUND_LIMIT", 2, 2),
    ("QA_V3_ROUND_LIMIT", 3, 2),
])
def test_v9_inconsistent_supplement_trace_is_not_reported_as_measured(basis, decisions, requested):
    result = {"answer_basis": basis, "decision_trace": [
        {"status": "NEED_EVIDENCE", "requests": [{"tool": "SEARCH_TEXT", "query": "synthetic"}]}
        for _ in range(requested)]}
    with pytest.raises(ValueError, match="supplement"):
        candidate._v9_supplement_counts(result, _chain(decisions)["execution_receipts"])


@pytest.mark.parametrize("mode,model_decisions,requested,accepted", [
    ("no_new_evidence", 1, 1, 1), ("round_limit", 3, 3, 2),
])
def test_actual_loop_reports_attempted_retrieval_and_unexecuted_round_limit_separately(
        tmp_path, mode, model_decisions, requested, accepted):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import Settings
    from tests.app.test_page_selector import _raw_run
    from tests.app.test_reference_v9_evaluations import _channel

    app = create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True))
    with TestClient(app, headers={"X-CIRP-Client": "browser"}) as client:
        project = client.post("/api/projects", json={"name": "Synthetic requested versus accepted"}).json()
        db, source_run, _ = _raw_run(client, project, [
            {"page": 1, "sheet": "A5.01", "text": "InitialToken information."},
            {"page": 2, "sheet": "A5.02", "text": "SupplementOne information."},
            {"page": 3, "sheet": "A5.03", "text": "SupplementTwo information."},
            {"page": 4, "sheet": "A5.04", "text": "SupplementThree information."},
        ])
        queries = iter(["AbsentUnlistedToken"] if mode == "no_new_evidence" else [
            "SupplementOne", "SupplementTwo", "SupplementThree"])
        def decision(*args):
            return {"status": "NEED_EVIDENCE", "reason_code": "MISSING_SOURCE_TEXT",
                    "missing_facts": ["Synthetic source is required."],
                    "requests": [{"tool": "SEARCH_TEXT", "query": next(queries)}],
                    "answer": {"claims": [], "calculations": [], "coverage": []}}
        calls = _channel(client, decision)
        fixture = {"frozen_context": {"project_id": project["id"], "run_id": source_run["id"],
                    "snapshot_id": source_run["snapshot_id"]}, "questions": [
                        {"id": "Q1", "question": "What InitialToken is recorded on Sheet A5.01?"}]}
        report = candidate.run(client, fixture, ["Q1"], confirmed=True,
                               profile_id="FLASH_NONE", selector_version=candidate.V9)
        row = report["items"][0]
        assert row["state"] == "COMPLETE" and row["status"] == "CANNOT_ANSWER"
        assert row["model_decisions"] == row["new_calls_this_run"] == model_decisions
        assert row["requested_supplement_rounds"] == row["requested_supplement_requests"] == requested
        assert row["accepted_supplement_rounds"] == row["accepted_supplement_requests"] == accepted
        assert row["supplement_rounds"] == row["supplement_requests"] == accepted
        resumed = candidate.run(client, fixture, ["Q1"], confirmed=True,
                                 evaluation_id=report["evaluation_id"], profile_id="FLASH_NONE",
                                 selector_version=candidate.V9)
        assert resumed["items"][0]["new_calls_this_run"] == 0
        assert resumed["items"][0]["accepted_supplement_rounds"] == accepted
        assert len(calls) == db.one("SELECT COUNT(*) AS n FROM model_calls")["n"] == model_decisions
