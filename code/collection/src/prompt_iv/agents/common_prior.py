from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import math

from prompt_iv.agents.prompts import COMMON_PRIOR_PROMPT_VERSION, build_forecaster_prompt
from prompt_iv.agents.simulation import build_agent_request, parse_agent_decision
from prompt_iv.agents.targets import inflation_target_for_meeting
from prompt_iv.llm.runner import LLMRunner
from prompt_iv.storage import (
    append_agent_result_event,
    connect_database,
    insert_agent_tasks,
    load_agent_task_text,
    load_pending_agent_tasks,
    register_sqlite_run,
)


FORECASTER_TYPES = ("anchored", "unanchored")
USAGE_LIMIT_PATTERNS = ("usage limit", "hit your usage limit")
ARM_SPECIFIC_PRIOR_DESIGN = "arm_specific_prior_path"
ARM_SPECIFIC_PRIOR_ROLES = (
    "dove_high",
    "dove_low",
    "original",
    "neutral",
    "hawk_low",
    "hawk_high",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paired_seed(random_seed: int, meeting: int, iteration: int, forecaster_type: str) -> int:
    payload = f"{random_seed}|{meeting}|{iteration}|{forecaster_type}"
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def _prompt_family_hash(forecaster_type: str) -> str:
    prompt = build_forecaster_prompt(
        forecaster_type,
        previous_inflation=0.0,
        target=0.0,
        prompt_version=COMMON_PRIOR_PROMPT_VERSION,
    )
    return _hash_text(prompt)


def _normalize_forecasters(forecaster_types: Iterable[str]) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(str(item).strip().lower() for item in forecaster_types if str(item).strip()))
    unknown = sorted(set(values) - set(FORECASTER_TYPES))
    if unknown:
        raise ValueError(f"Unknown forecaster type(s): {unknown}")
    if not values:
        raise ValueError("At least one forecaster type is required")
    return values


def prepare_original_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    meeting_numbers: Iterable[int],
    forecaster_types: Iterable[str] = FORECASTER_TYPES,
    initial_expectation: float = 3.80,
    random_seed: int = 20260712,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    requested_temperature: float = 1.0,
) -> dict[str, Any]:
    meetings = tuple(sorted(set(int(value) for value in meeting_numbers)))
    forecasters = _normalize_forecasters(forecaster_types)
    if not meetings:
        raise ValueError("At least one meeting number is required")

    conn = connect_database(database_path)
    try:
        placeholders = ",".join("?" for _ in meetings)
        source_rows = conn.execute(
            f"SELECT meeting_number, source_path, text_hash FROM minutes "
            f"WHERE meeting_number IN ({placeholders}) ORDER BY meeting_number",
            meetings,
        ).fetchall()
        found = {int(row[0]) for row in source_rows}
        missing = sorted(set(meetings) - found)
        if missing:
            raise ValueError(f"Missing original minutes in SQLite: {missing}")

        tasks: list[dict[str, Any]] = []
        for forecaster_type in forecasters:
            sequence_seed = _paired_seed(random_seed, meetings[0], 0, forecaster_type)
            for sequence_index, (meeting, source_path, text_hash) in enumerate(source_rows, start=1):
                task_id = f"original-{forecaster_type}-seq-{sequence_index:03d}-meeting-{int(meeting):03d}"
                tasks.append(
                    {
                        "task_id": task_id,
                        "design": "original_path",
                        "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
                        "meeting_number": int(meeting),
                        "corpus_iteration": 0,
                        "scenario": "original",
                        "source_role": "original",
                        "source_run_id": None,
                        "source_corpus": "sqlite://minutes",
                        "source_path": str(source_path),
                        "source_text_hash": str(text_hash),
                        "forecaster_type": forecaster_type,
                        "agent_id": 1,
                        "agent_seed": sequence_seed,
                        "sequence_index": sequence_index,
                        "state_group": f"{run_id}::original::{forecaster_type}",
                        "initial_expectation": float(initial_expectation),
                        "inflation_target": inflation_target_for_meeting(int(meeting)),
                        "previous_expectation_required": sequence_index > 1,
                        "prompt_family_hash": _prompt_family_hash(forecaster_type),
                        "prior_source": "initial" if sequence_index == 1 else f"{run_id}:lag",
                        "fixed_prior": None,
                        "pair_id": f"original::{int(meeting):03d}::{forecaster_type}",
                        "requested_model": model,
                        "requested_reasoning_effort": reasoning_effort,
                        "requested_temperature": float(requested_temperature),
                    }
                )

        metadata = {
            "design": "original_path",
            "purpose": "Build pre-treatment priors for the common-prior Prompt-IV design.",
            "model": model,
            "reasoning_effort": reasoning_effort,
            "requested_temperature": requested_temperature,
            "sampling_control": "codex_cli_default",
            "seed_requested_but_not_supported_by_codex_cli": True,
            "random_seed_for_pair_ids": random_seed,
            "initial_expectation": initial_expectation,
            "forecaster_types": list(forecasters),
            "meeting_numbers": list(meetings),
            "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
        }
        with conn:
            register_sqlite_run(
                conn,
                run_id,
                metadata=metadata,
                first_meeting=meetings[0],
                last_meeting=meetings[-1],
                prompt_versions=[COMMON_PRIOR_PROMPT_VERSION],
                stances=["original"],
            )
            inserted = insert_agent_tasks(conn, run_id, tasks)
        return {"run_id": run_id, "tasks": len(tasks), **inserted, "metadata": metadata}
    finally:
        conn.close()


