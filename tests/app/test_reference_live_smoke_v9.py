"""Offline proof-bound runner checks; synthetic transport and temporary application data."""
import json

import httpx
import pytest


from scripts import reference_live_smoke as candidate


def _fixture():
    return {"frozen_context": {"project_id": "P1", "run_id": "R1", "snapshot_id": "S1"},
            "questions": [{"id": "Q1", "question": "What is approved?"}]}


def _profile(profile_id):
    return candidate.named_profile(profile_id)


def _proof(profile_id):
    return candidate.canonical_preview_proof(
        {"project_id": "P1", "id": "R1", "snapshot_id": "S1"}, "What is approved?",
        _profile(profile_id), "SEL1", "3" * 64)


def _receipt(profile_id):
    proof, route = _proof(profile_id), _profile(profile_id)
    return {
        "receipt_version": "reference-model-input-receipt-3", "model_call_id": "CALL-" + "a" * 32,
        "round": 1, "request_hash": "1" * 64, "question_hash": proof["normalized_question_sha256"],
        "prompt_contract_hash": proof["prompt_contract_hash"], "provider": "deepseek",
        "model": route["text_model"], "api_protocol": "chat_completions", "structured_output_mode": "json_object",
        "inference_mode": route["inference_mode"], "max_output_tokens": route["max_output_tokens"],
        "cached": False, "source_text_included": False, "prompt_content_included": False,
        "chain_of_thought_included": False, "evidence_count": 1,
        "evidence_inputs": [{"evidence_id": "EV1", "text_sha256": "2" * 64}], "visual_inputs": [],
        "system_text_bytes": 100, "user_text_bytes": 500, "image_bytes": 0, "request_upper_bound_bytes": 900,
        "selector_version": candidate.V9, "context_policy": proof["context_policy"], "source_text_clipped": False,
        "ordered_evidence_manifest_sha256": "4" * 64, "source_text_bytes": 50,
        "initial_evidence_manifest_sha256": proof["initial_evidence_manifest_sha256"],
        "profile_neutral_input_sha256": "5" * 64, "layout_navigation_version": "source-layout-navigation-1",
        "layout_navigation_sha256": "6" * 64, "layout_navigation_bytes": 50,
    }


def _handler(*, enabled=True, proof_capability=True, selector=None, profile_id="FLASH_NONE",
             terminal=False, failure=False, proof_change=None, receipt_change=None, saved_count=1):
    calls, evaluation = [], {"evaluation_id": "E1", "project_id": "P1", "run_id": "R1", "snapshot_id": "S1",
        "selector_version": selector or candidate.V9, "profile": _profile(profile_id),
        "items": [{"item_id": "I1", "question": "What is approved?", "state": "COMPLETE" if terminal else "PENDING"}]}
    proof = _proof(profile_id)
    if proof_change: proof.update(proof_change)
    receipt = _receipt(profile_id)
    if receipt_change: receipt.update(receipt_change)
    if terminal: evaluation["items"][0]["result"] = {"result_id": "RES1"}
    saved = {"result_id": "RES1", "status": "CANNOT_ANSWER", "result": {
        "model_call_count": saved_count, "execution_receipts": [receipt]}}
    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path == "/api/settings": return httpx.Response(200, json={"capabilities": {"reference_layout_v9": {
            "enabled": enabled, "evaluation_execute_preview_proof": proof_capability}}})
        if path == "/api/model-settings": return httpx.Response(200, json={"provider": "deepseek", "model": "deepseek-flash", "reasoning_effort": "none", "vision_enabled": False})
        if path.endswith("unresolved-model-calls"): return httpx.Response(200, json=[])
        if path.endswith("/reference-evaluations") and request.method == "POST": return httpx.Response(201, json=evaluation)
        if path.endswith("/preview"): return httpx.Response(200, json={"preview_proof": proof,
            "evaluation_id": "E1", "item_id": "I1", "question": "What is approved?",
            "page_selection": {"selection_id": "SEL1"}, "source_text": "must-not-save"})
        if path.endswith("/execute"):
            replayed = evaluation["items"][0]["state"] != "PENDING"
            if evaluation["selector_version"] == candidate.V9:
                body = json.loads(request.content)
                assert body["preview_proof"] == proof and body["replay_only"] == replayed
            evaluation["items"][0]["state"] = "FAILED" if failure else "COMPLETE"
            if failure: evaluation["items"][0]["failure"] = {"code": "SYNTHETIC", "execution_receipt": receipt}
            else: evaluation["items"][0]["result"] = {"result_id": "RES1"}
            return httpx.Response(200, json={"replayed": replayed, "evaluation": evaluation,
                "result": None if failure else saved, "execution_telemetry": {
                    "scope": "CURRENT_EXECUTE", "model_call_count": 0 if replayed else 1,
                    "decision_count": 0 if replayed else 1, "cached_decision_count": 0}})
        if path == "/api/reference-evaluations/E1": return httpx.Response(200, json=evaluation)
        if path == "/api/reference-results/RES1": return httpx.Response(200, json=saved)
        return httpx.Response(404, json={"detail": path})
    return calls, handler


