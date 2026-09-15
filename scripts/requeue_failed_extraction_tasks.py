"""Safely requeue known extraction failures in a paused CIRP run.

The command is a dry-run unless ``--apply`` is supplied.  It never sends an
API request, changes a model call, or changes a budget account.  A billed
contract failure receives an explicit, audited billing generation before its
evidence is returned to ``PENDING``.  A known input-budget rejection that
happened before reservation is returned to generation zero.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import (Database, DomainError, MAX_PAID_TASK_CALLS, dumps, now,
                    paid_task_family, paid_task_generation, paid_task_key, uid)
from scripts.requeue_failed_visual_tasks import ReadOnlyDatabase, RESUMABLE_PAUSED_STATES


CONTRACT_EVIDENCE_ERROR = "模型结果未通过契约；已记录本次费用，不自动重复请求。"
INPUT_BUDGET_EVIDENCE_ERROR = "片段加Schema超出简单任务输入预算；需细分，未调用API"
REQUEUE_REASON = "manual_requeue_after_extraction_contract_fix"
HISTORY_KEY = "extraction_requeue_history"
KNOWN_CONTRACT_EXCEPTIONS = frozenset({
    "ValueError",
    "JSONDecodeError",
    "ValidationError",
    "InvalidModelOutput",
})


def _json_object(raw: str | None, label: str) -> dict:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError(f"{label}不是有效 JSON；未做任何修改", 409) from exc
    if not isinstance(value, dict):
        raise DomainError(f"{label}不是 JSON 对象；未做任何修改", 409)
    return value


def _evidence_metadata(row: sqlite3.Row) -> dict:
    payload = _json_object(row["payload"], f"证据 {row['id']} 的 payload")
    family = payload.get("evidence_id")
    if not isinstance(family, str) or not family.startswith("EV-"):
        raise DomainError(f"证据 {row['id']} 缺少有效 evidence_id；未做任何修改", 409)
    if payload.get("document_id") != row["document_id"]:
        raise DomainError(f"证据 {row['id']} 的文档标识不一致；未做任何修改", 409)
    locator = payload.get("locator")
    if not isinstance(locator, dict):
        raise DomainError(f"证据 {row['id']} 缺少有效 locator；未做任何修改", 409)
    page = locator.get("page_number")
    if page is not None and (type(page) is not int or page < 1):
        raise DomainError(f"证据 {row['id']} 的页码无效；未做任何修改", 409)
    method = payload.get("extraction_method")
    if not isinstance(method, str) or not method:
        raise DomainError(f"证据 {row['id']} 缺少提取方法；未做任何修改", 409)
    return {"family": family, "page_number": page, "extraction_method": method}


def _family_calls(connection: sqlite3.Connection, run_id: str, family: str) -> list[dict]:
    rows = connection.execute(
        """SELECT id,task_key,state,actual_units,response,error,created_at
           FROM model_calls WHERE run_id=? ORDER BY created_at,id""",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows if paid_task_family(row["task_key"]) == family]


def _recovery_events(connection: sqlite3.Connection, run_id: str, family: str) -> list[dict]:
    rows = connection.execute(
        """SELECT id,call_id,payload,created_at FROM call_reconciliation_events
           WHERE run_id=? ORDER BY created_at,id""",
        (run_id,),
    ).fetchall()
    result = []
    for row in rows:
        payload = _json_object(row["payload"], f"恢复事件 {row['id']} 的 payload")
        if payload.get("event_type") == "RECOVERY_GENERATION_AUTHORIZED" and payload.get("task_family") == family:
            result.append({**dict(row), "payload": payload})
    return result


def _known_contract_retry(connection: sqlite3.Connection, run: sqlite3.Row,
                          row: sqlite3.Row, metadata: dict) -> dict:
    family = metadata["family"]
    calls = _family_calls(connection, run["id"], family)
    if not calls:
        raise DomainError(f"证据 {row['id']} 标为已收费契约失败，但没有模型调用；未做任何修改", 409)
    generations = [paid_task_generation(call["task_key"]) for call in calls]
    if len(set(generations)) != len(generations):
        raise DomainError(f"证据 {row['id']} 的付费代次重复；未做任何修改", 409)
    if sorted(generations) != list(range(max(generations) + 1)):
        raise DomainError(f"证据 {row['id']} 的付费代次不连续；未做任何修改", 409)
    if any(call["task_key"] != paid_task_key(family, paid_task_generation(call["task_key"])) for call in calls):
        raise DomainError(f"证据 {row['id']} 的付费任务键无效；未做任何修改", 409)
    diagnostics = {}
    for call in calls:
        if (call["state"] != "SETTLED_ERROR" or call["actual_units"] is None
                or call["response"] is not None):
            raise DomainError(f"证据 {row['id']} 的任务族含有非契约失败调用；未做任何修改", 409)
        diagnostic = _json_object(call["error"], f"模型调用 {call['id']} 的诊断")
        if (diagnostic.get("kind") != "CONTRACT_ERROR"
                or diagnostic.get("class") != "EXTRACTION_RESULT"
                or diagnostic.get("exception") not in KNOWN_CONTRACT_EXCEPTIONS):
            raise DomainError(f"证据 {row['id']} 不是已知抽取契约错误；未做任何修改", 409)
        diagnostics[call["id"]] = diagnostic
    events = _recovery_events(connection, run["id"], family)
    by_generation: dict[int, list[dict]] = {}
    for event in events:
        generation = event["payload"].get("generation")
        if type(generation) is int:
            by_generation.setdefault(generation, []).append(event)
    for generation in range(1, max(generations) + 1):
        matching = by_generation.get(generation, [])
        if (len(matching) != 1
                or matching[0]["payload"].get("next_task_key") != paid_task_key(family, generation)):
            raise DomainError(f"证据 {row['id']} 的既有付费代次缺少唯一恢复授权；未做任何修改", 409)
    latest = max(calls, key=lambda call: paid_task_generation(call["task_key"]))
    diagnostic = diagnostics[latest["id"]]
    generation = paid_task_generation(latest["task_key"]) + 1
    if len(calls) >= MAX_PAID_TASK_CALLS or generation >= MAX_PAID_TASK_CALLS:
        raise DomainError(f"证据 {row['id']} 已达到三次累计调用上限；未做任何修改", 409)
    next_key = paid_task_key(family, generation)
    if any(event["payload"].get("generation") >= generation for event in events
           if type(event["payload"].get("generation")) is int):
        raise DomainError(f"证据 {row['id']} 已存在当前或更高恢复授权；未做任何修改", 409)
    safe_diagnostic = {key: diagnostic[key] for key in ("kind", "class", "exception", "path", "validator")
                       if key in diagnostic}
    return {
        "failure_type": "BILLED_CONTRACT_ERROR",
        "current_generation": generation - 1,
        "next_generation": generation,
        "next_task_key": next_key,
        "source_call_id": latest["id"],
        "source_task_key": latest["task_key"],
        "source_state": latest["state"],
        "diagnostic": safe_diagnostic,
        "billable_retry": True,
        **metadata,
    }


def _known_input_retry(connection: sqlite3.Connection, run: sqlite3.Row,
                       row: sqlite3.Row, metadata: dict) -> dict:
    family = metadata["family"]
    if _family_calls(connection, run["id"], family):
        raise DomainError(f"证据 {row['id']} 标为发送前拒绝，但已有模型调用；未做任何修改", 409)
    if _recovery_events(connection, run["id"], family):
        raise DomainError(f"证据 {row['id']} 标为发送前拒绝，但已有恢复授权；未做任何修改", 409)
    return {
        "failure_type": "PRE_REQUEST_INPUT_BUDGET",
        "current_generation": 0,
        "next_generation": 0,
        "next_task_key": family,
        "source_call_id": None,
        "source_task_key": None,
        "source_state": None,
        "diagnostic": {"kind": "LOCAL_INPUT_BUDGET", "class": "PRE_REQUEST_REJECTED"},
        "billable_retry": False,
        **metadata,
    }


def _document_summaries(connection: sqlite3.Connection, run_id: str,
                        document_ids: set[str]) -> dict[str, dict]:
    summaries = {}
    for document_id in sorted(document_ids):
        row = connection.execute(
            "SELECT summary FROM document_results WHERE run_id=? AND document_id=?",
            (run_id, document_id),
        ).fetchone()
        if not row:
            raise DomainError(f"文档 {document_id} 缺少解析摘要；未做任何修改", 409)
        summary = _json_object(row["summary"], f"文档 {document_id} 的解析摘要")
        history = summary.get(HISTORY_KEY, [])
        if not isinstance(history, list):
            raise DomainError(f"文档 {document_id} 的抽取重排历史格式无效；未做任何修改", 409)
        summaries[document_id] = summary
    return summaries


def requeue_failed_extraction_tasks(db: Database | ReadOnlyDatabase, run_id: str,
                                    *, apply: bool = False) -> dict:
    """Preview or atomically requeue only recognized extraction failures."""
    with db.connect(write=apply) as connection:
        run = connection.execute(
            "SELECT id,project_id,status FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run:
            raise DomainError("未找到分析运行", 404)
        if run["status"] not in RESUMABLE_PAUSED_STATES:
            raise DomainError("仅可恢复的暂停运行可重排抽取失败任务", 409)
        unresolved = connection.execute(
            "SELECT COUNT(*) FROM model_calls WHERE project_id=? AND actual_units IS NULL",
            (run["project_id"],),
        ).fetchone()[0]
        if unresolved:
            raise DomainError("项目有待对账 API 请求；未重排，也不会自动再次付费", 409)

        rows = connection.execute(
            """SELECT id,document_id,status,error,payload FROM evidence
               WHERE run_id=? AND status='NEEDS_REVIEW' AND error IN (?,?) ORDER BY id""",
            (run_id, CONTRACT_EVIDENCE_ERROR, INPUT_BUDGET_EVIDENCE_ERROR),
        ).fetchall()
        prepared = []
        for row in rows:
            metadata = _evidence_metadata(row)
            retry = (_known_contract_retry(connection, run, row, metadata)
                     if row["error"] == CONTRACT_EVIDENCE_ERROR
                     else _known_input_retry(connection, run, row, metadata))
            prepared.append({"row": row, **retry})
        summaries = _document_summaries(connection, run_id, {item["row"]["document_id"] for item in prepared})

        public_tasks = []
        recorded_at = now() if apply and prepared else None
        for item in prepared:
            recovery_event_id = None
            if apply and item["billable_retry"]:
                recovery_event_id = uid("RECOVERY")
                event_payload = {
                    "event_type": "RECOVERY_GENERATION_AUTHORIZED",
                    "policy": "EXPLICIT_ONLY_MAX_3",
                    "task_family": item["family"],
                    "generation": item["next_generation"],
                    "attempt_number": item["next_generation"] + 1,
                    "source_call_id": item["source_call_id"],
                    "source_task_key": item["source_task_key"],
                    "source_state": item["source_state"],
                    "source_diagnostic": item["diagnostic"],
                    "trigger": "RUN_RESUME",
                    "context_id": f"manual-extraction-contract:{item['row']['id']}:{item['next_generation']}",
                    "next_task_key": item["next_task_key"],
                    "authorization_reason": REQUEUE_REASON,
                }
                connection.execute(
                    "INSERT INTO call_reconciliation_events VALUES(?,?,?,?,?,?,?)",
                    (recovery_event_id, item["source_call_id"], run["project_id"], run_id,
                     "local-user", dumps(event_payload), recorded_at),
                )

            if apply:
                summary = summaries[item["row"]["document_id"]]
                history = summary.setdefault(HISTORY_KEY, [])
                history.append({
                    "evidence_id": item["family"],
                    "page_number": item["page_number"],
                    "extraction_method": item["extraction_method"],
                    "previous_status": item["row"]["status"],
                    "failure_type": item["failure_type"],
                    "diagnostic": item["diagnostic"],
                    "source_call_id": item["source_call_id"],
                    "source_task_key": item["source_task_key"],
                    "current_generation": item["current_generation"],
                    "next_generation": item["next_generation"],
                    "next_task_key": item["next_task_key"],
                    "recovery_event_id": recovery_event_id,
                    "recorded_at": recorded_at,
                    "reason": REQUEUE_REASON,
                })
                connection.execute(
                    "UPDATE evidence SET status='PENDING',error='' WHERE id=?", (item["row"]["id"],)
                )

            public_tasks.append({
                "document_id": item["row"]["document_id"],
                "page_number": item["page_number"],
                "extraction_method": item["extraction_method"],
                "failure_type": item["failure_type"],
                "current_generation": item["current_generation"],
                "next_generation": item["next_generation"],
                "billable_retry": item["billable_retry"],
            })

        if apply:
            for document_id, summary in summaries.items():
                connection.execute(
                    "UPDATE document_results SET summary=? WHERE run_id=? AND document_id=?",
                    (dumps(summary), run_id, document_id),
                )

    return {
        "run_id": run_id,
        "mode": "APPLIED" if apply else "DRY_RUN",
        "requeued_count": len(public_tasks),
        "tasks": public_tasks,
        "guarantees": [
            "只处理精确匹配的已知抽取契约错误或发送前输入预算拒绝",
            "未修改 model_calls 或预算账户，也未发送 API 请求",
            "已收费契约失败使用显式新代次，gen0 至 gen2 累计最多三次",
            "发送前输入预算拒绝没有模型调用，仍使用 gen0",
            "原失败诊断保存在模型调用或文档抽取重排历史中",
            "未修改运行状态",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="预览或重排暂停 CIRP 运行中的已知抽取失败任务")
    parser.add_argument("--run-id", required=True, help="必须指定的暂停运行 ID")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local", help="CIRP 数据目录（默认 .local）")
    parser.add_argument("--apply", action="store_true", help="明确执行重排；缺省为不写入的 dry-run")
    args = parser.parse_args()
    try:
        database_path = (args.data_dir / "cirp.sqlite3").resolve()
        if not database_path.is_file():
            raise DomainError("指定数据目录中不存在既有 cirp.sqlite3；未创建任何文件", 404)
        if not args.apply and Path(str(database_path) + "-wal").exists():
            raise DomainError("检测到活动 WAL；请停止本地服务后再执行只读预览，未创建任何文件", 409)
        database = Database(database_path) if args.apply else ReadOnlyDatabase(database_path)
        outcome = requeue_failed_extraction_tasks(database, args.run_id, apply=args.apply)
    except (DomainError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", 409)}, ensure_ascii=False))
        return 2
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