def _prior_map_from_original_run(
    conn,
    prior_run_id: str,
    meeting_numbers: tuple[int, ...],
    forecaster_types: tuple[str, ...],
) -> dict[tuple[int, str], float]:
    rows = conn.execute(
        """
        SELECT t.meeting_number, t.forecaster_type, r.previous_expectation, r.status
        FROM agent_tasks t
        LEFT JOIN agent_results_latest r
          ON r.run_id = t.run_id AND r.task_id = t.task_id
        WHERE t.run_id = ? AND t.design = 'original_path'
        ORDER BY t.forecaster_type, t.sequence_index
        """,
        (prior_run_id,),
    ).fetchall()
    prior_map: dict[tuple[int, str], float] = {}
    for meeting, forecaster_type, previous_expectation, status in rows:
        if int(meeting) in meeting_numbers and str(forecaster_type) in forecaster_types:
            if status != "completed" or previous_expectation is None:
                raise ValueError(
                    f"Original prior is incomplete for meeting={meeting}, forecaster={forecaster_type}"
                )
            prior_map[(int(meeting), str(forecaster_type))] = float(previous_expectation)
    expected = {(meeting, forecaster) for meeting in meeting_numbers for forecaster in forecaster_types}
    missing = sorted(expected - set(prior_map))
    if missing:
        raise ValueError(f"Missing common priors: {missing[:8]}")
    return prior_map


def prepare_common_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    source_run_id: str,
    prior_run_id: str,
    meeting_numbers: Iterable[int],
    iterations: Iterable[int],
    roles: Iterable[str] = ("hawk", "dove"),
    forecaster_types: Iterable[str] = FORECASTER_TYPES,
    random_seed: int = 20260712,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    requested_temperature: float = 1.0,
) -> dict[str, Any]:
    meetings = tuple(sorted(set(int(value) for value in meeting_numbers)))
    draws = tuple(sorted(set(int(value) for value in iterations)))
    role_values = tuple(dict.fromkeys(str(value).strip().lower() for value in roles if str(value).strip()))
    forecasters = _normalize_forecasters(forecaster_types)
    if not meetings or not draws or not role_values:
        raise ValueError("Meetings, iterations, and roles must all be non-empty")

    conn = connect_database(database_path)
    try:
        prior_map = _prior_map_from_original_run(conn, prior_run_id, meetings, forecasters)
        source_rows = conn.execute(
            """
            SELECT meeting_number, iteration, role, source_path, text_hash
            FROM corpus_texts WHERE run_id = ?
            ORDER BY meeting_number, iteration, role
            """,
            (source_run_id,),
        ).fetchall()
        selected = [
            row
            for row in source_rows
            if int(row[0]) in meetings and int(row[1]) in draws and str(row[2]) in role_values
        ]
        expected_texts = {(meeting, draw, role) for meeting in meetings for draw in draws for role in role_values}
        found_texts = {(int(row[0]), int(row[1]), str(row[2])) for row in selected}
        missing_texts = sorted(expected_texts - found_texts)
        if missing_texts:
            raise ValueError(f"Missing source texts in {source_run_id!r}: {missing_texts[:8]}")

        tasks: list[dict[str, Any]] = []
        sequence_by_meeting = {meeting: index for index, meeting in enumerate(meetings, start=1)}
        for meeting, iteration, role, source_path, text_hash in selected:
            for forecaster_type in forecasters:
                pair_id = f"meeting-{int(meeting):03d}-iter-{int(iteration):02d}-{forecaster_type}"
                pair_seed = _paired_seed(random_seed, int(meeting), int(iteration), forecaster_type)
                fixed_prior = prior_map[(int(meeting), forecaster_type)]
                task_id = f"{pair_id}-{str(role)}"
                tasks.append(
                    {
                        "task_id": task_id,
                        "design": "common_prior",
                        "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
                        "meeting_number": int(meeting),
                        "corpus_iteration": int(iteration),
                        "scenario": str(role),
                        "source_role": str(role),
                        "source_run_id": source_run_id,
                        "source_corpus": f"sqlite://corpus_texts/{source_run_id}",
                        "source_path": str(source_path or ""),
                        "source_text_hash": str(text_hash),
                        "forecaster_type": forecaster_type,
                        "agent_id": 1,
                        "agent_seed": pair_seed,
                        "sequence_index": sequence_by_meeting[int(meeting)],
                        "state_group": pair_id,
                        "initial_expectation": fixed_prior,
                        "inflation_target": inflation_target_for_meeting(int(meeting)),
                        "previous_expectation_required": False,
                        "prompt_family_hash": _prompt_family_hash(forecaster_type),
                        "prior_source": prior_run_id,
                        "fixed_prior": fixed_prior,
                        "pair_id": pair_id,
                        "requested_model": model,
                        "requested_reasoning_effort": reasoning_effort,
                        "requested_temperature": float(requested_temperature),
                    }
                )

        metadata = {
            "design": "common_prior",
            "purpose": "Estimate contemporaneous forecast revisions with a fixed pre-treatment prior.",
            "source_run_id": source_run_id,
            "prior_run_id": prior_run_id,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "requested_temperature": requested_temperature,
            "sampling_control": "codex_cli_default",
            "seed_requested_but_not_supported_by_codex_cli": True,
            "random_seed_for_pair_ids": random_seed,
            "meeting_numbers": list(meetings),
            "iterations": list(draws),
            "roles": list(role_values),
            "forecaster_types": list(forecasters),
            "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
            "outcome": "new_expectation_minus_fixed_prior",
            "no_recursive_treatment_state": True,
        }
        with conn:
            register_sqlite_run(
                conn,
                run_id,
                metadata=metadata,
                first_meeting=meetings[0],
                last_meeting=meetings[-1],
                prompt_versions=[COMMON_PRIOR_PROMPT_VERSION],
                stances=role_values,
            )
            inserted = insert_agent_tasks(conn, run_id, tasks)
        return {"run_id": run_id, "tasks": len(tasks), **inserted, "metadata": metadata}
    finally:
        conn.close()


