"""Fail-closed schema26 to schema27 migration for reference results."""
from __future__ import annotations

import sqlite3

from app.settings import ROOT


VERSION = 27
_STAGING = "reference_results__migration27"
_OLD_COLUMNS = (
    "id", "result_key", "project_id", "run_id", "snapshot_id", "qa_version",
    "question", "question_key", "status", "answer_basis", "provider", "model",
    "result_hash", "result_json", "review_status", "review_version",
    "review_event_id", "created_at", "updated_at",
)
_NEW_COLUMNS = _OLD_COLUMNS[:8] + ("result_kind",) + _OLD_COLUMNS[8:]


def _sql(value: str) -> str:
    """Normalize only whitespace and SQLite's ALTER TABLE table-name quotes."""
    compact = " ".join(value.split())
    return compact.replace('CREATE TABLE "reference_results"', "CREATE TABLE reference_results")


def _table_info(connection: sqlite3.Connection, table: str):
    return [tuple(row[1:6]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _indexes(connection: sqlite3.Connection) -> dict[str, str]:
    return {row[0]: row[1] for row in connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='reference_results' "
        "AND sql IS NOT NULL"
    )}


def _has_result_trigger(connection: sqlite3.Connection) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='reference_results'"
    ).fetchone() is not None


def _expected_sql(table: str) -> list[str]:
    text = (ROOT / "migrations/027_reference_projection_results.sql").read_text(encoding="utf-8")
    return [
        item.strip().replace("{{TABLE}}", table)
        .replace("{{PROJECT_INDEX}}", "ix_reference_results_project")
        .replace("{{RUN_INDEX}}", "ix_reference_results_run")
        for item in text.split(";") if item.strip()
    ]


def _shape(connection: sqlite3.Connection, version: int) -> bool:
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reference_results'"
    ).fetchone()
    if table is None or _has_result_trigger(connection):
        return False
    columns = tuple(name for name, *_ in _table_info(connection, "reference_results"))
    if version == 26:
        if columns != _OLD_COLUMNS or _indexes(connection) != {
            "ix_reference_results_project": "CREATE INDEX ix_reference_results_project\n ON reference_results(project_id,created_at DESC,id)",
            "ix_reference_results_run": "CREATE INDEX ix_reference_results_run\n ON reference_results(run_id,created_at DESC,id)",
        }:
            return False
        legacy = (ROOT / "migrations/011_reference_results.sql").read_text(encoding="utf-8")
        expected = legacy[legacy.index("CREATE TABLE"):].split(";", 1)[0].replace(
            "CREATE TABLE IF NOT EXISTS", "CREATE TABLE", 1
        )
        return _sql(table[0]) == _sql(expected)
    if columns != _NEW_COLUMNS:
        return False
    expected = _expected_sql("reference_results")
    return _sql(table[0]) == _sql(expected[0]) and _indexes(connection) == {
        "ix_reference_results_project": expected[1],
        "ix_reference_results_run": expected[2],
    }


def _marker(connection: sqlite3.Connection, version: int) -> bool:
    return connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
    ).fetchone() is not None


def _has_partial_reference_results(connection: sqlite3.Connection) -> bool:
    names = ("reference_results", "reference_result_citations", "reference_result_review_events", _STAGING)
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name IN (?,?,?,?)", names
    ).fetchone() is not None


def preflight_reference_projection_results(connection: sqlite3.Connection) -> None:
    """Validate the pre-011 state under a lock so IF NOT EXISTS cannot repair drift."""
    if connection.in_transaction:
        raise RuntimeError("migration27 preflight requires no caller transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        has11 = _marker(connection, 11)
        has27 = _marker(connection, VERSION)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (_STAGING,)).fetchone():
            raise RuntimeError("migration27 residual staging table")
        if has27:
            if not has11 or not _shape(connection, VERSION):
                raise RuntimeError("migration27 marker/table shape drift")
        elif has11:
            if not _shape(connection, 26):
                raise RuntimeError("migration27 requires exact schema26 without marker")
        elif _has_partial_reference_results(connection):
            raise RuntimeError("migration27 markerless reference-results partial state")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("migration27 preflight foreign-key check failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def install_reference_projection_results(connection: sqlite3.Connection) -> None:
    """Atomically rebuild exactly schema26 ``reference_results`` into schema27."""
    if connection.in_transaction:
        raise RuntimeError("migration27 requires no caller transaction")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (_STAGING,)).fetchone():
                raise RuntimeError("migration27 residual staging table")
            if _marker(connection, VERSION):
                if not _marker(connection, 11) or not _shape(connection, VERSION):
                    raise RuntimeError("migration27 marker/table shape drift")
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("migration27 marker foreign-key check failed")
                connection.commit()
                return
            if not _shape(connection, 26):
                raise RuntimeError("migration27 requires exact schema26 without marker")
            old_count = connection.execute("SELECT COUNT(*) FROM reference_results").fetchone()[0]
            create, _project, _run = _expected_sql(_STAGING)
            connection.execute(create)
            old_names = ",".join(_OLD_COLUMNS)
            new_names = ",".join(_NEW_COLUMNS)
            source = ",".join(_OLD_COLUMNS[:8]) + ",'REFERENCE_QA_RESULT'," + ",".join(_OLD_COLUMNS[8:])
            connection.execute(
                f"INSERT INTO {_STAGING} (rowid,{new_names}) "
                f"SELECT rowid,{source} FROM reference_results ORDER BY rowid"
            )
            typed = "rowid," + old_names + "," + ",".join(f"typeof({name})" for name in _OLD_COLUMNS)
            if (
                connection.execute(
                    f"SELECT {typed} FROM reference_results EXCEPT SELECT {typed} FROM {_STAGING}"
                ).fetchone()
                or connection.execute(
                    f"SELECT {typed} FROM {_STAGING} EXCEPT SELECT {typed} FROM reference_results"
                ).fetchone()
            ):
                raise RuntimeError("migration27 copied legacy values inconsistently")
            connection.execute("DROP TABLE reference_results")
            connection.execute(f"ALTER TABLE {_STAGING} RENAME TO reference_results")
            _create, project_index, run_index = _expected_sql("reference_results")
            connection.execute(project_index)
            connection.execute(run_index)
            if (
                not _shape(connection, VERSION)
                or connection.execute("SELECT COUNT(*) FROM reference_results").fetchone()[0] != old_count
                or connection.execute(
                    "SELECT COUNT(*) FROM reference_results WHERE result_kind!='REFERENCE_QA_RESULT'"
                ).fetchone()[0]
                or connection.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise RuntimeError("migration27 verification failed")
            connection.execute("INSERT INTO schema_migrations VALUES(27,datetime('now'))")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
