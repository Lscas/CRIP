"""Safely requeue only failed visual-contract tasks in a paused CIRP run.

The tool deliberately never inserts, updates, or reconciles ``model_calls``.
It defaults to a read-only preview; use ``--apply`` only after reviewing it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database, DomainError, dumps, now


REQUEUE_REASON = "manual_requeue_after_contract_normalization"
RESUMABLE_PAUSED_STATES = frozenset({"PAUSED", "PAUSED_PROVIDER", "PAUSED_BUDGET", "INTERRUPTED"})


class ReadOnlyDatabase:
    """Minimal Database-compatible reader that cannot create or migrate a DB."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write:
            raise RuntimeError("只读数据库不可写入")
        # immutable=1 keeps SQLite from creating -wal/-shm lock artifacts.
        # The tool is intentionally used only after the local service has been
        # paused/stopped, so it does not need visibility into concurrent writes.
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def _result_summary(raw: str, document_id: str) -> dict:
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DomainError(f"文档 {document_id} 的解析摘要不是有效 JSON；未做任何修改", 409) from exc
    if not isinstance(summary, dict) or not isinstance(summary.get("visual_tasks"), list):
        raise DomainError(f"文档 {document_id} 缺少 visual_tasks；未做任何修改", 409)
    return summary


def _failed_tasks(summary: dict, document_id: str) -> list[dict]:
    candidates: list[dict] = []
    for index, task in enumerate(summary["visual_tasks"]):
        if not isinstance(task, dict):
            raise DomainError(f"文档 {document_id} 的第 {index + 1} 个视觉任务格式无效；未做任何修改", 409)
        if task.get("status") == "FAILED_CONTRACT":
            page = task.get("page")
            if type(page) is not int or page < 1:
                raise DomainError(f"文档 {document_id} 的失败视觉任务没有有效页码；未做任何修改", 409)
            generation = task.get("billing_generation", 0)
            if type(generation) is not int or not 0 <= generation <= 2:
                raise DomainError(f"文档 {document_id} 第 {page} 页的付费代次无效；未做任何修改", 409)
            if generation >= 2:
                raise DomainError(f"文档 {document_id} 第 {page} 页已达到三次累计调用上限；未做任何修改", 409)
            candidates.append({"index": index, "page": page, "task": task,
                               "billing_generation": generation})
    return candidates


def requeue_failed_contract_tasks(db: Database | ReadOnlyDatabase, run_id: str, *, apply: bool = False,
                                  reopen_partial: bool = False) -> dict:
    """Preview or atomically requeue FAILED_CONTRACT visual tasks for one paused run.

    Preconditions are checked in the same write transaction used for ``apply``.
    This means an unresolved call or run state transition cannot race an update.
    """
    with db.connect(write=apply) as connection:
        run = connection.execute("SELECT id,project_id,status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise DomainError("未找到分析运行", 404)
        reopen_run = run["status"] == "PARTIAL" and reopen_partial
        if run["status"] not in RESUMABLE_PAUSED_STATES and not reopen_run:
            if run["status"] == "PARTIAL":
                raise DomainError("PARTIAL 运行必须明确使用 reopen_partial 才能重排并改为 PAUSED", 409)
            raise DomainError("仅可恢复的暂停运行可重排失败视觉任务", 409)
        unresolved = connection.execute(
            "SELECT COUNT(*) FROM model_calls WHERE project_id=? AND actual_units IS NULL", (run["project_id"],)
        ).fetchone()[0]
        if unresolved:
            raise DomainError("项目有待对账 API 请求；未重排，也不会自动再次付费", 409)

        rows = connection.execute(
            "SELECT document_id,status,summary FROM document_results WHERE run_id=? ORDER BY document_id", (run_id,)
        ).fetchall()
        prepared: list[tuple[object, dict, list[dict]]] = []
        preview: list[dict] = []
        for row in rows:
            summary = _result_summary(row["summary"], row["document_id"])
            failed = _failed_tasks(summary, row["document_id"])
            if failed:
                prepared.append((row, summary, failed))
                preview.extend({"document_id": row["document_id"], "page": item["page"],
                                "billing_generation": item["billing_generation"] + 1} for item in failed)

        if apply:
            recorded_at = now()
            for row, summary, failed in prepared:
                for item in failed:
                    task = item["task"]
                    history = task.get("attempt_history", [])
                    if not isinstance(history, list):
                        raise DomainError(f"文档 {row['document_id']} 的 attempt_history 格式无效；未做任何修改", 409)
                    history.append({
                        "status": task.get("status"),
                        "error": task.get("error"),
                        "billing_generation": item["billing_generation"],
                        "recorded_at": recorded_at,
                        "reason": REQUEUE_REASON,
                    })
                    task["attempt_history"] = history
                    # The paid gateway treats each explicit generation as a
                    # distinct task key.  A crash can recover the same
                    # generation, while only this audited mutation authorizes
                    # a new provider call.
                    task["billing_generation"] = item["billing_generation"] + 1
                    task["status"] = "PENDING"
                    task.pop("error", None)
                    task["requeued_at"] = recorded_at
                    task["requeue_reason"] = REQUEUE_REASON
                connection.execute(
                    "UPDATE document_results SET summary=? WHERE run_id=? AND document_id=?",
                    (dumps(summary), run_id, row["document_id"]),
                )
            if reopen_run and preview:
                connection.execute(
                    "UPDATE runs SET status='PAUSED',stop_requested=0,message=? WHERE id=?",
                    ("已明确重开已结束运行并重排失败视觉任务；等待人工恢复", run_id),
                )

    guarantees = [
        "只处理 FAILED_CONTRACT 的 visual_tasks",
        "未修改 model_calls 或预算账户",
        "每项重排均保留旧状态、错误、付费代次、时间和原因",
        "每次显式重排生成新的付费任务代次，累计最多三次",
    ]
    if reopen_run:
        guarantees.append("仅在明确 reopen_partial 且实际有重排项时将 PARTIAL 原子改为 PAUSED")
    else:
        guarantees.append("未修改运行状态")
    return {
        "run_id": run_id,
        "mode": "APPLIED" if apply else "DRY_RUN",
        "requeued_count": len(preview),
        "tasks": preview,
        "guarantees": guarantees,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="预览或重排暂停 CIRP 运行中的 FAILED_CONTRACT 视觉任务")
    parser.add_argument("--run-id", required=True, help="必须指定的 PAUSED 运行 ID")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local", help="CIRP 数据目录（默认 .local）")
    parser.add_argument("--apply", action="store_true", help="明确执行重排；缺省为不写入的 dry-run")
    parser.add_argument("--reopen-partial", action="store_true",
                        help="明确允许有失败项的 PARTIAL 运行在执行时原子改为 PAUSED")
    args = parser.parse_args()
    try:
        database_path = (args.data_dir / "cirp.sqlite3").resolve()
        # Never make a data directory, database, WAL file, or migration during
        # a dry-run.  Apply also insists on an existing regular database file,
        # so a typo cannot initialise a separate empty CIRP installation.
        if not database_path.is_file():
            raise DomainError("指定数据目录中不存在既有 cirp.sqlite3；未创建任何文件", 404)
        if not args.apply and Path(str(database_path) + "-wal").exists():
            raise DomainError("检测到活动 WAL；请停止本地服务后再执行只读预览，未创建任何文件", 409)
        database = Database(database_path) if args.apply else ReadOnlyDatabase(database_path)
        outcome = requeue_failed_contract_tasks(database, args.run_id, apply=args.apply,
                                                 reopen_partial=args.reopen_partial)
    except (DomainError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", 409)}, ensure_ascii=False))
        return 2
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