def prepare_arm_specific_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    source_run_id: str,
    meeting_numbers: Iterable[int],
    iterations: Iterable[int],
    roles: Iterable[str] = ARM_SPECIFIC_PRIOR_ROLES,
    forecaster_types: Iterable[str] = FORECASTER_TYPES,
    initial_expectation: float = 3.80,
    random_seed: int = 20260714,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    requested_temperature: float = 1.0,
) -> dict[str, Any]:
    """Register recursive paths in which each textual arm carries its own prior."""
    meetings = tuple(sorted(set(int(value) for value in meeting_numbers)))
    draws = tuple(sorted(set(int(value) for value in iterations)))
    role_values = tuple(
        dict.fromkeys(str(value).strip().lower() for value in roles if str(value).strip())
    )
    forecasters = _normalize_forecasters(forecaster_types)
    if not meetings or not draws or not role_values:
        raise ValueError("Meetings, iterations, and roles must all be non-empty")
    unknown_roles = sorted(set(role_values) - set(ARM_SPECIFIC_PRIOR_ROLES))
    if unknown_roles:
        raise ValueError(f"Unknown arm-specific prior role(s): {unknown_roles}")

    conn = connect_database(database_path)
    try:
        source_rows = conn.execute(
            """
            SELECT meeting_number, iteration, role, source_path, text_hash
            FROM corpus_texts WHERE run_id = ?
            ORDER BY iteration, role, meeting_number
            """,
            (source_run_id,),
        ).fetchall()
        selected = [
            row
            for row in source_rows
            if int(row[0]) in meetings
            and int(row[1]) in draws
            and str(row[2]) in role_values
        ]
        expected_texts = {
            (meeting, draw, role)
            for meeting in meetings
            for draw in draws
            for role in role_values
        }
        found_texts = {(int(row[0]), int(row[1]), str(row[2])) for row in selected}
        missing_texts = sorted(expected_texts - found_texts)
        if missing_texts:
            raise ValueError(f"Missing source texts in {source_run_id!r}: {missing_texts[:8]}")

        sequence_by_meeting = {
            meeting: index for index, meeting in enumerate(meetings, start=1)
        }
        tasks: list[dict[str, Any]] = []
        for meeting, iteration, role, source_path, text_hash in selected:
            sequence_index = sequence_by_meeting[int(meeting)]
            for forecaster_type in forecasters:
                pair_id = (
                    f"meeting-{int(meeting):03d}-iter-{int(iteration):02d}-{forecaster_type}"
                )
                path_seed = _paired_seed(
                    random_seed, meetings[0], int(iteration), forecaster_type
                )
                state_group = (
                    f"{run_id}::iter-{int(iteration):02d}::{str(role)}::{forecaster_type}"
                )
                task_id = (
                    f"iter-{int(iteration):02d}-{str(role)}-{forecaster_type}"
                    f"-seq-{sequence_index:03d}-meeting-{int(meeting):03d}"
                )
                tasks.append(
                    {
                        "task_id": task_id,
                        "design": ARM_SPECIFIC_PRIOR_DESIGN,
                        "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
                        "meeting_number": int(meeting),
                        "corpus_iteration": int(iteration),
                        "scenario": str(role),
                        "source_role": str(role),
                        "source_run_id": source_run_id,
                        "source_corpus": f"sqlite://corpus_texts/{source_run_id}",
                        "source_path": str(source_path or ""),
                        "source_text_hash": str(text_hash),
                        "forecaster_type": forecaster_type,
                        "agent_id": 1,
                        "agent_seed": path_seed,
                        "sequence_index": sequence_index,
                        "state_group": state_group,
                        "initial_expectation": float(initial_expectation),
                        "inflation_target": inflation_target_for_meeting(int(meeting)),
                        "previous_expectation_required": sequence_index > 1,
                        "prompt_family_hash": _prompt_family_hash(forecaster_type),
                        "prior_source": (
                            "initial" if sequence_index == 1 else f"{run_id}:same_arm_lag"
                        ),
                        "fixed_prior": None,
                        "pair_id": pair_id,
                        "requested_model": model,
                        "requested_reasoning_effort": reasoning_effort,
                        "requested_temperature": float(requested_temperature),
                    }
                )

        metadata = {
            "design": ARM_SPECIFIC_PRIOR_DESIGN,
            "purpose": (
                "Estimate persistent forecast paths when each textual arm carries "
                "its own lagged synthetic expectation."
            ),
            "source_run_id": source_run_id,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "requested_temperature": requested_temperature,
            "sampling_control": "codex_cli_default",
            "seed_requested_but_not_supported_by_codex_cli": True,
            "random_seed_for_path_ids": random_seed,
            "initial_expectation": float(initial_expectation),
            "meeting_numbers": list(meetings),
            "iterations": list(draws),
            "roles": list(role_values),
            "forecaster_types": list(forecasters),
            "prompt_version": COMMON_PRIOR_PROMPT_VERSION,
            "outcome": "new_expectation_level",
            "reference_arm": "original",
            "own_arm_recursive_state": True,
        }
        with conn:
            register_sqlite_run(
                conn,
                run_id,
                metadata=metadata,
                first_meeting=meetings[0],
                last_meeting=meetings[-1],
                prompt_versions=[COMMON_PRIOR_PROMPT_VERSION],
                stances=role_values,
            )
            inserted = insert_agent_tasks(conn, run_id, tasks)
        return {"run_id": run_id, "tasks": len(tasks), **inserted, "metadata": metadata}
    finally:
        conn.close()