@pytest.mark.parametrize("enabled", [False, None])
def test_v9_off_or_missing_capability_stops_before_any_post(enabled):
    calls, handler = _handler(enabled=enabled)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="not enabled"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True, profile_id="FLASH_NONE", selector_version=candidate.V9)
    assert calls == [("GET", "/api/settings", b"")]


def test_no_confirmation_has_no_transport_activity():
    with pytest.raises(ValueError, match="confirmation"):
        candidate.run(None, _fixture(), ["Q1"], profile_id="FLASH_NONE", selector_version=candidate.V9)


def test_v9_wrong_selector_resume_never_executes():
    calls, handler = _handler(selector=candidate.V8)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="frozen selector"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True, evaluation_id="E1", profile_id="FLASH_NONE", selector_version=candidate.V9)
    assert not any(path.endswith("/execute") for _, path, _ in calls)


def test_v9_wrong_run_or_profile_resume_never_executes():
    for profile_id in ("FLASH_LOW",):
        calls, handler = _handler(profile_id="FLASH_NONE")
        with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="named text profile"):
                candidate.run(client, _fixture(), ["Q1"], confirmed=True, evaluation_id="E1", profile_id=profile_id, selector_version=candidate.V9)
        assert not any(path.endswith("/execute") for _, path, _ in calls)


def test_v9_wrong_run_resume_never_executes():
    calls, handler = _handler()
    fixture = _fixture()
    fixture["frozen_context"]["run_id"] = "OTHER"
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="frozen selector/project/run"):
            candidate.run(client, fixture, ["Q1"], confirmed=True, evaluation_id="E1", profile_id="FLASH_NONE", selector_version=candidate.V9)
    assert not any(path.endswith("/execute") for _, path, _ in calls)


@pytest.mark.parametrize("profile_id", ["FLASH_NONE", "FLASH_LOW", "PRO"])
def test_v9_profiles_preview_then_execute_and_resume_without_new_call(profile_id):
    calls, handler = _handler(profile_id=profile_id)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        report = candidate.run(client, _fixture(), ["Q1"], confirmed=True, profile_id=profile_id, selector_version=candidate.V9)
        assert report["items"][0]["preview_proof"] == _proof(profile_id)
        assert "must-not-save" not in repr(report) and "answer" not in report["items"][0]
        resumed = candidate.run(client, _fixture(), ["Q1"], confirmed=True, evaluation_id="E1", profile_id=profile_id, selector_version=candidate.V9)
    paths = [path for _, path, _ in calls]
    assert paths.index("/api/reference-evaluations/E1/items/I1/preview") < paths.index("/api/reference-evaluations/E1/items/I1/execute")
    assert paths.count("/api/reference-evaluations/E1/items/I1/execute") == 2
    assert resumed["items"][0]["already_terminal"] is True
    assert resumed["items"][0]["replayed"] is True
    assert resumed["items"][0]["new_calls_this_run"] == 0


def test_v8_default_remains_compatible_without_named_profile():
    calls, handler = _handler(selector=candidate.V8)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        report = candidate.run(client, _fixture(), ["Q1"], confirmed=True, selector_version=candidate.V8)
    assert report["selector_version"] == candidate.V8
    create = next(body for method, path, body in calls if method == "POST" and path.endswith("/reference-evaluations"))
    assert b"selector_version" not in create


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("round_number", [1, 2, 3])
def test_v9_failure_receipt_does_not_invent_whole_question_call_count(cached, round_number):
    calls, handler = _handler(failure=True, receipt_change={"cached": cached, "round": round_number})
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        report = candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                               profile_id="FLASH_NONE", selector_version=candidate.V9)
    row = report["items"][0]
    assert row["model_call_count"] is None and row["model_call_count_exact"] is False
    assert row["terminal_round"] == round_number and row["terminal_receipt_cached"] is cached
    assert row["terminal_new_call_lower_bound"] == int(not cached)
    assert row["receipt_scope"] == "TERMINAL_ONLY" and row["new_calls_this_run"] is None
    assert sum(path.endswith("/execute") for _, path, _ in calls) == 1


@pytest.mark.parametrize("supported", [False, None, 1, "true"])
def test_v9_old_service_cannot_silently_ignore_execute_body(supported):
    calls, handler = _handler(proof_capability=supported)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="not enabled"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                          profile_id="FLASH_NONE", selector_version=candidate.V9)
    assert calls == [("GET", "/api/settings", b"")]


