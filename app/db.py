"""SQLite persistence layer.

Answers, per-record judgments and per-item category counts are always written
in the same explicit transaction, so a failure leaves all three untouched.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g

from .judge import CATEGORIES, judge
from .version_def import loads

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    record_no TEXT NOT NULL,
    answers TEXT NOT NULL,
    categories TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (version_id, record_no)
);

CREATE INDEX IF NOT EXISTS idx_records_version ON records(version_id);

CREATE TABLE IF NOT EXISTS item_counts (
    version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    item_index INTEGER NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    exempt INTEGER NOT NULL DEFAULT 0,
    pending INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (version_id, item_index)
);

CREATE TRIGGER IF NOT EXISTS versions_immutable_update
BEFORE UPDATE ON versions
BEGIN
    SELECT RAISE(ABORT, 'versions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS versions_immutable_delete
BEFORE DELETE ON versions
BEGIN
    SELECT RAISE(ABORT, 'versions are immutable');
END;

-- One-shot per-submission feedback for the correction page.  Each token holds
-- the snapshot of exactly one POST (success or failure) so that a tab always
-- sees the result of its own submission even when another tab commits in
-- between.  Tokens are consumed once and pruned when they expire.
CREATE TABLE IF NOT EXISTS correction_results (
    token TEXT PRIMARY KEY,
    version_id INTEGER NOT NULL,
    record_no TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""

RESULT_TTL = timedelta(hours=1)

CATEGORY_COLUMNS = {
    "answered": "answered",
    "missing": "missing",
    "exempt": "exempt",
    "pending": "pending",
}


class DataError(Exception):
    """Validation or business error shown to the user."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.isolation_level = None
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def init_db(path: str | os.PathLike) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.executescript(SCHEMA)
    finally:
        connection.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exception=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def _in_transaction(connection: sqlite3.Connection) -> bool:
    return connection.in_transaction


def _commit(connection: sqlite3.Connection) -> None:
    connection.execute("COMMIT")


# ---------------------------------------------------------------------------
# Versions


def create_version(name: str, source: str, definition: dict) -> int:
    connection = get_db()
    created_at = utcnow()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "INSERT INTO versions (name, source, definition, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                name,
                source,
                json.dumps(definition, ensure_ascii=False, separators=(",", ":")),
                created_at,
            ),
        )
        _commit(connection)
        return int(cursor.lastrowid)
    except Exception:
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise


def list_versions() -> list[dict]:
    connection = get_db()
    rows = connection.execute(
        """
        SELECT v.id, v.name, v.created_at,
               (SELECT COUNT(*) FROM records r WHERE r.version_id = v.id)
                   AS record_count
        FROM versions v
        ORDER BY v.id DESC
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "record_count": row["record_count"],
        }
        for row in rows
    ]


def get_version(version_id: int) -> dict | None:
    connection = get_db()
    row = connection.execute(
        "SELECT * FROM versions WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        return None
    record_count = connection.execute(
        "SELECT COUNT(*) FROM records WHERE version_id = ?", (version_id,)
    ).fetchone()[0]
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "definition": loads(row["definition"]),
        "created_at": row["created_at"],
        "record_count": record_count,
    }


def get_counts(version_id: int, item_count: int) -> list[dict]:
    connection = get_db()
    rows = connection.execute(
        "SELECT * FROM item_counts WHERE version_id = ?", (version_id,)
    ).fetchall()
    by_index = {row["item_index"]: row for row in rows}
    counts = []
    for index in range(item_count):
        row = by_index.get(index)
        entry = {category: 0 for category in CATEGORIES}
        if row is not None:
            for category in CATEGORIES:
                entry[category] = row[CATEGORY_COLUMNS[category]]
        counts.append(entry)
    return counts


# ---------------------------------------------------------------------------
# Records / import


def _serialise_answers(answer_list: list[str]) -> str:
    return json.dumps(answer_list, ensure_ascii=False, separators=(",", ":"))


def _serialise_categories(categories: list[str]) -> str:
    return json.dumps(categories, ensure_ascii=False, separators=(",", ":"))


def import_records(
    version_id: int, rows: list[tuple[str, list[str]]], definition: dict
) -> int:
    """Import validated rows atomically; returns number of records."""
    items = definition["items"]
    connection = get_db()
    counts = [{category: 0 for category in CATEGORIES} for _ in items]
    prepared: list[tuple] = []
    stamp = utcnow()

    for record_no, answers in rows:
        answer_map = {
            index: value
            for index, value in enumerate(answers)
            if value in ("是", "否")
        }
        categories = judge(definition, answer_map)
        for index, category in enumerate(categories):
            counts[index][category] += 1
        prepared.append(
            (
                version_id,
                record_no,
                _serialise_answers(answers),
                _serialise_categories(categories),
                stamp,
            )
        )

    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT COUNT(*) FROM records WHERE version_id = ?", (version_id,)
        ).fetchone()[0]
        if existing:
            raise DataError("该表版已导入记录，不能重复导入。")

        connection.executemany(
            "INSERT INTO records"
            " (version_id, record_no, answers, categories, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            prepared,
        )
        connection.executemany(
            "INSERT INTO item_counts"
            " (version_id, item_index, answered, missing, exempt, pending)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    version_id,
                    index,
                    entry["answered"],
                    entry["missing"],
                    entry["exempt"],
                    entry["pending"],
                )
                for index, entry in enumerate(counts)
            ],
        )
        _commit(connection)
        return len(prepared)
    except Exception:
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise


def get_record(version_id: int, record_no: str) -> dict | None:
    connection = get_db()
    row = connection.execute(
        "SELECT * FROM records WHERE version_id = ? AND record_no = ?",
        (version_id, record_no),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "version_id": row["version_id"],
        "record_no": row["record_no"],
        "answers": json.loads(row["answers"]),
        "categories": json.loads(row["categories"]),
        "updated_at": row["updated_at"],
    }


def correct_answer(
    version_id: int, record_no: str, item_index: int, value: str
) -> dict:
    """Change one answer, then re-judge only that record and update counts.

    Returns a snapshot of the committed state (new answers, categories and
    update time).  The record update and the count update share one
    transaction; on failure the exception propagates and nothing changes.
    """
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")

        version_row = connection.execute(
            "SELECT definition FROM versions WHERE id = ?", (version_id,)
        ).fetchone()
        if version_row is None:
            raise DataError("表版不存在。")
        definition = loads(version_row["definition"])

        row = connection.execute(
            "SELECT answers, categories FROM records"
            " WHERE version_id = ? AND record_no = ?",
            (version_id, record_no),
        ).fetchone()
        if row is None:
            raise DataError("记录号不存在。")

        if not 0 <= item_index < len(definition["items"]):
            raise DataError("检查项不存在。")
        if value not in ("是", "否", ""):
            raise DataError("答案只能是是、否或留空。")

        answers = json.loads(row["answers"])
        old_categories = json.loads(row["categories"])
        while len(answers) < len(definition["items"]):
            answers.append("")
        answers[item_index] = value

        answer_map = {
            index: answer
            for index, answer in enumerate(answers)
            if answer in ("是", "否")
        }
        new_categories = judge(definition, answer_map)
        updated_at = utcnow()

        connection.execute(
            "UPDATE records SET answers = ?, categories = ?, updated_at = ?"
            " WHERE version_id = ? AND record_no = ?",
            (
                _serialise_answers(answers),
                _serialise_categories(new_categories),
                updated_at,
                version_id,
                record_no,
            ),
        )

        for index, (old, new) in enumerate(zip(old_categories, new_categories)):
            if old == new:
                continue
            connection.execute(
                f"UPDATE item_counts SET {CATEGORY_COLUMNS[old]} ="
                f" {CATEGORY_COLUMNS[old]} - 1, {CATEGORY_COLUMNS[new]} ="
                f" {CATEGORY_COLUMNS[new]} + 1"
                " WHERE version_id = ? AND item_index = ?",
                (version_id, index),
            )

        _commit(connection)
        return {
            "answers": answers,
            "categories": new_categories,
            "updated_at": updated_at,
        }
    except Exception:
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# One-shot correction feedback (shared across worker processes via SQLite)


def save_correction_result(
    version_id: int, record_no: str, kind: str, payload: dict
) -> str:
    """Store a one-time feedback snapshot and return its opaque token."""
    connection = get_db()
    token = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    expires_at = (now + RESULT_TTL).isoformat(timespec="seconds")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM correction_results WHERE expires_at <= ?",
            (now.isoformat(timespec="seconds"),),
        )
        connection.execute(
            "INSERT INTO correction_results"
            " (token, version_id, record_no, kind, payload, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                token,
                version_id,
                record_no,
                kind,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                expires_at,
            ),
        )
        _commit(connection)
    except Exception:
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise
    return token


def consume_correction_result(
    token: str, version_id: int, record_no: str
) -> dict | None:
    """Return and delete a one-time feedback snapshot, scoped to this record.

    A token can only be consumed once; reloading the page (or opening it in
    another tab) therefore shows the latest committed data without a banner.
    """
    connection = get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT kind, payload, expires_at FROM correction_results"
            " WHERE token = ? AND version_id = ? AND record_no = ?",
            (token, version_id, record_no),
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None
        connection.execute(
            "DELETE FROM correction_results WHERE token = ?", (token,)
        )
        connection.execute("COMMIT")
    except Exception:
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise

    if row["expires_at"] <= datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ):
        return None
    result = json.loads(row["payload"])
    result["kind"] = row["kind"]
    return result