def _record_from_response(task: dict[str, Any], response, requested_temperature: float) -> dict[str, Any]:
    metadata = dict(response.metadata or {})
    base = {
        "task_id": task["task_id"],
        "created_at": _utc_now(),
        "runner": response.runner,
        "runner_dry_run": response.dry_run,
        "model": response.model,
        "reasoning_effort": task["requested_reasoning_effort"],
        "temperature": requested_temperature,
        "agent_id": task["agent_id"],
        "agent_seed": task["agent_seed"],
        "state_group": task["state_group"],
        "scenario": task["scenario"],
        "corpus_iteration": task["corpus_iteration"],
        "sequence_index": task["sequence_index"],
        "meeting_number": task["meeting_number"],
        "forecaster_type": task["forecaster_type"],
        "inflation_target": task["inflation_target"],
        "previous_expectation": task["fixed_prior"],
        "perceived_tone": None,
        "request_input_hash": response.input_hash,
        "response_metadata": metadata,
        "design": task["design"],
        "prompt_version": task["prompt_version"],
        "pair_id": task["pair_id"],
        "source_role": task["source_role"],
        "source_text_hash": task["source_text_hash"],
        "temperature_applied": bool(metadata.get("temperature_applied", False)),
        "seed_applied": bool(metadata.get("seed_applied", False)),
        "response_schema_applied": bool(metadata.get("response_schema_applied", False)),
        "sampling_control": metadata.get("sampling_control"),
    }
    if response.dry_run:
        return {
            **base,
            "status": "dry_run",
            "new_expectation": None,
            "forecast_revision": None,
            "output_text_hash": None,
        }
    if metadata.get("returncode") not in (None, 0):
        lowered = response.text.lower()
        status = "usage_limited" if any(pattern in lowered for pattern in USAGE_LIMIT_PATTERNS) else "failed"
        return {
            **base,
            "status": status,
            "new_expectation": None,
            "forecast_revision": None,
            "output_text_hash": _hash_text(response.text),
            "error": response.text[:1000],
        }
    try:
        decision = parse_agent_decision(response.text, prompt_version=task["prompt_version"])
        new_expectation = float(decision.new_expectation)
        return {
            **base,
            "status": "completed",
            "new_expectation": new_expectation,
            "forecast_revision": new_expectation - float(task["fixed_prior"]),
            "output_text_hash": _hash_text(response.text),
        }
    except Exception as exc:
        return {
            **base,
            "status": "parse_failed",
            "new_expectation": None,
            "forecast_revision": None,
            "output_text_hash": _hash_text(response.text),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "raw_response_preview": response.text[:1000],
        }


def _execute_task(task: dict[str, Any], text: str, runner: LLMRunner) -> dict[str, Any]:
    request = None
    try:
        request = build_agent_request(
            text=text,
            forecaster_type=task["forecaster_type"],
            previous_expectation=float(task["fixed_prior"]),
            inflation_target=float(task["inflation_target"]),
            seed=int(task["agent_seed"]),
            meeting_number=int(task["meeting_number"]),
            model=task["requested_model"],
            reasoning_effort=task["requested_reasoning_effort"],
            temperature=float(task["requested_temperature"]),
            prompt_version=task["prompt_version"],
        )
        response = runner.generate(request)
        record = _record_from_response(task, response, float(task["requested_temperature"]))
        record.update(_request_provenance(request))
        return record
    except Exception as exc:
        record = {
            "task_id": task["task_id"],
            "status": "failed",
            "created_at": _utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "design": task["design"],
            "prompt_version": task["prompt_version"],
            "pair_id": task["pair_id"],
            "source_role": task["source_role"],
            "source_text_hash": task["source_text_hash"],
        }
        if request is not None:
            record.update(_request_provenance(request))
        return record


def _request_provenance(request) -> dict[str, Any]:
    schema_json = json.dumps(request.response_schema, ensure_ascii=False, sort_keys=True)
    metadata = request.metadata or {}
    return {
        "system_prompt_hash": _hash_text(request.system_prompt),
        "user_prompt_hash": _hash_text(request.user_prompt),
        "response_schema_hash": _hash_text(schema_json),
        "source_text_char_count": int(metadata.get("source_text_char_count", 0)),
        "supplied_text_char_count": int(metadata.get("supplied_text_char_count", 0)),
        "text_truncated": bool(metadata.get("text_truncated", False)),
    }


def _load_all_tasks(conn, run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(row[0])
        for row in conn.execute(
            "SELECT raw_json FROM agent_tasks WHERE run_id = ? ORDER BY forecaster_type, sequence_index, scenario",
            (run_id,),
        ).fetchall()
    ]


