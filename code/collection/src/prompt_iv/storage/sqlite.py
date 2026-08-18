from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import csv
import hashlib
import json
import re
import sqlite3


SCHEMA_VERSION = 3
TEXT_COLUMN_SUFFIX = "_text"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or "", flags=re.UNICODE))


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_without_text(row: dict[str, Any], text_keys: Iterable[str] = ("generated_text",)) -> str:
    payload = {key: value for key, value in row.items() if key not in set(text_keys)}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _bool_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(bool(value))
    return int(str(value).strip().lower() in {"1", "true", "yes", "sim"})


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _relpath(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _parse_meeting_number(path: Path) -> int | None:
    match = re.search(r"ata(\d+)", path.stem, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _iter_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    rows: list[tuple[int, dict[str, Any]]] = []
    bad_json = 0
    if not path.exists():
        return rows, bad_json
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append((line_number, json.loads(stripped)))
            except json.JSONDecodeError:
                bad_json += 1
    return rows, bad_json


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect_database(db_path: str | Path, root: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path)
    if root is not None and not path.is_absolute():
        path = Path(root) / path
    conn = _connect(path)
    initialize_schema(conn)
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS minutes (
            meeting_number INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            word_count INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            modified_at TEXT,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            run_dir TEXT NOT NULL,
            first_meeting INTEGER,
            last_meeting INTEGER,
            prompt_versions_json TEXT,
            stances_json TEXT,
            rewrite_tasks_total INTEGER NOT NULL DEFAULT 0,
            rewrite_result_lines INTEGER NOT NULL DEFAULT 0,
            rewrite_completed_latest INTEGER NOT NULL DEFAULT 0,
            rewrite_bad_json_lines INTEGER NOT NULL DEFAULT 0,
            agent_tasks_total INTEGER NOT NULL DEFAULT 0,
            agent_result_lines INTEGER NOT NULL DEFAULT 0,
            agent_completed_latest INTEGER NOT NULL DEFAULT 0,
            agent_bad_json_lines INTEGER NOT NULL DEFAULT 0,
            corpus_rows INTEGER NOT NULL DEFAULT 0,
            tone_rows INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT,
            ingested_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS run_files (
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            size_bytes INTEGER,
            modified_at TEXT,
            PRIMARY KEY (run_id, kind, path),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rewrite_tasks (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            meeting_number INTEGER,
            source_path TEXT,
            iteration INTEGER,
            block_index INTEGER,
            total_blocks INTEGER,
            stance TEXT,
            prompt_version TEXT,
            input_hash TEXT,
            original_text_hash TEXT,
            system_prompt_hash TEXT,
            user_prompt_hash TEXT,
            char_count INTEGER,
            word_count INTEGER,
            numeric_token_count INTEGER,
            numeric_unique_count INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rewrite_result_events (
            run_id TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            task_id TEXT,
            status TEXT,
            created_at TEXT,
            meeting_number INTEGER,
            source_path TEXT,
            iteration INTEGER,
            block_index INTEGER,
            total_blocks INTEGER,
            stance TEXT,
            prompt_version TEXT,
            runner TEXT,
            runner_dry_run INTEGER,
            model TEXT,
            reasoning_effort TEXT,
            temperature REAL,
            manifest_input_hash TEXT,
            request_input_hash TEXT,
            output_text_hash TEXT,
            numeric_ok INTEGER,
            numeric_missing_json TEXT,
            numeric_extra_json TEXT,
            retry_attempts_used INTEGER,
            generated_text TEXT,
            response_metadata_json TEXT,
            error_type TEXT,
            error TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, line_number),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rewrite_results_latest (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_line_number INTEGER NOT NULL,
            status TEXT,
            created_at TEXT,
            meeting_number INTEGER,
            source_path TEXT,
            iteration INTEGER,
            block_index INTEGER,
            total_blocks INTEGER,
            stance TEXT,
            prompt_version TEXT,
            model TEXT,
            reasoning_effort TEXT,
            temperature REAL,
            manifest_input_hash TEXT,
            request_input_hash TEXT,
            output_text_hash TEXT,
            numeric_ok INTEGER,
            generated_text TEXT,
            retry_attempts_used INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id, event_line_number)
                REFERENCES rewrite_result_events(run_id, line_number)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_tasks (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            meeting_number INTEGER,
            corpus_iteration INTEGER,
            scenario TEXT,
            forecaster_type TEXT,
            agent_id INTEGER,
            agent_seed INTEGER,
            sequence_index INTEGER,
            state_group TEXT,
            source_corpus TEXT,
            source_path TEXT,
            initial_expectation REAL,
            inflation_target REAL,
            previous_expectation_required INTEGER,
            prompt_family_hash TEXT,
            design TEXT,
            prompt_version TEXT,
            prior_source TEXT,
            fixed_prior REAL,
            pair_id TEXT,
            source_run_id TEXT,
            source_role TEXT,
            source_text_hash TEXT,
            requested_model TEXT,
            requested_reasoning_effort TEXT,
            requested_temperature REAL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_result_events (
            run_id TEXT NOT NULL,
            line_number INTEGER NOT NULL,
            task_id TEXT,
            status TEXT,
            created_at TEXT,
            meeting_number INTEGER,
            corpus_iteration INTEGER,
            scenario TEXT,
            forecaster_type TEXT,
            agent_id INTEGER,
            agent_seed INTEGER,
            sequence_index INTEGER,
            state_group TEXT,
            previous_expectation REAL,
            new_expectation REAL,
            perceived_tone REAL,
            inflation_target REAL,
            runner TEXT,
            runner_dry_run INTEGER,
            model TEXT,
            reasoning_effort TEXT,
            temperature REAL,
            request_input_hash TEXT,
            output_text_hash TEXT,
            response_metadata_json TEXT,
            design TEXT,
            prompt_version TEXT,
            pair_id TEXT,
            source_role TEXT,
            source_text_hash TEXT,
            forecast_revision REAL,
            temperature_applied INTEGER,
            seed_applied INTEGER,
            response_schema_applied INTEGER,
            system_prompt_hash TEXT,
            user_prompt_hash TEXT,
            response_schema_hash TEXT,
            source_text_char_count INTEGER,
            supplied_text_char_count INTEGER,
            text_truncated INTEGER,
            sampling_control TEXT,
            error_type TEXT,
            error TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, line_number),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_results_latest (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_line_number INTEGER NOT NULL,
            status TEXT,
            created_at TEXT,
            meeting_number INTEGER,
            corpus_iteration INTEGER,
            scenario TEXT,
            forecaster_type TEXT,
            agent_id INTEGER,
            agent_seed INTEGER,
            previous_expectation REAL,
            new_expectation REAL,
            perceived_tone REAL,
            design TEXT,
            prompt_version TEXT,
            pair_id TEXT,
            source_role TEXT,
            source_text_hash TEXT,
            forecast_revision REAL,
            model TEXT,
            reasoning_effort TEXT,
            temperature REAL,
            temperature_applied INTEGER,
            seed_applied INTEGER,
            response_schema_applied INTEGER,
            system_prompt_hash TEXT,
            user_prompt_hash TEXT,
            response_schema_hash TEXT,
            source_text_char_count INTEGER,
            supplied_text_char_count INTEGER,
            text_truncated INTEGER,
            sampling_control TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id, event_line_number)
                REFERENCES agent_result_events(run_id, line_number)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS corpus_texts (
            run_id TEXT NOT NULL,
            meeting_number INTEGER NOT NULL,
            iteration INTEGER NOT NULL,
            role TEXT NOT NULL,
            source_path TEXT,
            complete INTEGER,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            char_count INTEGER NOT NULL,
            word_count INTEGER NOT NULL,
            PRIMARY KEY (run_id, meeting_number, iteration, role),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tone_scores (
            run_id TEXT NOT NULL,
            meeting_number INTEGER NOT NULL,
            iteration INTEGER NOT NULL,
            role TEXT NOT NULL,
            source_path TEXT,
            complete INTEGER,
            score REAL,
            hawk_count INTEGER,
            dove_count INTEGER,
            word_count INTEGER,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (run_id, meeting_number, iteration, role),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_rewrite_tasks_meeting
            ON rewrite_tasks(run_id, meeting_number, iteration, stance);
        CREATE INDEX IF NOT EXISTS idx_rewrite_latest_status
            ON rewrite_results_latest(run_id, status, stance);
        CREATE INDEX IF NOT EXISTS idx_agent_latest_status
            ON agent_results_latest(run_id, status, scenario, forecaster_type);
        CREATE INDEX IF NOT EXISTS idx_corpus_texts_role
            ON corpus_texts(run_id, role, meeting_number, iteration);
        CREATE INDEX IF NOT EXISTS idx_tone_scores_role
            ON tone_scores(run_id, role, meeting_number, iteration);
        """
    )
    migrations = {
        "agent_tasks": {
            "design": "TEXT",
            "prompt_version": "TEXT",
            "prior_source": "TEXT",
            "fixed_prior": "REAL",
            "pair_id": "TEXT",
            "source_run_id": "TEXT",
            "source_role": "TEXT",
            "source_text_hash": "TEXT",
            "requested_model": "TEXT",
            "requested_reasoning_effort": "TEXT",
            "requested_temperature": "REAL",
        },
        "agent_result_events": {
            "design": "TEXT",
            "prompt_version": "TEXT",
            "pair_id": "TEXT",
            "source_role": "TEXT",
            "source_text_hash": "TEXT",
            "forecast_revision": "REAL",
            "temperature_applied": "INTEGER",
            "seed_applied": "INTEGER",
            "response_schema_applied": "INTEGER",
            "system_prompt_hash": "TEXT",
            "user_prompt_hash": "TEXT",
            "response_schema_hash": "TEXT",
            "source_text_char_count": "INTEGER",
            "supplied_text_char_count": "INTEGER",
            "text_truncated": "INTEGER",
            "sampling_control": "TEXT",
        },
        "agent_results_latest": {
            "design": "TEXT",
            "prompt_version": "TEXT",
            "pair_id": "TEXT",
            "source_role": "TEXT",
            "source_text_hash": "TEXT",
            "forecast_revision": "REAL",
            "model": "TEXT",
            "reasoning_effort": "TEXT",
            "temperature": "REAL",
            "temperature_applied": "INTEGER",
            "seed_applied": "INTEGER",
            "response_schema_applied": "INTEGER",
            "system_prompt_hash": "TEXT",
            "user_prompt_hash": "TEXT",
            "response_schema_hash": "TEXT",
            "source_text_char_count": "INTEGER",
            "supplied_text_char_count": "INTEGER",
            "text_truncated": "INTEGER",
            "sampling_control": "TEXT",
        },
    }
    for table, columns in migrations.items():
        for column, definition in columns.items():
            _ensure_column(conn, table, column, definition)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_design_pair "
        "ON agent_tasks(run_id, design, pair_id, source_role)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


def sync_minutes(conn: sqlite3.Connection, root: Path, minutes_dir: Path) -> int:
    count = 0
    ingested_at = _utc_now()
    for path in sorted(minutes_dir.glob("*.txt")):
        meeting_number = _parse_meeting_number(path)
        if meeting_number is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        conn.execute(
            """
            INSERT OR REPLACE INTO minutes (
                meeting_number, source_path, text_hash, char_count, word_count,
                raw_text, modified_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_number,
                _relpath(path, root),
                _sha256_text(text),
                len(text),
                _word_count(text),
                text,
                datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                ingested_at,
            ),
        )
        count += 1
    return count


def _delete_run_payload(conn: sqlite3.Connection, run_id: str) -> None:
    for table in [
        "run_files",
        "rewrite_tasks",
        "rewrite_result_events",
        "rewrite_results_latest",
        "agent_tasks",
        "agent_result_events",
        "agent_results_latest",
        "corpus_texts",
        "tone_scores",
    ]:
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))


def _record_run_file(conn: sqlite3.Connection, root: Path, run_id: str, kind: str, path: Path) -> None:
    if not path.exists():
        return
    stat = path.stat()
    conn.execute(
        """
        INSERT OR REPLACE INTO run_files(run_id, kind, path, size_bytes, modified_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            run_id,
            kind,
            _relpath(path, root),
            stat.st_size,
            datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        ),
    )


def _insert_rewrite_tasks(conn: sqlite3.Connection, run_id: str, rows: list[tuple[int, dict[str, Any]]]) -> dict[str, Any]:
    stances: Counter[str] = Counter()
    prompt_versions: Counter[str] = Counter()
    meetings: list[int] = []
    for _, row in rows:
        stances.update([str(row.get("stance"))])
        prompt_versions.update([str(row.get("prompt_version"))])
        meeting_number = _int_or_none(row.get("meeting_number"))
        if meeting_number is not None:
            meetings.append(meeting_number)
        conn.execute(
            """
            INSERT OR REPLACE INTO rewrite_tasks (
                run_id, task_id, meeting_number, source_path, iteration, block_index,
                total_blocks, stance, prompt_version, input_hash, original_text_hash,
                system_prompt_hash, user_prompt_hash, char_count, word_count,
                numeric_token_count, numeric_unique_count, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("task_id"),
                meeting_number,
                row.get("source_path"),
                _int_or_none(row.get("iteration")),
                _int_or_none(row.get("block_index")),
                _int_or_none(row.get("total_blocks")),
                row.get("stance"),
                row.get("prompt_version"),
                row.get("input_hash"),
                row.get("original_text_hash"),
                row.get("system_prompt_hash"),
                row.get("user_prompt_hash"),
                _int_or_none(row.get("char_count")),
                _int_or_none(row.get("word_count")),
                _int_or_none(row.get("numeric_token_count")),
                _int_or_none(row.get("numeric_unique_count")),
                _json(row),
            ),
        )
    return {
        "tasks": len(rows),
        "stances": sorted(key for key in stances if key != "None"),
        "prompt_versions": sorted(key for key in prompt_versions if key != "None"),
        "first_meeting": min(meetings) if meetings else None,
        "last_meeting": max(meetings) if meetings else None,
    }


def _insert_rewrite_results(
    conn: sqlite3.Connection, run_id: str, rows: list[tuple[int, dict[str, Any]]]
) -> dict[str, Any]:
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for line_number, row in rows:
        task_id = row.get("task_id")
        conn.execute(
            """
            INSERT OR REPLACE INTO rewrite_result_events (
                run_id, line_number, task_id, status, created_at, meeting_number,
                source_path, iteration, block_index, total_blocks, stance,
                prompt_version, runner, runner_dry_run, model, reasoning_effort,
                temperature, manifest_input_hash, request_input_hash, output_text_hash,
                numeric_ok, numeric_missing_json, numeric_extra_json,
                retry_attempts_used, generated_text, response_metadata_json,
                error_type, error, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                line_number,
                task_id,
                row.get("status"),
                row.get("created_at"),
                _int_or_none(row.get("meeting_number")),
                row.get("source_path"),
                _int_or_none(row.get("iteration")),
                _int_or_none(row.get("block_index")),
                _int_or_none(row.get("total_blocks")),
                row.get("stance"),
                row.get("prompt_version"),
                row.get("runner"),
                _bool_int(row.get("runner_dry_run")),
                row.get("model"),
                row.get("reasoning_effort"),
                _float_or_none(row.get("temperature")),
                row.get("manifest_input_hash"),
                row.get("request_input_hash"),
                row.get("output_text_hash"),
                _bool_int(row.get("numeric_ok")),
                _json(row.get("numeric_missing")),
                _json(row.get("numeric_extra")),
                _int_or_none(row.get("retry_attempts_used")),
                row.get("generated_text"),
                _json(row.get("response_metadata")),
                row.get("error_type"),
                row.get("error"),
                _json_without_text(row),
            ),
        )
        if task_id is not None:
            latest[str(task_id)] = (line_number, row)

    for task_id, (line_number, row) in latest.items():
        conn.execute(
            """
            INSERT OR REPLACE INTO rewrite_results_latest (
                run_id, task_id, event_line_number, status, created_at,
                meeting_number, source_path, iteration, block_index, total_blocks,
                stance, prompt_version, model, reasoning_effort, temperature,
                manifest_input_hash, request_input_hash, output_text_hash,
                numeric_ok, generated_text, retry_attempts_used, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                line_number,
                row.get("status"),
                row.get("created_at"),
                _int_or_none(row.get("meeting_number")),
                row.get("source_path"),
                _int_or_none(row.get("iteration")),
                _int_or_none(row.get("block_index")),
                _int_or_none(row.get("total_blocks")),
                row.get("stance"),
                row.get("prompt_version"),
                row.get("model"),
                row.get("reasoning_effort"),
                _float_or_none(row.get("temperature")),
                row.get("manifest_input_hash"),
                row.get("request_input_hash"),
                row.get("output_text_hash"),
                _bool_int(row.get("numeric_ok")),
                row.get("generated_text"),
                _int_or_none(row.get("retry_attempts_used")),
                _json_without_text(row),
            ),
        )
    completed = sum(1 for _, row in latest.values() if row.get("status") == "completed")
    return {"lines": len(rows), "latest": len(latest), "completed_latest": completed}


def load_pending_rewrite_tasks(
    conn: sqlite3.Connection,
    run_id: str,
    limit: int | None = None,
    resume: bool = True,
    done_statuses: Iterable[str] = ("completed",),
) -> tuple[list[dict[str, Any]], int, int]:
    total = conn.execute("SELECT COUNT(*) FROM rewrite_tasks WHERE run_id = ?", (run_id,)).fetchone()[0]
    if total == 0:
        raise ValueError(f"No rewrite_tasks found in database for run_id={run_id!r}")

    done = set(done_statuses) if resume else set()
    params: list[Any] = [run_id]
    sql = """
        SELECT
            task_id, meeting_number, source_path, iteration, block_index,
            total_blocks, stance, prompt_version, input_hash,
            original_text_hash, system_prompt_hash, user_prompt_hash,
            char_count, word_count, numeric_token_count, numeric_unique_count
        FROM rewrite_tasks
        WHERE run_id = ?
    """
    if done:
        placeholders = ",".join("?" for _ in done)
        sql += f"""
            AND task_id NOT IN (
                SELECT task_id
                FROM rewrite_results_latest
                WHERE run_id = ? AND status IN ({placeholders})
            )
        """
        params.extend([run_id, *sorted(done)])
    sql += " ORDER BY meeting_number, iteration, block_index, stance"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))

    rows = conn.execute(sql, params).fetchall()
    columns = [
        "task_id",
        "meeting_number",
        "source_path",
        "iteration",
        "block_index",
        "total_blocks",
        "stance",
        "prompt_version",
        "input_hash",
        "original_text_hash",
        "system_prompt_hash",
        "user_prompt_hash",
        "char_count",
        "word_count",
        "numeric_token_count",
        "numeric_unique_count",
    ]
    done_count = 0
    if done:
        placeholders = ",".join("?" for _ in done)
        done_count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM rewrite_results_latest
            WHERE run_id = ? AND status IN ({placeholders})
            """,
            [run_id, *sorted(done)],
        ).fetchone()[0]
    return [dict(zip(columns, row)) for row in rows], int(total), int(done_count)


def append_rewrite_result_event(conn: sqlite3.Connection, run_id: str, record: dict[str, Any]) -> int:
    line_number = int(
        conn.execute(
            "SELECT COALESCE(MAX(line_number), 0) + 1 FROM rewrite_result_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    row = dict(record)
    if row.get("stance") is None and row.get("task_id"):
        task = conn.execute(
            "SELECT stance FROM rewrite_tasks WHERE run_id = ? AND task_id = ?",
            (run_id, row.get("task_id")),
        ).fetchone()
        if task:
            row["stance"] = task[0]
    if row.get("meeting_number") is None and row.get("task_id"):
        task = conn.execute(
            """
            SELECT meeting_number, source_path, iteration, block_index, total_blocks,
                   prompt_version, input_hash
            FROM rewrite_tasks
            WHERE run_id = ? AND task_id = ?
            """,
            (run_id, row.get("task_id")),
        ).fetchone()
        if task:
            (
                row["meeting_number"],
                row["source_path"],
                row["iteration"],
                row["block_index"],
                row["total_blocks"],
                row["prompt_version"],
                row["manifest_input_hash"],
            ) = task

    conn.execute(
        """
        INSERT INTO rewrite_result_events (
            run_id, line_number, task_id, status, created_at, meeting_number,
            source_path, iteration, block_index, total_blocks, stance,
            prompt_version, runner, runner_dry_run, model, reasoning_effort,
            temperature, manifest_input_hash, request_input_hash, output_text_hash,
            numeric_ok, numeric_missing_json, numeric_extra_json,
            retry_attempts_used, generated_text, response_metadata_json,
            error_type, error, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            line_number,
            row.get("task_id"),
            row.get("status"),
            row.get("created_at"),
            _int_or_none(row.get("meeting_number")),
            row.get("source_path"),
            _int_or_none(row.get("iteration")),
            _int_or_none(row.get("block_index")),
            _int_or_none(row.get("total_blocks")),
            row.get("stance"),
            row.get("prompt_version"),
            row.get("runner"),
            _bool_int(row.get("runner_dry_run")),
            row.get("model"),
            row.get("reasoning_effort"),
            _float_or_none(row.get("temperature")),
            row.get("manifest_input_hash"),
            row.get("request_input_hash"),
            row.get("output_text_hash"),
            _bool_int(row.get("numeric_ok")),
            _json(row.get("numeric_missing")),
            _json(row.get("numeric_extra")),
            _int_or_none(row.get("retry_attempts_used")),
            row.get("generated_text"),
            _json(row.get("response_metadata")),
            row.get("error_type"),
            row.get("error"),
            _json_without_text(row),
        ),
    )

    task_id = row.get("task_id")
    if task_id is not None:
        conn.execute(
            """
            INSERT OR REPLACE INTO rewrite_results_latest (
                run_id, task_id, event_line_number, status, created_at,
                meeting_number, source_path, iteration, block_index, total_blocks,
                stance, prompt_version, model, reasoning_effort, temperature,
                manifest_input_hash, request_input_hash, output_text_hash,
                numeric_ok, generated_text, retry_attempts_used, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                line_number,
                row.get("status"),
                row.get("created_at"),
                _int_or_none(row.get("meeting_number")),
                row.get("source_path"),
                _int_or_none(row.get("iteration")),
                _int_or_none(row.get("block_index")),
                _int_or_none(row.get("total_blocks")),
                row.get("stance"),
                row.get("prompt_version"),
                row.get("model"),
                row.get("reasoning_effort"),
                _float_or_none(row.get("temperature")),
                row.get("manifest_input_hash"),
                row.get("request_input_hash"),
                row.get("output_text_hash"),
                _bool_int(row.get("numeric_ok")),
                row.get("generated_text"),
                _int_or_none(row.get("retry_attempts_used")),
                _json_without_text(row),
            ),
        )
    return line_number


def refresh_rewrite_run_summary(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    total = conn.execute("SELECT COUNT(*) FROM rewrite_tasks WHERE run_id = ?", (run_id,)).fetchone()[0]
    lines = conn.execute("SELECT COUNT(*) FROM rewrite_result_events WHERE run_id = ?", (run_id,)).fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM rewrite_results_latest WHERE run_id = ? AND status = 'completed'",
        (run_id,),
    ).fetchone()[0]
    conn.execute(
        """
        UPDATE runs
        SET rewrite_tasks_total = ?,
            rewrite_result_lines = ?,
            rewrite_completed_latest = ?,
            ingested_at = ?
        WHERE run_id = ?
        """,
        (total, lines, completed, _utc_now(), run_id),
    )
    return {"rewrite_tasks": int(total), "rewrite_result_lines": int(lines), "rewrite_completed_latest": int(completed)}


def _insert_agent_tasks(conn: sqlite3.Connection, run_id: str, rows: list[tuple[int, dict[str, Any]]]) -> int:
    for _, row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_tasks (
                run_id, task_id, meeting_number, corpus_iteration, scenario,
                forecaster_type, agent_id, agent_seed, sequence_index,
                state_group, source_corpus, source_path, initial_expectation,
                inflation_target, previous_expectation_required,
                prompt_family_hash, design, prompt_version, prior_source,
                fixed_prior, pair_id, source_run_id, source_role,
                source_text_hash, requested_model, requested_reasoning_effort,
                requested_temperature, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                row.get("task_id"),
                _int_or_none(row.get("meeting_number")),
                _int_or_none(row.get("corpus_iteration")),
                row.get("scenario"),
                row.get("forecaster_type"),
                _int_or_none(row.get("agent_id")),
                _int_or_none(row.get("agent_seed")),
                _int_or_none(row.get("sequence_index")),
                row.get("state_group"),
                row.get("source_corpus"),
                row.get("source_path"),
                _float_or_none(row.get("initial_expectation")),
                _float_or_none(row.get("inflation_target")),
                _bool_int(row.get("previous_expectation_required")),
                row.get("prompt_family_hash"),
                row.get("design"),
                row.get("prompt_version"),
                row.get("prior_source"),
                _float_or_none(row.get("fixed_prior")),
                row.get("pair_id"),
                row.get("source_run_id"),
                row.get("source_role"),
                row.get("source_text_hash"),
                row.get("requested_model"),
                row.get("requested_reasoning_effort"),
                _float_or_none(row.get("requested_temperature")),
                _json(row),
            ),
        )
    return len(rows)


def _insert_agent_results(
    conn: sqlite3.Connection, run_id: str, rows: list[tuple[int, dict[str, Any]]]
) -> dict[str, Any]:
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for line_number, row in rows:
        task_id = row.get("task_id")
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_result_events (
                run_id, line_number, task_id, status, created_at, meeting_number,
                corpus_iteration, scenario, forecaster_type, agent_id, agent_seed,
                sequence_index, state_group, previous_expectation, new_expectation,
                perceived_tone, inflation_target, runner, runner_dry_run, model,
                reasoning_effort, temperature, request_input_hash, output_text_hash,
                response_metadata_json, design, prompt_version, pair_id,
                source_role, source_text_hash, forecast_revision,
                temperature_applied, seed_applied, response_schema_applied,
                system_prompt_hash, user_prompt_hash, response_schema_hash,
                source_text_char_count, supplied_text_char_count,
                text_truncated, sampling_control,
                error_type, error, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                line_number,
                task_id,
                row.get("status"),
                row.get("created_at"),
                _int_or_none(row.get("meeting_number")),
                _int_or_none(row.get("corpus_iteration")),
                row.get("scenario"),
                row.get("forecaster_type"),
                _int_or_none(row.get("agent_id")),
                _int_or_none(row.get("agent_seed")),
                _int_or_none(row.get("sequence_index")),
                row.get("state_group"),
                _float_or_none(row.get("previous_expectation")),
                _float_or_none(row.get("new_expectation")),
                _float_or_none(row.get("perceived_tone")),
                _float_or_none(row.get("inflation_target")),
                row.get("runner"),
                _bool_int(row.get("runner_dry_run")),
                row.get("model"),
                row.get("reasoning_effort"),
                _float_or_none(row.get("temperature")),
                row.get("request_input_hash"),
                row.get("output_text_hash"),
                _json(row.get("response_metadata")),
                row.get("design"),
                row.get("prompt_version"),
                row.get("pair_id"),
                row.get("source_role"),
                row.get("source_text_hash"),
                _float_or_none(row.get("forecast_revision")),
                _bool_int(row.get("temperature_applied")),
                _bool_int(row.get("seed_applied")),
                _bool_int(row.get("response_schema_applied")),
                row.get("system_prompt_hash"),
                row.get("user_prompt_hash"),
                row.get("response_schema_hash"),
                _int_or_none(row.get("source_text_char_count")),
                _int_or_none(row.get("supplied_text_char_count")),
                _bool_int(row.get("text_truncated")),
                row.get("sampling_control"),
                row.get("error_type"),
                row.get("error"),
                _json(row),
            ),
        )
        if task_id is not None:
            latest[str(task_id)] = (line_number, row)

    for task_id, (line_number, row) in latest.items():
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_results_latest (
                run_id, task_id, event_line_number, status, created_at,
                meeting_number, corpus_iteration, scenario, forecaster_type,
                agent_id, agent_seed, previous_expectation, new_expectation,
                perceived_tone, design, prompt_version, pair_id, source_role,
                source_text_hash, forecast_revision, model, reasoning_effort,
                temperature, temperature_applied, seed_applied,
                response_schema_applied, system_prompt_hash, user_prompt_hash,
                response_schema_hash, source_text_char_count,
                supplied_text_char_count, text_truncated, sampling_control,
                raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                line_number,
                row.get("status"),
                row.get("created_at"),
                _int_or_none(row.get("meeting_number")),
                _int_or_none(row.get("corpus_iteration")),
                row.get("scenario"),
                row.get("forecaster_type"),
                _int_or_none(row.get("agent_id")),
                _int_or_none(row.get("agent_seed")),
                _float_or_none(row.get("previous_expectation")),
                _float_or_none(row.get("new_expectation")),
                _float_or_none(row.get("perceived_tone")),
                row.get("design"),
                row.get("prompt_version"),
                row.get("pair_id"),
                row.get("source_role"),
                row.get("source_text_hash"),
                _float_or_none(row.get("forecast_revision")),
                row.get("model"),
                row.get("reasoning_effort"),
                _float_or_none(row.get("temperature")),
                _bool_int(row.get("temperature_applied")),
                _bool_int(row.get("seed_applied")),
                _bool_int(row.get("response_schema_applied")),
                row.get("system_prompt_hash"),
                row.get("user_prompt_hash"),
                row.get("response_schema_hash"),
                _int_or_none(row.get("source_text_char_count")),
                _int_or_none(row.get("supplied_text_char_count")),
                _bool_int(row.get("text_truncated")),
                row.get("sampling_control"),
                _json(row),
            ),
        )
    completed = sum(1 for _, row in latest.values() if row.get("status") == "completed")
    return {"lines": len(rows), "latest": len(latest), "completed_latest": completed}


def register_sqlite_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    metadata: dict[str, Any],
    first_meeting: int | None = None,
    last_meeting: int | None = None,
    prompt_versions: Iterable[str] = (),
    stances: Iterable[str] = (),
) -> None:
    existing = conn.execute("SELECT metadata_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    metadata_json = _json(metadata)
    if existing is not None:
        if existing[0] != metadata_json:
            raise ValueError(f"Run {run_id!r} already exists with different metadata")
        return
    conn.execute(
        """
        INSERT INTO runs (
            run_id, run_dir, first_meeting, last_meeting,
            prompt_versions_json, stances_json, metadata_json, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            f"sqlite://{run_id}",
            _int_or_none(first_meeting),
            _int_or_none(last_meeting),
            _json(sorted(set(prompt_versions))),
            _json(list(stances)),
            metadata_json,
            _utc_now(),
        ),
    )


def insert_agent_tasks(
    conn: sqlite3.Connection,
    run_id: str,
    rows: Iterable[dict[str, Any]],
) -> dict[str, int]:
    inserted = 0
    skipped = 0
    for row in rows:
        task_id = str(row["task_id"])
        raw_json = _json(row)
        existing = conn.execute(
            "SELECT raw_json FROM agent_tasks WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != raw_json:
                raise ValueError(f"Task {task_id!r} already exists with different contents")
            skipped += 1
            continue
        _insert_agent_tasks(conn, run_id, [(inserted + 1, row)])
        inserted += 1
    refresh_agent_run_summary(conn, run_id)
    return {"inserted": inserted, "skipped_existing": skipped, "total_seen": inserted + skipped}


def load_pending_agent_tasks(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    limit: int | None = None,
    resume: bool = True,
    done_statuses: Iterable[str] = ("completed",),
    corpus_iterations: Iterable[int] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    iterations = tuple(sorted(set(int(value) for value in (corpus_iterations or ()))))
    scope_sql = "run_id = ?"
    scope_params: list[Any] = [run_id]
    if iterations:
        placeholders = ",".join("?" for _ in iterations)
        scope_sql += f" AND corpus_iteration IN ({placeholders})"
        scope_params.extend(iterations)
    total = int(
        conn.execute(
            f"SELECT COUNT(*) FROM agent_tasks WHERE {scope_sql}",
            scope_params,
        ).fetchone()[0]
    )
    if total == 0:
        raise ValueError(f"No agent_tasks found in database for run_id={run_id!r}")
    done = sorted(set(done_statuses)) if resume else []
    params: list[Any] = list(scope_params)
    sql = f"SELECT raw_json FROM agent_tasks WHERE {scope_sql}"
    if done:
        placeholders = ",".join("?" for _ in done)
        sql += f"""
            AND task_id NOT IN (
                SELECT task_id FROM agent_results_latest
                WHERE run_id = ? AND status IN ({placeholders})
            )
        """
        params.extend([run_id, *done])
    sql += " ORDER BY corpus_iteration, meeting_number, forecaster_type, agent_id, scenario"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    tasks = [json.loads(row[0]) for row in conn.execute(sql, params).fetchall()]
    completed = 0
    if done:
        placeholders = ",".join("?" for _ in done)
        iteration_sql = ""
        iteration_params: list[Any] = []
        if iterations:
            iteration_placeholders = ",".join("?" for _ in iterations)
            iteration_sql = f" AND t.corpus_iteration IN ({iteration_placeholders})"
            iteration_params.extend(iterations)
        completed = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM agent_results_latest r
                JOIN agent_tasks t
                  ON t.run_id = r.run_id AND t.task_id = r.task_id
                WHERE r.run_id = ? AND r.status IN ({placeholders}){iteration_sql}
                """,
                [run_id, *done, *iteration_params],
            ).fetchone()[0]
        )
    return tasks, total, completed


def load_agent_task_text(conn: sqlite3.Connection, task: dict[str, Any]) -> str:
    source_run_id = task.get("source_run_id")
    if source_run_id:
        row = conn.execute(
            """
            SELECT text, text_hash FROM corpus_texts
            WHERE run_id = ? AND meeting_number = ? AND iteration = ? AND role = ?
            """,
            (
                source_run_id,
                int(task["meeting_number"]),
                int(task["corpus_iteration"]),
                task["source_role"],
            ),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT raw_text, text_hash FROM minutes WHERE meeting_number = ?",
            (int(task["meeting_number"]),),
        ).fetchone()
    if row is None:
        raise ValueError(f"Source text not found for task {task.get('task_id')!r}")
    if task.get("source_text_hash") and row[1] != task["source_text_hash"]:
        raise ValueError(f"Source text hash mismatch for task {task.get('task_id')!r}")
    return str(row[0])


def append_agent_result_event(conn: sqlite3.Connection, run_id: str, record: dict[str, Any]) -> int:
    line_number = int(
        conn.execute(
            "SELECT COALESCE(MAX(line_number), 0) + 1 FROM agent_result_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    _insert_agent_results(conn, run_id, [(line_number, dict(record))])
    refresh_agent_run_summary(conn, run_id)
    return line_number


def refresh_agent_run_summary(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    total = int(conn.execute("SELECT COUNT(*) FROM agent_tasks WHERE run_id = ?", (run_id,)).fetchone()[0])
    lines = int(conn.execute("SELECT COUNT(*) FROM agent_result_events WHERE run_id = ?", (run_id,)).fetchone()[0])
    completed = int(
        conn.execute(
            "SELECT COUNT(*) FROM agent_results_latest WHERE run_id = ? AND status = 'completed'",
            (run_id,),
        ).fetchone()[0]
    )
    conn.execute(
        """
        UPDATE runs
        SET agent_tasks_total = ?, agent_result_lines = ?,
            agent_completed_latest = ?, ingested_at = ?
        WHERE run_id = ?
        """,
        (total, lines, completed, _utc_now(), run_id),
    )
    return {"agent_tasks": total, "agent_result_lines": lines, "agent_completed_latest": completed}


def _insert_corpus_texts(conn: sqlite3.Connection, run_id: str, path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return 0
        text_columns = [column for column in reader.fieldnames if column.endswith(TEXT_COLUMN_SUFFIX)]
        for row in reader:
            meeting_number = _int_or_none(row.get("meeting_number"))
            iteration = _int_or_none(row.get("iteration"))
            if meeting_number is None or iteration is None:
                continue
            for column in text_columns:
                text = row.get(column) or ""
                role = column[: -len(TEXT_COLUMN_SUFFIX)]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO corpus_texts (
                        run_id, meeting_number, iteration, role, source_path,
                        complete, text, text_hash, char_count, word_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        meeting_number,
                        iteration,
                        role,
                        row.get("source_path"),
                        _bool_int(row.get("complete")),
                        text,
                        _sha256_text(text),
                        len(text),
                        _word_count(text),
                    ),
                )
                count += 1
    return count


def _score_roles(fieldnames: list[str]) -> list[str]:
    roles: set[str] = set()
    for column in fieldnames:
        if column.endswith("_score"):
            roles.add(column[: -len("_score")])
    return sorted(roles)


def _insert_tone_scores(conn: sqlite3.Connection, run_id: str, path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return 0
        roles = _score_roles(reader.fieldnames)
        for row in reader:
            meeting_number = _int_or_none(row.get("meeting_number"))
            iteration = _int_or_none(row.get("iteration"))
            if meeting_number is None or iteration is None:
                continue
            for role in roles:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tone_scores (
                        run_id, meeting_number, iteration, role, source_path,
                        complete, score, hawk_count, dove_count, word_count,
                        raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        meeting_number,
                        iteration,
                        role,
                        row.get("source_path"),
                        _bool_int(row.get("complete")),
                        _float_or_none(row.get(f"{role}_score")),
                        _int_or_none(row.get(f"{role}_hawk_count")),
                        _int_or_none(row.get(f"{role}_dove_count")),
                        _int_or_none(row.get(f"{role}_word_count")),
                        _json(
                            {
                                "score": row.get(f"{role}_score"),
                                "hawk_count": row.get(f"{role}_hawk_count"),
                                "dove_count": row.get(f"{role}_dove_count"),
                                "word_count": row.get(f"{role}_word_count"),
                            }
                        ),
                    ),
                )
                count += 1
    return count


def sync_run(conn: sqlite3.Connection, root: Path, run_dir: Path) -> dict[str, Any]:
    run_id = run_dir.name
    _delete_run_payload(conn, run_id)
    conn.execute(
        """
        INSERT OR IGNORE INTO runs(run_id, run_dir, metadata_json, ingested_at)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, _relpath(run_dir, root), _json({"status": "syncing"}), _utc_now()),
    )

    manifest_path = run_dir / "rewrite_manifest.jsonl"
    results_path = run_dir / "rewrite_results.jsonl"
    agent_manifest_path = run_dir / "counterfactual_agent_manifest.jsonl"
    agent_results_path = run_dir / "counterfactual_agent_results.jsonl"
    corpus_path = run_dir / "counterfactual_corpus.csv"
    tone_path = run_dir / "counterfactual_tone_scores.csv"

    for kind, path in [
        ("rewrite_manifest", manifest_path),
        ("rewrite_results", results_path),
        ("agent_manifest", agent_manifest_path),
        ("agent_results", agent_results_path),
        ("counterfactual_corpus", corpus_path),
        ("counterfactual_tone_scores", tone_path),
    ]:
        _record_run_file(conn, root, run_id, kind, path)

    manifest_rows, rewrite_bad_json_manifest = _iter_jsonl(manifest_path)
    result_rows, rewrite_bad_json_results = _iter_jsonl(results_path)
    agent_task_rows, agent_bad_json_manifest = _iter_jsonl(agent_manifest_path)
    agent_result_rows, agent_bad_json_results = _iter_jsonl(agent_results_path)

    task_summary = _insert_rewrite_tasks(conn, run_id, manifest_rows)
    result_summary = _insert_rewrite_results(conn, run_id, result_rows)
    agent_tasks_total = _insert_agent_tasks(conn, run_id, agent_task_rows)
    agent_result_summary = _insert_agent_results(conn, run_id, agent_result_rows)
    corpus_rows = _insert_corpus_texts(conn, run_id, corpus_path)
    tone_rows = _insert_tone_scores(conn, run_id, tone_path)

    metadata = {
        "rewrite_manifest_bad_json_lines": rewrite_bad_json_manifest,
        "agent_manifest_bad_json_lines": agent_bad_json_manifest,
        "active_snapshot": results_path.exists(),
    }
    conn.execute(
        """
        UPDATE runs
        SET run_dir = ?,
            first_meeting = ?,
            last_meeting = ?,
            prompt_versions_json = ?,
            stances_json = ?,
            rewrite_tasks_total = ?,
            rewrite_result_lines = ?,
            rewrite_completed_latest = ?,
            rewrite_bad_json_lines = ?,
            agent_tasks_total = ?,
            agent_result_lines = ?,
            agent_completed_latest = ?,
            agent_bad_json_lines = ?,
            corpus_rows = ?,
            tone_rows = ?,
            metadata_json = ?,
            ingested_at = ?
        WHERE run_id = ?
        """,
        (
            _relpath(run_dir, root),
            task_summary["first_meeting"],
            task_summary["last_meeting"],
            _json(task_summary["prompt_versions"]),
            _json(task_summary["stances"]),
            task_summary["tasks"],
            result_summary["lines"],
            result_summary["completed_latest"],
            rewrite_bad_json_results,
            agent_tasks_total,
            agent_result_summary["lines"],
            agent_result_summary["completed_latest"],
            agent_bad_json_results,
            corpus_rows,
            tone_rows,
            _json(metadata),
            _utc_now(),
            run_id,
        ),
    )

    return {
        "run_id": run_id,
        "rewrite_tasks": task_summary["tasks"],
        "rewrite_result_lines": result_summary["lines"],
        "rewrite_completed_latest": result_summary["completed_latest"],
        "rewrite_bad_json_lines": rewrite_bad_json_results,
        "agent_tasks": agent_tasks_total,
        "agent_result_lines": agent_result_summary["lines"],
        "agent_completed_latest": agent_result_summary["completed_latest"],
        "agent_bad_json_lines": agent_bad_json_results,
        "corpus_text_rows": corpus_rows,
        "tone_score_rows": tone_rows,
    }


def discover_run_dirs(root: Path, run_dirs: Iterable[str | Path] | None = None) -> list[Path]:
    if run_dirs:
        return [Path(path) if Path(path).is_absolute() else root / Path(path) for path in run_dirs]
    replications = root / "data" / "interim" / "replications"
    if not replications.exists():
        return []
    return sorted(path for path in replications.iterdir() if path.is_dir())


def sync_project_database(
    db_path: str | Path,
    root: str | Path,
    minutes_dir: str | Path,
    run_dirs: Iterable[str | Path] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    database_path = Path(db_path)
    if not database_path.is_absolute():
        database_path = root_path / database_path
    minutes_path = Path(minutes_dir)
    if not minutes_path.is_absolute():
        minutes_path = root_path / minutes_path
    selected_runs = discover_run_dirs(root_path, run_dirs)

    if replace and database_path.exists():
        database_path.unlink()
        wal = database_path.with_suffix(database_path.suffix + "-wal")
        shm = database_path.with_suffix(database_path.suffix + "-shm")
        for sidecar in [wal, shm]:
            if sidecar.exists():
                sidecar.unlink()

    conn = _connect(database_path)
    try:
        initialize_schema(conn)
        with conn:
            minutes_count = sync_minutes(conn, root_path, minutes_path)
            run_summaries = [sync_run(conn, root_path, run_dir) for run_dir in selected_runs]
        table_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "minutes",
                "runs",
                "rewrite_tasks",
                "rewrite_result_events",
                "rewrite_results_latest",
                "agent_tasks",
                "agent_result_events",
                "agent_results_latest",
                "corpus_texts",
                "tone_scores",
            ]
        }
        return {
            "database": str(database_path),
            "minutes_dir": str(minutes_path),
            "minutes_ingested": minutes_count,
            "runs": run_summaries,
            "table_counts": table_counts,
        }
    finally:
        conn.close()