@pytest.mark.parametrize("field", list(_proof("FLASH_NONE")))
def test_v9_each_proof_field_mismatch_stops_before_execute(field):
    value = _proof("FLASH_NONE")[field]
    calls, handler = _handler(proof_change={field: value + "-changed"})
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="preview"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                          profile_id="FLASH_NONE", selector_version=candidate.V9)
    assert not any(path.endswith("/execute") for _, path, _ in calls)


@pytest.mark.parametrize("field", ["question_hash", "prompt_contract_hash", "initial_evidence_manifest_sha256",
                                   "model", "inference_mode", "context_policy", "selector_version"])
def test_v9_receipt_identity_mismatch_is_not_a_successful_report(field):
    original = _receipt("FLASH_NONE")[field]
    changed = "0" * 64 if field.endswith("hash") or field.endswith("sha256") else original + "-changed"
    _, handler = _handler(receipt_change={field: changed})
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="receipt"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                          profile_id="FLASH_NONE", selector_version=candidate.V9)


def test_duplicate_fixture_ids_rejected_before_transport():
    fixture = _fixture()
    fixture["questions"] *= 2
    with pytest.raises(ValueError, match="distinct"):
        candidate.run(None, fixture, ["Q1"], confirmed=True)


def test_checkpoint_failure_preserves_prior_complete_report(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    candidate.write_checkpoint(report, {"evaluation_id": "E1"})
    def reject_replace(*args): raise OSError("synthetic interrupted replacement")
    monkeypatch.setattr(candidate.os, "replace", reject_replace)
    with pytest.raises(OSError): candidate.write_checkpoint(report, {"evaluation_id": "E2"})
    assert json.loads(report.read_text()) == {"evaluation_id": "E1"}
    assert list(tmp_path.iterdir()) == [report]


@pytest.mark.parametrize("profile_id", ["FLASH_NONE", "FLASH_LOW", "PRO"])
@pytest.mark.parametrize("failure", [False, True])
def test_real_api_named_runner_and_authenticated_terminal_recovery(tmp_path, profile_id, failure):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import Settings
    from tests.app.test_reference_v9_evaluations import _v9_run, _channel, _cannot_answer

    app = create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True))
    with TestClient(app, headers={"X-CIRP-Client": "browser"}) as client:
        project = client.post("/api/projects", json={"name": "Synthetic proof-bound runner"}).json()
        db, source_run, _ = _v9_run(client, project)
        calls = _channel(client, lambda *_: "not json" if failure else _cannot_answer())
        fixture = {"frozen_context": {"project_id": project["id"], "run_id": source_run["id"],
                    "snapshot_id": source_run["snapshot_id"]}, "questions": [
                        {"id": "Q1", "question": "What approved color applies to Finish key PT9?"}]}
        report = candidate.run(client, fixture, ["Q1"], confirmed=True,
                               profile_id=profile_id, selector_version=candidate.V9)
        row = report["items"][0]
        assert row["state"] == ("FAILED" if failure else "COMPLETE")
        assert row["receipt_count"] == 1 and row["replayed"] is False
        assert len(calls) == 1 and db.one("SELECT COUNT(*) AS n FROM model_calls")["n"] == 1
        assert calls[0][0]["model"] == _profile(profile_id)["text_model"]
        assert calls[0][0]["max_tokens"] == _profile(profile_id)["max_output_tokens"]
        if not failure:
            assert row["new_calls_this_run"] == 1
            duplicate = candidate.run(client, fixture, ["Q1"], confirmed=True,
                                      profile_id=profile_id, selector_version=candidate.V9)
            duplicate_row = duplicate["items"][0]
            assert duplicate["evaluation_id"] != report["evaluation_id"]
            assert duplicate_row["result_id"] == row["result_id"]
            assert duplicate_row["replayed"] is False and len(calls) == 1
            assert duplicate_row["model_call_count"] == 1  # Original saved execution.
            assert duplicate_row["new_calls_this_run"] == 0  # This request used cached decisions.
            assert duplicate_row["new_calls_this_run_exact"] is True
        recovered = candidate.run(client, fixture, ["Q1"], confirmed=True,
                                  evaluation_id=report["evaluation_id"], profile_id=profile_id,
                                  selector_version=candidate.V9)
        assert recovered["items"][0]["replayed"] is True
        assert recovered["items"][0]["new_calls_this_run"] == 0
        assert len(calls) == 1 and db.one("SELECT COUNT(*) AS n FROM model_calls")["n"] == 1
        assert "Finish PT9 blue" not in json.dumps(report) and "answer" not in row