def _execute_recursive_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    runner: LLMRunner,
    expected_design: str,
    max_workers: int = 2,
) -> dict[str, Any]:
    conn = connect_database(database_path)
    try:
        tasks = _load_all_tasks(conn, run_id)
        grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for task in tasks:
            if task.get("design") != expected_design:
                raise ValueError(
                    f"Run {run_id!r} has design {task.get('design')!r}; "
                    f"expected {expected_design!r}"
                )
            key = (
                str(task["state_group"]),
                str(task["forecaster_type"]),
                int(task["agent_id"]),
            )
            grouped.setdefault(key, []).append(task)

        existing_rows = conn.execute(
            "SELECT task_id, status, previous_expectation, new_expectation FROM agent_results_latest WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        existing = {str(row[0]): row[1:] for row in existing_rows}
        sequences: list[dict[str, Any]] = []
        skipped = 0
        for group_tasks in grouped.values():
            ordered = sorted(group_tasks, key=lambda item: int(item["sequence_index"]))
            current: float | None = None
            first_pending = len(ordered)
            for index, task in enumerate(ordered):
                row = existing.get(task["task_id"])
                expected = float(current) if current is not None else float(task["initial_expectation"])
                if row and row[0] == "completed" and row[1] is not None and row[2] is not None:
                    if math.isclose(float(row[1]), expected, rel_tol=0.0, abs_tol=1e-9):
                        current = float(row[2])
                        skipped += 1
                        continue
                first_pending = index
                break
            if first_pending < len(ordered):
                pending = []
                for task in ordered[first_pending:]:
                    task_copy = dict(task)
                    pending.append((task_copy, load_agent_task_text(conn, task_copy)))
                sequences.append(
                    {
                        "sequence": pending,
                        "current_prior": (
                            float(current)
                            if current is not None
                            else float(pending[0][0]["initial_expectation"])
                        ),
                    }
                )

        records: list[dict[str, Any]] = []
        workers = max(1, min(int(max_workers), len(sequences) or 1))
        stopped_on_usage_limit = False

        def submit_next(executor, state, futures) -> None:
            task, text = state["sequence"][state["index"]]
            task = dict(task)
            task["fixed_prior"] = state["current_prior"]
            future = executor.submit(_execute_task, task, text, runner)
            futures[future] = state

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for sequence_state in sequences:
                state = {**sequence_state, "index": 0}
                submit_next(executor, state, futures)
            while futures:
                future = next(as_completed(tuple(futures)))
                state = futures.pop(future)
                record = future.result()
                with conn:
                    append_agent_result_event(conn, run_id, record)
                records.append(record)
                if record.get("status") == "usage_limited":
                    stopped_on_usage_limit = True
                if record.get("status") != "completed":
                    continue
                state["current_prior"] = float(record["new_expectation"])
                state["index"] += 1
                if not stopped_on_usage_limit and state["index"] < len(state["sequence"]):
                    submit_next(executor, state, futures)
        statuses: dict[str, int] = {}
        for record in records:
            statuses[record["status"]] = statuses.get(record["status"], 0) + 1
        return {
            "run_id": run_id,
            "design": expected_design,
            "tasks_total": len(tasks),
            "trajectories": len(grouped),
            "skipped_existing_prefix": skipped,
            "attempted": len(records),
            "statuses": statuses,
            "max_workers": workers,
            "stopped_on_usage_limit": stopped_on_usage_limit,
        }
    finally:
        conn.close()


def execute_original_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    runner: LLMRunner,
    max_workers: int = 2,
) -> dict[str, Any]:
    return _execute_recursive_prior_run(
        database_path,
        run_id,
        runner=runner,
        expected_design="original_path",
        max_workers=max_workers,
    )


def execute_arm_specific_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    runner: LLMRunner,
    max_workers: int = 2,
) -> dict[str, Any]:
    return _execute_recursive_prior_run(
        database_path,
        run_id,
        runner=runner,
        expected_design=ARM_SPECIFIC_PRIOR_DESIGN,
        max_workers=max_workers,
    )


def execute_common_prior_run(
    database_path: str | Path,
    run_id: str,
    *,
    runner: LLMRunner,
    limit: int | None = None,
    resume: bool = True,
    max_workers: int = 4,
    iterations: Iterable[int] | None = None,
    stop_on_usage_limit: bool = True,
) -> dict[str, Any]:
    iteration_values = tuple(sorted(set(int(value) for value in (iterations or ()))))
    conn = connect_database(database_path)
    try:
        tasks, total, completed = load_pending_agent_tasks(
            conn,
            run_id,
            limit=limit,
            resume=resume,
            done_statuses=("completed",),
            corpus_iterations=iteration_values,
        )
        hydrated = [(task, load_agent_task_text(conn, task)) for task in tasks]
        records: list[dict[str, Any]] = []
        workers = max(1, int(max_workers))
        stopped_on_usage_limit = False
        if workers == 1:
            for task, text in hydrated:
                record = _execute_task(task, text, runner)
                with conn:
                    append_agent_result_event(conn, run_id, record)
                records.append(record)
                if stop_on_usage_limit and record.get("status") == "usage_limited":
                    stopped_on_usage_limit = True
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                pending = iter(hydrated)
                futures = {}
                for _ in range(min(workers, len(hydrated))):
                    task, text = next(pending)
                    futures[executor.submit(_execute_task, task, text, runner)] = None
                while futures:
                    future = next(as_completed(tuple(futures)))
                    futures.pop(future)
                    record = future.result()
                    with conn:
                        append_agent_result_event(conn, run_id, record)
                    records.append(record)
                    if stop_on_usage_limit and record.get("status") == "usage_limited":
                        stopped_on_usage_limit = True
                    if stopped_on_usage_limit:
                        continue
                    try:
                        task, text = next(pending)
                    except StopIteration:
                        continue
                    futures[executor.submit(_execute_task, task, text, runner)] = None
        statuses: dict[str, int] = {}
        for record in records:
            statuses[record["status"]] = statuses.get(record["status"], 0) + 1
        return {
            "run_id": run_id,
            "tasks_total": total,
            "skipped_completed": completed,
            "selected": len(tasks),
            "attempted": len(records),
            "statuses": statuses,
            "max_workers": workers,
            "iterations": list(iteration_values),
            "stopped_on_usage_limit": stopped_on_usage_limit,
        }
    finally:
        conn.close()


def _runner_capability_summary(conn, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT temperature_applied, seed_applied, response_schema_applied, COUNT(*)
        FROM agent_results_latest WHERE run_id = ? AND status = 'completed'
        GROUP BY temperature_applied, seed_applied, response_schema_applied
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "temperature_applied": None if row[0] is None else bool(row[0]),
            "seed_applied": None if row[1] is None else bool(row[1]),
            "response_schema_applied": None if row[2] is None else bool(row[2]),
            "n": int(row[3]),
        }
        for row in rows
    ]


