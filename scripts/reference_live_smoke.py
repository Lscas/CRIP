"""Explicitly authorized local Reference evaluation; no automatic retry or model switch."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.reference_text_profiles import profile as named_profile
from app.reference_layout_input import preview_proof as canonical_preview_proof
from contracts.runtime_rules import validate_schema
from jsonschema import ValidationError


V8 = "literal-page-selector-8"
V9 = "literal-page-selector-9"
_V9_PROOF_KEYS = (
    "proof_version", "project_id", "run_id", "snapshot_id",
    "normalized_question_sha256", "selector_version", "context_policy",
    "profile_version", "profile_id", "route_sha256", "selection_id",
    "initial_evidence_manifest_sha256", "prompt_contract_hash",
)


def _v9_enabled(settings: dict) -> bool:
    capability = settings.get("capabilities", {}).get("reference_layout_v9", {})
    return (isinstance(capability, dict) and capability.get("enabled") is True
            and capability.get("evaluation_execute_preview_proof") is True)


def _proof_metadata(preview: dict, *, project: str, run_id: str, snapshot_id: str,
                    profile_id: str, evaluation_id: str, item_id: str, question: str) -> dict:
    proof = preview.get("preview_proof")
    if not isinstance(proof, dict) or set(proof) != set(_V9_PROOF_KEYS):
        raise ValueError("The v9 item preview did not return an exact proof.")
    if ((preview.get("evaluation_id"), preview.get("item_id"), preview.get("question"))
            != (evaluation_id, item_id, question)):
        raise ValueError("The v9 item preview differs from the frozen item identity.")
    selection = proof.get("selection_id")
    manifest = proof.get("initial_evidence_manifest_sha256")
    if (not isinstance(selection, str) or not 1 <= len(selection) <= 128
            or not isinstance(manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest)
            or preview.get("page_selection", {}).get("selection_id") != selection):
        raise ValueError("The v9 item preview source identity is malformed.")
    expected = canonical_preview_proof(
        {"project_id": project, "id": run_id, "snapshot_id": snapshot_id}, question,
        named_profile(profile_id), selection, manifest)
    if proof != expected:
        raise ValueError("The v9 item preview proof differs from the frozen evaluation identity.")
    # Copy only fixed identity metadata: never retain preview content or reasoning.
    return {key: proof[key] for key in _V9_PROOF_KEYS}


def _require_v9_receipts(receipts: list[dict], proof: dict, *, terminal_only=False) -> None:
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("The v9 result requires authenticated execution receipts.")
    route = named_profile(proof["profile_id"])
    expected = {
        "receipt_version": "reference-model-input-receipt-3", "selector_version": V9,
        "initial_evidence_manifest_sha256": proof["initial_evidence_manifest_sha256"],
        "question_hash": proof["normalized_question_sha256"],
        "prompt_contract_hash": proof["prompt_contract_hash"], "context_policy": proof["context_policy"],
        "provider": route["provider"], "model": route["text_model"],
        "inference_mode": route["inference_mode"], "max_output_tokens": route["max_output_tokens"],
        "api_protocol": route["api_protocol"], "structured_output_mode": route["structured_output_mode"],
        "visual_inputs": [], "image_bytes": 0, "source_text_clipped": False,
    }
    for receipt in receipts:
        try:
            validate_schema("reference-model-input-receipt", receipt)
        except ValidationError as exc:
            raise ValueError("The v9 execution receipt is malformed.") from exc
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("The v9 result receipt does not match the preview proof and profile.")
    if not terminal_only and [r["round"] for r in receipts] != list(range(1, len(receipts) + 1)):
        raise ValueError("The v9 result receipt chain is incomplete.")


def _new_call_count(executed: dict, *, expected_decisions: int) -> int | None:
    if executed["replayed"]:
        return 0
    # Saving an identical successful result may return an older result object.
    # Only request-local telemetry, never saved receipt/cache flags, counts this run.
    telemetry = executed.get("execution_telemetry")
    if not isinstance(telemetry, dict) or telemetry.get("scope") != "CURRENT_EXECUTE":
        return None
    fields = ("model_call_count", "decision_count", "cached_decision_count")
    if any(type(telemetry.get(key)) is not int or not 0 <= telemetry[key] <= 3 for key in fields):
        return None
    if telemetry["model_call_count"] + telemetry["cached_decision_count"] != telemetry["decision_count"]:
        return None
    if telemetry["decision_count"] != expected_decisions:
        return None
    return telemetry["model_call_count"]


def _complete_failure_counts(executed: dict, terminal_receipt: dict, proof: dict) -> dict | None:
    """Project authenticated safe metadata only; absent historical chains stay unknown."""
    if "failure_execution" not in executed:
        return None
    chain = executed["failure_execution"]
    keys = {"failure_execution_version", "receipt_scope", "execution_receipts",
            "supplement_round_count", "accepted_supplement_request_count"}
    if (not isinstance(chain, dict) or set(chain) != keys
            or chain["failure_execution_version"] != "reference-failure-execution-1"
            or chain["receipt_scope"] != "COMPLETE_CHAIN"):
        raise ValueError("The complete failure execution metadata is malformed.")
    receipts = chain["execution_receipts"]
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 3:
        raise ValueError("The complete failure receipt chain is malformed.")
    _require_v9_receipts(receipts, proof)
    if (len({receipt["model_call_id"] for receipt in receipts}) != len(receipts)
            or receipts[-1] != terminal_receipt):
        raise ValueError("The complete failure receipt chain differs from its terminal outcome.")
    rounds, requests = chain["supplement_round_count"], chain["accepted_supplement_request_count"]
    if (type(rounds) is not int or rounds != len(receipts) - 1
            or type(requests) is not int or not rounds <= requests <= 2 * rounds):
        raise ValueError("The complete failure supplement counts are inconsistent.")
    new_calls = _new_call_count(executed, expected_decisions=len(receipts))
    return {"model_call_count": sum(not receipt["cached"] for receipt in receipts),
            "model_call_count_exact": True, "model_call_count_scope": "SAVED_EXECUTION",
            "receipt_count": len(receipts), "receipt_scope": "COMPLETE_CHAIN",
            "model_decisions": len(receipts),
            "all_receipts_cached": all(receipt["cached"] for receipt in receipts),
            "supplement_rounds": rounds, "supplement_requests": requests,
            "requested_supplement_rounds": rounds, "requested_supplement_requests": requests,
            "accepted_supplement_rounds": rounds, "accepted_supplement_requests": requests,
            "input_evidence_counts": [receipt["evidence_count"] for receipt in receipts],
            "input_bytes": [receipt["user_text_bytes"] for receipt in receipts],
            "new_calls_this_run": new_calls, "new_calls_this_run_exact": new_calls is not None}


def _v9_supplement_counts(result: dict, receipts: list[dict]) -> dict:
    """Distinguish model requests from local retrievals, without storing trace text."""
    trace = result.get("decision_trace", [])
    if not isinstance(trace, list) or any(not isinstance(step, dict) for step in trace):
        raise ValueError("The v9 supplement trace is malformed.")
    requested = [step for step in trace if step.get("status") == "NEED_EVIDENCE"]
    if any(not isinstance(step.get("requests"), list) or not 1 <= len(step["requests"]) <= 2
           for step in requested):
        raise ValueError("The v9 supplement requests are malformed.")
    basis = result.get("answer_basis")
    # NO_NEW_EVIDENCE performed a local retrieval without a subsequent model
    # decision. ROUND_LIMIT returned before executing its last proposed retrieval.
    accepted = len(receipts) - 1 + int(basis == "QA_V3_NO_NEW_EVIDENCE")
    if (not 0 <= accepted <= 2
            or len(requested) != accepted + int(basis == "QA_V3_ROUND_LIMIT")
            or (basis == "QA_V3_ROUND_LIMIT" and len(receipts) != 3)):
        raise ValueError("The v9 supplement trace does not match its decision receipts.")
    request_count = sum(len(step["requests"]) for step in requested)
    accepted_count = sum(len(step["requests"]) for step in requested[:accepted])
    return {"requested_supplement_rounds": len(requested),
            "requested_supplement_requests": request_count,
            "accepted_supplement_rounds": accepted,
            "accepted_supplement_requests": accepted_count,
            "supplement_rounds": accepted, "supplement_requests": accepted_count}


def run(client, fixture, question_ids, *, confirmed=False, evaluation_id=None, profile_id=None,
        selector_version=V8, checkpoint=lambda value: None):
    """Run only selected frozen questions; never retry or resolve pending calls automatically."""
    if confirmed is not True:
        raise ValueError("Explicit live-call confirmation is required.")
    if selector_version not in (V8, V9):
        raise ValueError("Choose a supported frozen selector version.")
    questions = {item["id"]: item["question"] for item in fixture["questions"]}
    if (len(questions) != len(fixture["questions"]) or not question_ids
            or len(set(question_ids)) != len(question_ids)
            or not set(question_ids) <= questions.keys()):
        raise ValueError("Choose distinct questions from the frozen fixture.")
    context = fixture["frozen_context"]
    project, run_id = context["project_id"], context["run_id"]

    def get(path):
        response = client.get(path)
        response.raise_for_status()
        return response.json()

    def post(path, value=None):
        response = client.post(path, json=value or {})
        response.raise_for_status()
        return response.json()

    named = None if profile_id is None else named_profile(profile_id)
    if selector_version == V9:
        if profile_id not in ("FLASH_NONE", "FLASH_LOW", "PRO"):
            raise ValueError("Selector v9 requires one canonical named text profile.")
        # The capability check is deliberately the first HTTP request for v9.
        if not _v9_enabled(get("/api/settings")):
            raise ValueError("Reference layout v9 proof-bound execution is not enabled; no dispatch occurred.")
    else:
        active = get("/api/model-settings")
        if named is None and (active["provider"] != "deepseek" or active["model"] != "deepseek-flash"
                              or active["reasoning_effort"] != "none" or active["vision_enabled"]):
            raise ValueError("This controlled smoke requires the existing Flash, thinking-disabled, text-only profile.")
    if get(f"/api/projects/{project}/unresolved-model-calls"):
        raise ValueError("Unresolved calls must be reconciled; no dispatch occurred.")
    snapshot_id = context["snapshot_id"]
    ordered = [questions[key] for key in question_ids]
    if evaluation_id:
        evaluation = get(f"/api/reference-evaluations/{evaluation_id}")
    else:
        body = {"run_id": run_id, "name": ("Complete-source v8 controlled failure retest"
                if selector_version == V8 else "Layout-bound v9 controlled failure retest"), "questions": ordered}
        if profile_id is not None:
            body["profile_id"] = profile_id
        if selector_version == V9:
            body["selector_version"] = V9
        evaluation = post(f"/api/projects/{project}/reference-evaluations", body)
        evaluation_id = evaluation["evaluation_id"]
    if (evaluation.get("evaluation_id") != evaluation_id
            or evaluation.get("project_id") != project or evaluation.get("run_id") != run_id
            or evaluation.get("snapshot_id") != snapshot_id
            or evaluation.get("selector_version") != selector_version
            or [item["question"] for item in evaluation.get("items", [])] != ordered):
        raise ValueError("The evaluation differs from the frozen selector/project/run/snapshot/question identity.")
    if any(item.get("state") not in ("PENDING", "COMPLETE", "FAILED") for item in evaluation["items"]):
        raise ValueError("The evaluation contains an unsupported item state.")
    if selector_version == V9 and evaluation.get("profile") != named:
        raise ValueError("The saved v9 evaluation does not match the requested named text profile.")
    if selector_version == V8 and named is not None and evaluation.get("profile") != named:
        raise ValueError("The saved evaluation does not match the requested named text profile.")

    report = {"report_version": ("reference-live-smoke-1" if selector_version == V8
                                  else "reference-live-smoke-3"), "evaluation_id": evaluation_id,
              "project_id": project, "run_id": run_id, "snapshot_id": snapshot_id,
              "selector_version": evaluation["selector_version"], "profile": evaluation["profile"],
              "policy": {"automatic_retries": 0, "human_adjudications_written": False,
                         "source_text_in_report": False, "private_reasoning_saved": False}, "items": []}
    if selector_version == V9:
        report["policy"]["supplement_count_semantics"] = "REQUESTED_AND_ACCEPTED_SEPARATE_LEGACY_ALIASES_ACCEPTED"
    checkpoint(report)
    for question_id, item in zip(question_ids, evaluation["items"]):
        started = time.perf_counter()
        terminal = item["state"] != "PENDING"
        proof = None
        # The proof is enforced by the server before dispatch; terminal recovery
        # authenticates the saved outcome and cannot fall through to a new call.
        if selector_version == V9:
            proof = _proof_metadata(post(f"/api/reference-evaluations/{evaluation_id}/items/{item['item_id']}/preview"),
                                    project=project, run_id=run_id, snapshot_id=snapshot_id,
                                    profile_id=profile_id, evaluation_id=evaluation_id,
                                    item_id=item["item_id"], question=item["question"])
            executed = post(f"/api/reference-evaluations/{evaluation_id}/items/{item['item_id']}/execute",
                            {"preview_proof": proof, "replay_only": terminal})
            if type(executed.get("replayed")) is not bool or (terminal and not executed["replayed"]):
                raise ValueError("The v9 execution did not confirm authenticated terminal replay.")
        elif not terminal:
            post(f"/api/reference-evaluations/{evaluation_id}/items/{item['item_id']}/execute")
        current = (executed["evaluation"] if selector_version == V9
                   else get(f"/api/reference-evaluations/{evaluation_id}"))
        if selector_version == V9 and any(current.get(key) != evaluation.get(key) for key in (
                "evaluation_id", "project_id", "run_id", "snapshot_id", "selector_version", "profile")):
            raise ValueError("The authenticated execution changed the frozen evaluation identity.")
        outcome = next(value for value in current["items"] if value["item_id"] == item["item_id"])
        if outcome.get("question") != item["question"] or outcome.get("state") not in ("COMPLETE", "FAILED"):
            raise ValueError("The evaluation execution did not return the same terminal question.")
        row = {"question_id": question_id, "item_id": item["item_id"], "state": outcome["state"],
               "elapsed_seconds": round(time.perf_counter() - started, 3), "already_terminal": terminal}
        if selector_version == V8:
            row["failure"] = outcome.get("failure")
        if proof is not None:
            row["preview_proof"] = proof
            row["replayed"] = executed["replayed"]
        saved = outcome.get("result")
        if saved:
            result_id = saved["result_id"]
            saved = (executed["result"] if selector_version == V9
                     else get(f"/api/reference-results/{result_id}"))
            if not isinstance(saved, dict) or saved.get("result_id") != result_id:
                raise ValueError("The execution result differs from the authenticated item result.")
            result = saved["result"]
            receipts = result.get("execution_receipts", [])
            if selector_version == V9:
                _require_v9_receipts(receipts, proof)
                if (type(result.get("model_call_count")) is not int
                        or result["model_call_count"] != sum(not r["cached"] for r in receipts)):
                    raise ValueError("The saved call count does not match its complete receipt chain.")
            row.update({"result_id": saved["result_id"], "status": saved["status"],
                        "model_call_count": result.get("model_call_count"), "receipt_count": len(receipts),
                        "all_receipts_cached": bool(receipts) and all(receipt["cached"] for receipt in receipts)})
            trace = result.get("decision_trace", [])
            row.update({"supplement_rounds": sum(step["status"] == "NEED_EVIDENCE" for step in trace),
                        "supplement_requests": sum(len(step.get("requests", [])) for step in trace),
                        "input_evidence_counts": [receipt["evidence_count"] for receipt in receipts],
                        "input_bytes": [receipt["user_text_bytes"] for receipt in receipts]})
            if selector_version == V8:
                row.update({"answer": result.get("answer", ""), "missing": result.get("missing", [])})
            else:
                row.update(_v9_supplement_counts(result, receipts))
                new_calls = _new_call_count(executed, expected_decisions=len(receipts))
                row.update({"model_call_count_exact": True, "receipt_scope": "COMPLETE_CHAIN",
                            "model_call_count_scope": "SAVED_EXECUTION",
                            "new_calls_this_run": new_calls,
                            "new_calls_this_run_exact": new_calls is not None,
                            "model_decisions": len(receipts)})
        elif outcome.get("failure"):
            failure = outcome["failure"]
            receipt = failure.get("execution_receipt") if isinstance(failure, dict) else None
            if selector_version == V9:
                _require_v9_receipts([receipt] if receipt else [], proof, terminal_only=True)
            if selector_version == V9:
                row.update({"failure_code": failure.get("code") if isinstance(failure, dict) else "UNKNOWN",
                            "model_call_count": None, "model_call_count_exact": False,
                            "model_call_count_scope": "SAVED_EXECUTION",
                            "receipt_count": 1, "receipt_scope": "TERMINAL_ONLY",
                            "terminal_round": receipt["round"],
                            "terminal_receipt_cached": receipt["cached"],
                            "terminal_new_call_lower_bound": int(receipt["cached"] is False)})
                row["new_calls_this_run"] = 0 if executed["replayed"] else None
                row["new_calls_this_run_exact"] = executed["replayed"]
                row["new_calls_this_run_lower_bound"] = (0 if executed["replayed"]
                                                        else row["terminal_new_call_lower_bound"])
                complete_counts = _complete_failure_counts(executed, receipt, proof)
                if complete_counts is not None:
                    row.update(complete_counts)
                    # Saved cache flags do not establish CURRENT_EXECUTE counts.
                    row.pop("new_calls_this_run_lower_bound", None)
        else:
            raise ValueError("The evaluation item has no terminal outcome; no retry was started.")
        report["items"].append(row)
        checkpoint(report)
        print(json.dumps({key: row[key] for key in ("question_id", "state", "elapsed_seconds", "already_terminal")}, ensure_ascii=False), flush=True)
        if get(f"/api/projects/{project}/unresolved-model-calls"):
            raise ValueError("An unresolved call stopped this run; it will not be retried.")
    report["unresolved_calls_at_completion"] = 0
    checkpoint(report)
    return report


def write_checkpoint(path: Path, value: dict) -> None:
    """Preserve the last complete report if serialization or replacement fails."""
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evaluation-id")
    parser.add_argument("--profile-id", choices=("FLASH_NONE", "FLASH_LOW", "PRO"))
    parser.add_argument("--selector-version", choices=(V8, V9), default=V8)
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve it and choose a new report path for an explicit resume.")
    if args.selector_version == V9 and args.profile_id is None:
        parser.error("--selector-version literal-page-selector-9 requires --profile-id.")
    fixture = json.loads(args.questions.read_text(encoding="utf-8"))
    def checkpoint(value):
        write_checkpoint(args.output, value)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180, trust_env=False,
                      headers={"X-CIRP-Client": "browser"}) as client:
        run(client, fixture, [value.strip() for value in args.ids.split(",")], confirmed=args.confirm_live,
            evaluation_id=args.evaluation_id, profile_id=args.profile_id,
            selector_version=args.selector_version, checkpoint=checkpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