def test_real_api_two_round_failure_counts_cache_replay_and_preserves_old_null_unknown(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import Settings
    from tests.app.test_page_selector import _raw_run
    from tests.app.test_reference_v9_evaluations import _channel

    app = create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True))
    with TestClient(app, headers={"X-CIRP-Client": "browser"}) as client:
        project = client.post("/api/projects", json={"name": "Synthetic multi-round runner"}).json()
        db, source_run, _ = _raw_run(client, project, [
            {"page": 1, "text": "InitialToken is shown here."},
            {"page": 2, "text": "SupplementToken contains the remaining detail."},
        ])
        def decision(*args):
            if len(calls) > 1: return "not json"
            return {"status": "NEED_EVIDENCE", "reason_code": "MISSING_SOURCE_TEXT",
                    "missing_facts": ["Supplemental detail is required."],
                    "requests": [{"tool": "SEARCH_TEXT", "query": "SupplementToken"}],
                    "answer": {"claims": [], "calculations": [], "coverage": []}}
        calls = _channel(client, decision)
        fixture = {"frozen_context": {"project_id": project["id"], "run_id": source_run["id"],
                   "snapshot_id": source_run["snapshot_id"]}, "questions": [
                       {"id": "Q1", "question": "What InitialToken is recorded?"}]}
        first = candidate.run(client, fixture, ["Q1"], confirmed=True,
                              profile_id="FLASH_NONE", selector_version=candidate.V9)
        row = first["items"][0]
        assert len(calls) == 2 and row["terminal_round"] == 2
        assert row["model_call_count"] == row["receipt_count"] == 2
        assert row["supplement_rounds"] == row["supplement_requests"] == 1
        assert row["terminal_receipt_cached"] is False and row["new_calls_this_run"] == 2
        assert row["receipt_scope"] == "COMPLETE_CHAIN"
        # A new evaluation of the exact same frozen input recovers cached decisions.
        second = candidate.run(client, fixture, ["Q1"], confirmed=True,
                               profile_id="FLASH_NONE", selector_version=candidate.V9)
        cached = second["items"][0]
        assert second["evaluation_id"] != first["evaluation_id"] and len(calls) == 2
        assert cached["terminal_receipt_cached"] is True and cached["terminal_round"] == 2
        assert cached["model_call_count"] == cached["new_calls_this_run"] == 0
        assert cached["receipt_count"] == 2 and cached["all_receipts_cached"] is True
        assert db.one("SELECT COUNT(*) AS n FROM model_calls")["n"] == 2
        # A historical terminal-only failure remains explicitly unknown. This
        # synthetic NULL simulates a pre-migration row, never a product backfill.
        db.execute("UPDATE reference_evaluation_failures SET failure_execution_json=NULL WHERE item_id=?",
                   (row["item_id"],))
        old = candidate.run(client, fixture, ["Q1"], confirmed=True,
                            evaluation_id=first["evaluation_id"], profile_id="FLASH_NONE",
                            selector_version=candidate.V9)["items"][0]
        assert old["model_call_count"] is None and old["model_call_count_exact"] is False
        assert old["receipt_scope"] == "TERMINAL_ONLY" and old["new_calls_this_run"] == 0
        assert "supplement_rounds" not in old and "supplement_requests" not in old
        assert len(calls) == 2


def test_v8_failure_report_keeps_exact_original_shape():
    _, handler = _handler(selector=candidate.V8, failure=True)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        report = candidate.run(client, _fixture(), ["Q1"], confirmed=True)
    assert report["report_version"] == "reference-live-smoke-1"
    assert set(report["items"][0]) == {
        "question_id", "item_id", "state", "elapsed_seconds", "already_terminal", "failure"}


@pytest.mark.parametrize("telemetry", [None, {}, {"scope": "SAVED_EXECUTION"},
    {"scope": "CURRENT_EXECUTE", "model_call_count": 1, "decision_count": 1, "cached_decision_count": 1},
    {"scope": "CURRENT_EXECUTE", "model_call_count": 0, "decision_count": 0, "cached_decision_count": 0},
    {"scope": "CURRENT_EXECUTE", "model_call_count": True, "decision_count": 1, "cached_decision_count": 0}])
def test_unknown_current_telemetry_never_falls_back_to_saved_call_count(telemetry):
    assert candidate._new_call_count({"replayed": False, "execution_telemetry": telemetry}, expected_decisions=1) is None


@pytest.mark.parametrize("saved_count", [3, True, None])
def test_saved_success_call_count_must_match_its_receipt_chain(saved_count):
    _, handler = _handler(saved_count=saved_count)
    with httpx.Client(base_url="http://synthetic", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="saved call count"):
            candidate.run(client, _fixture(), ["Q1"], confirmed=True,
                          profile_id="FLASH_NONE", selector_version=candidate.V9)