def audit_original_prior_run(database_path: str | Path, run_id: str) -> dict[str, Any]:
    conn = connect_database(database_path)
    try:
        rows = conn.execute(
            """
            SELECT t.forecaster_type, t.sequence_index, t.meeting_number,
                   t.initial_expectation, t.design, t.prompt_version,
                   r.status, r.previous_expectation, r.new_expectation,
                   r.perceived_tone
            FROM agent_tasks t
            LEFT JOIN agent_results_latest r
              ON r.run_id = t.run_id AND r.task_id = t.task_id
            WHERE t.run_id = ?
            ORDER BY t.forecaster_type, t.sequence_index
            """,
            (run_id,),
        ).fetchall()
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(str(row[0]), []).append(row)

        completed = 0
        transition_mismatches = 0
        unexpected_tone_outputs = 0
        for sequence in grouped.values():
            expected_prior: float | None = float(sequence[0][3]) if sequence else None
            for row in sequence:
                status, previous, new_value, tone = row[6], row[7], row[8], row[9]
                if status != "completed":
                    expected_prior = None
                    continue
                completed += 1
                if expected_prior is None or previous is None or not math.isclose(
                    float(previous), expected_prior, rel_tol=0.0, abs_tol=1e-9
                ):
                    transition_mismatches += 1
                expected_prior = float(new_value) if new_value is not None else None
                if tone is not None:
                    unexpected_tone_outputs += 1

        task_design_mismatches = sum(
            1
            for row in rows
            if row[4] != "original_path" or row[5] != COMMON_PRIOR_PROMPT_VERSION
        )
        source_hash_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM agent_tasks t
                LEFT JOIN minutes m ON m.meeting_number = t.meeting_number
                WHERE t.run_id = ?
                  AND (m.text_hash IS NULL OR m.text_hash != t.source_text_hash)
                """,
                (run_id,),
            ).fetchone()[0]
        )
        response_schema_missing = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed'
                  AND COALESCE(response_schema_applied, 0) != 1
                """,
                (run_id,),
            ).fetchone()[0]
        )
        request_provenance_violations = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed' AND (
                    system_prompt_hash IS NULL
                    OR user_prompt_hash IS NULL
                    OR response_schema_hash IS NULL
                    OR source_text_char_count IS NULL
                    OR supplied_text_char_count IS NULL
                    OR source_text_char_count != supplied_text_char_count
                    OR COALESCE(text_truncated, 1) != 0
                )
                """,
                (run_id,),
            ).fetchone()[0]
        )
        task_count = len(rows)
        return {
            "run_id": run_id,
            "design": "original_path",
            "tasks": task_count,
            "forecaster_trajectories": len(grouped),
            "completed": completed,
            "transition_mismatches": transition_mismatches,
            "task_design_mismatches": task_design_mismatches,
            "source_hash_mismatches": source_hash_mismatches,
            "unexpected_tone_outputs": unexpected_tone_outputs,
            "response_schema_missing": response_schema_missing,
            "request_provenance_violations": request_provenance_violations,
            "runner_capabilities": _runner_capability_summary(conn, run_id),
            "ready": (
                task_count > 0
                and completed == task_count
                and transition_mismatches == 0
                and task_design_mismatches == 0
                and source_hash_mismatches == 0
                and unexpected_tone_outputs == 0
                and response_schema_missing == 0
                and request_provenance_violations == 0
            ),
        }
    finally:
        conn.close()


def audit_arm_specific_prior_run(database_path: str | Path, run_id: str) -> dict[str, Any]:
    conn = connect_database(database_path)
    try:
        rows = conn.execute(
            """
            SELECT t.state_group, t.source_role, t.forecaster_type, t.agent_id,
                   t.sequence_index, t.meeting_number, t.initial_expectation,
                   t.previous_expectation_required, t.design, t.prompt_version,
                   t.pair_id, r.status, r.previous_expectation, r.new_expectation,
                   r.forecast_revision, r.perceived_tone
            FROM agent_tasks AS t
            LEFT JOIN agent_results_latest AS r
              ON r.run_id = t.run_id AND r.task_id = t.task_id
            WHERE t.run_id = ?
            ORDER BY t.state_group, t.forecaster_type, t.agent_id, t.sequence_index
            """,
            (run_id,),
        ).fetchall()
        grouped: dict[tuple[str, str, int], list[Any]] = {}
        for row in rows:
            key = (str(row[0]), str(row[2]), int(row[3]))
            grouped.setdefault(key, []).append(row)

        completed = 0
        transition_mismatches = 0
        revision_mismatches = 0
        unexpected_tone_outputs = 0
        trajectory_length_violations = 0
        expected_length = max((len(sequence) for sequence in grouped.values()), default=0)
        for sequence in grouped.values():
            ordered = sorted(sequence, key=lambda row: int(row[4]))
            if len(ordered) != expected_length or [int(row[4]) for row in ordered] != list(
                range(1, expected_length + 1)
            ):
                trajectory_length_violations += 1
            expected_prior: float | None = float(ordered[0][6]) if ordered else None
            for row in ordered:
                sequence_index = int(row[4])
                previous_required = bool(row[7])
                status, previous, new_value, revision, tone = row[11:16]
                if previous_required != (sequence_index > 1):
                    transition_mismatches += 1
                if status != "completed":
                    expected_prior = None
                    continue
                completed += 1
                if expected_prior is None or previous is None or not math.isclose(
                    float(previous), expected_prior, rel_tol=0.0, abs_tol=1e-9
                ):
                    transition_mismatches += 1
                if (
                    previous is None
                    or new_value is None
                    or revision is None
                    or not math.isclose(
                        float(revision),
                        float(new_value) - float(previous),
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                ):
                    revision_mismatches += 1
                expected_prior = float(new_value) if new_value is not None else None
                if tone is not None:
                    unexpected_tone_outputs += 1

        task_design_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_tasks
                WHERE run_id = ? AND (
                    design != ?
                    OR prompt_version != ?
                    OR fixed_prior IS NOT NULL
                    OR initial_expectation IS NULL
                    OR state_group IS NULL
                    OR pair_id IS NULL
                    OR source_role IS NULL
                )
                """,
                (run_id, ARM_SPECIFIC_PRIOR_DESIGN, COMMON_PRIOR_PROMPT_VERSION),
            ).fetchone()[0]
        )
        source_hash_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM agent_tasks AS t
                LEFT JOIN corpus_texts AS c
                  ON c.run_id = t.source_run_id
                 AND c.meeting_number = t.meeting_number
                 AND c.iteration = t.corpus_iteration
                 AND c.role = t.source_role
                WHERE t.run_id = ?
                  AND (c.text_hash IS NULL OR c.text_hash != t.source_text_hash)
                """,
                (run_id,),
            ).fetchone()[0]
        )
        pair_rows = conn.execute(
            """
            SELECT pair_id, source_role, agent_seed, prompt_version
            FROM agent_tasks WHERE run_id = ?
            ORDER BY pair_id, source_role
            """,
            (run_id,),
        ).fetchall()
        expected_roles = {str(row[1]) for row in pair_rows}
        roles_by_pair: dict[str, set[str]] = {}
        seeds_by_pair: dict[str, set[int]] = {}
        prompts_by_pair: dict[str, set[str]] = {}
        for pair_id, role, seed, prompt_version in pair_rows:
            pair_key = str(pair_id)
            roles_by_pair.setdefault(pair_key, set()).add(str(role))
            seeds_by_pair.setdefault(pair_key, set()).add(int(seed))
            prompts_by_pair.setdefault(pair_key, set()).add(str(prompt_version))
        pair_invariant_violations = sum(
            1
            for pair_id in roles_by_pair
            if roles_by_pair[pair_id] != expected_roles
            or len(seeds_by_pair[pair_id]) != 1
            or len(prompts_by_pair[pair_id]) != 1
        )
        response_schema_missing = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed'
                  AND COALESCE(response_schema_applied, 0) != 1
                """,
                (run_id,),
            ).fetchone()[0]
        )
        request_provenance_violations = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed' AND (
                    system_prompt_hash IS NULL
                    OR user_prompt_hash IS NULL
                    OR response_schema_hash IS NULL
                    OR source_text_char_count IS NULL
                    OR supplied_text_char_count IS NULL
                    OR source_text_char_count != supplied_text_char_count
                    OR COALESCE(text_truncated, 1) != 0
                )
                """,
                (run_id,),
            ).fetchone()[0]
        )
        task_count = len(rows)
        return {
            "run_id": run_id,
            "design": ARM_SPECIFIC_PRIOR_DESIGN,
            "tasks": task_count,
            "trajectories": len(grouped),
            "trajectory_length": expected_length,
            "completed": completed,
            "expected_roles": sorted(expected_roles),
            "transition_mismatches": transition_mismatches,
            "revision_mismatches": revision_mismatches,
            "trajectory_length_violations": trajectory_length_violations,
            "pair_invariant_violations": pair_invariant_violations,
            "task_design_mismatches": task_design_mismatches,
            "source_hash_mismatches": source_hash_mismatches,
            "unexpected_tone_outputs": unexpected_tone_outputs,
            "response_schema_missing": response_schema_missing,
            "request_provenance_violations": request_provenance_violations,
            "runner_capabilities": _runner_capability_summary(conn, run_id),
            "ready": (
                task_count > 0
                and completed == task_count
                and transition_mismatches == 0
                and revision_mismatches == 0
                and trajectory_length_violations == 0
                and pair_invariant_violations == 0
                and task_design_mismatches == 0
                and source_hash_mismatches == 0
                and unexpected_tone_outputs == 0
                and response_schema_missing == 0
                and request_provenance_violations == 0
            ),
        }
    finally:
        conn.close()


def audit_common_prior_run(database_path: str | Path, run_id: str) -> dict[str, Any]:
    conn = connect_database(database_path)
    try:
        summary = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT pair_id),
                   COUNT(DISTINCT prompt_version), COUNT(DISTINCT prompt_family_hash)
            FROM agent_tasks WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        completed = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_results_latest WHERE run_id = ? AND status = 'completed'",
                (run_id,),
            ).fetchone()[0]
        )
        pair_violations = conn.execute(
            """
            SELECT pair_id, COUNT(DISTINCT fixed_prior), COUNT(DISTINCT agent_seed),
                   COUNT(DISTINCT prompt_version)
            FROM agent_tasks WHERE run_id = ?
            GROUP BY pair_id
            HAVING COUNT(DISTINCT fixed_prior) != 1
                OR COUNT(DISTINCT agent_seed) != 1
                OR COUNT(DISTINCT prompt_version) != 1
            """,
            (run_id,),
        ).fetchall()
        pair_role_rows = conn.execute(
            "SELECT pair_id, source_role FROM agent_tasks WHERE run_id = ? ORDER BY pair_id, source_role",
            (run_id,),
        ).fetchall()
        expected_roles = {str(row[1]) for row in pair_role_rows}
        roles_by_pair: dict[str, set[str]] = {}
        for pair_id, role in pair_role_rows:
            roles_by_pair.setdefault(str(pair_id), set()).add(str(role))
        pair_role_violations = [
            pair_id for pair_id, roles in roles_by_pair.items() if roles != expected_roles
        ]
        task_design_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_tasks
                WHERE run_id = ? AND (
                    design != 'common_prior'
                    OR prompt_version != ?
                    OR fixed_prior IS NULL
                    OR pair_id IS NULL
                )
                """,
                (run_id, COMMON_PRIOR_PROMPT_VERSION),
            ).fetchone()[0]
        )
        source_hash_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM agent_tasks t
                LEFT JOIN corpus_texts c
                  ON c.run_id = t.source_run_id
                 AND c.meeting_number = t.meeting_number
                 AND c.iteration = t.corpus_iteration
                 AND c.role = t.source_role
                WHERE t.run_id = ?
                  AND (c.text_hash IS NULL OR c.text_hash != t.source_text_hash)
                """,
                (run_id,),
            ).fetchone()[0]
        )
        prior_task_rows = conn.execute(
            """
            SELECT prior_source, meeting_number, forecaster_type, fixed_prior
            FROM agent_tasks WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        prior_sources = sorted({str(row[0]) for row in prior_task_rows if row[0]})
        expected_priors: dict[tuple[str, int, str], float] = {}
        for prior_source in prior_sources:
            for row in conn.execute(
                """
                SELECT t.meeting_number, t.forecaster_type, r.previous_expectation
                FROM agent_tasks t
                JOIN agent_results_latest r
                  ON r.run_id = t.run_id AND r.task_id = t.task_id
                WHERE t.run_id = ? AND t.design = 'original_path'
                  AND r.status = 'completed'
                """,
                (prior_source,),
            ).fetchall():
                expected_priors[(prior_source, int(row[0]), str(row[1]))] = float(row[2])
        source_prior_mismatches = 0
        for prior_source, meeting, forecaster_type, fixed_prior in prior_task_rows:
            expected = expected_priors.get((str(prior_source), int(meeting), str(forecaster_type)))
            if expected is None or fixed_prior is None or not math.isclose(
                float(fixed_prior), expected, rel_tol=0.0, abs_tol=1e-9
            ):
                source_prior_mismatches += 1
        revision_mismatches = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed'
                  AND ABS(forecast_revision - (new_expectation - previous_expectation)) > 1e-9
                """,
                (run_id,),
            ).fetchone()[0]
        )
        tone_outputs = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_results_latest WHERE run_id = ? AND perceived_tone IS NOT NULL",
                (run_id,),
            ).fetchone()[0]
        )
        response_schema_missing = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed'
                  AND COALESCE(response_schema_applied, 0) != 1
                """,
                (run_id,),
            ).fetchone()[0]
        )
        request_provenance_violations = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM agent_results_latest
                WHERE run_id = ? AND status = 'completed' AND (
                    system_prompt_hash IS NULL
                    OR user_prompt_hash IS NULL
                    OR response_schema_hash IS NULL
                    OR source_text_char_count IS NULL
                    OR supplied_text_char_count IS NULL
                    OR source_text_char_count != supplied_text_char_count
                    OR COALESCE(text_truncated, 1) != 0
                )
                """,
                (run_id,),
            ).fetchone()[0]
        )
        return {
            "run_id": run_id,
            "tasks": int(summary[0]),
            "pairs": int(summary[1]),
            "prompt_versions": int(summary[2]),
            "prompt_family_hashes": int(summary[3]),
            "completed": completed,
            "pair_invariant_violations": len(pair_violations),
            "pair_role_violations": len(pair_role_violations),
            "expected_roles": sorted(expected_roles),
            "task_design_mismatches": task_design_mismatches,
            "source_hash_mismatches": source_hash_mismatches,
            "source_prior_mismatches": source_prior_mismatches,
            "revision_mismatches": revision_mismatches,
            "unexpected_tone_outputs": tone_outputs,
            "response_schema_missing": response_schema_missing,
            "request_provenance_violations": request_provenance_violations,
            "runner_capabilities": _runner_capability_summary(conn, run_id),
            "ready": (
                int(summary[0]) > 0
                and completed == int(summary[0])
                and not pair_violations
                and not pair_role_violations
                and task_design_mismatches == 0
                and source_hash_mismatches == 0
                and source_prior_mismatches == 0
                and revision_mismatches == 0
                and tone_outputs == 0
                and response_schema_missing == 0
                and request_provenance_violations == 0
            ),
        }
    finally:
        conn.close()


def audit_common_prior_gate(
    database_path: str | Path,
    run_id: str,
    *,
    iterations: Iterable[int],
) -> dict[str, Any]:
    draws = tuple(sorted(set(int(value) for value in iterations)))
    if not draws:
        raise ValueError("At least one gate iteration is required")
    full_audit = audit_common_prior_run(database_path, run_id)
    conn = connect_database(database_path)
    try:
        placeholders = ",".join("?" for _ in draws)
        params = [run_id, *draws]
        expected_tasks = int(
            conn.execute(
                f"SELECT COUNT(*) FROM agent_tasks WHERE run_id = ? "
                f"AND corpus_iteration IN ({placeholders})",
                params,
            ).fetchone()[0]
        )
        expected_pairs = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT pair_id) FROM agent_tasks WHERE run_id = ? "
                f"AND corpus_iteration IN ({placeholders})",
                params,
            ).fetchone()[0]
        )
        status_rows = conn.execute(
            f"""
            SELECT COALESCE(r.status, 'missing'), COUNT(*)
            FROM agent_tasks t
            LEFT JOIN agent_results_latest r
              ON r.run_id = t.run_id AND r.task_id = t.task_id
            WHERE t.run_id = ? AND t.corpus_iteration IN ({placeholders})
            GROUP BY COALESCE(r.status, 'missing')
            """,
            params,
        ).fetchall()
        statuses = {str(row[0]): int(row[1]) for row in status_rows}
        completed = statuses.get("completed", 0)
        violation_fields = (
            "pair_invariant_violations",
            "pair_role_violations",
            "task_design_mismatches",
            "source_hash_mismatches",
            "source_prior_mismatches",
            "revision_mismatches",
            "unexpected_tone_outputs",
            "response_schema_missing",
            "request_provenance_violations",
        )
        invariant_violations = sum(int(full_audit[field]) for field in violation_fields)
        return {
            "run_id": run_id,
            "iterations": list(draws),
            "expected_tasks": expected_tasks,
            "expected_pairs": expected_pairs,
            "completed": completed,
            "statuses": statuses,
            "full_plan_tasks": full_audit["tasks"],
            "full_plan_completed": full_audit["completed"],
            "invariant_violations": invariant_violations,
            "runner_capabilities": full_audit["runner_capabilities"],
            "ready": (
                expected_tasks > 0
                and completed == expected_tasks
                and set(statuses) == {"completed"}
                and invariant_violations == 0
            ),
        }
    finally:
        conn.close()
