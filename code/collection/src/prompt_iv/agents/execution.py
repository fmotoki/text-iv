from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import math

from prompt_iv.agents.simulation import build_agent_request, parse_agent_decision
from prompt_iv.io.jsonl import append_jsonl, read_jsonl
from prompt_iv.io.minutes import read_text
from prompt_iv.llm.runner import LLMRunner
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path


COMPLETED_STATUSES = {"completed"}
USAGE_LIMIT_PATTERNS = ("usage limit", "hit your usage limit")


def _state_key(row: dict) -> tuple[str, str, int]:
    return str(row.get("state_group", "baseline")), str(row["forecaster_type"]), int(row["agent_id"])


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _latest_rows_by_task(existing_rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in existing_rows:
        task_id = row.get("task_id")
        if task_id is None:
            continue
        latest[str(task_id)] = row
    return latest


def _float_equal(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def _valid_resume_prefix(
    tasks: list[dict],
    latest_rows: dict[str, dict],
    resumable_statuses: set[str],
    model: str,
    reasoning_effort: str,
    temperature: float,
) -> tuple[set[str], float | None]:
    """Return task ids that form a valid recursive prefix and its final state.

    Synthetic forecasters are stateful: task k+1 must use task k's output as
    `previous_expectation`. A completed row after a missing/failed middle task is
    not a safe resume point, even if it has the same task id in the append-only
    log. This helper only marks the longest sequentially consistent prefix done.
    """
    done_ids: set[str] = set()
    current_expectation: float | None = None

    for task in sorted(tasks, key=lambda row: int(row["sequence_index"])):
        task_id = str(task["task_id"])
        row = latest_rows.get(task_id)
        if row is None or row.get("status") not in resumable_statuses:
            break
        if row.get("status") == "dry_run":
            done_ids.add(task_id)
            continue
        if row.get("new_expectation") is None or row.get("previous_expectation") is None:
            break

        expected_previous = (
            float(current_expectation) if current_expectation is not None else float(task["initial_expectation"])
        )
        if not _float_equal(float(row["previous_expectation"]), expected_previous):
            raise ValueError("Cached agent prior differs from its chronological predecessor")
        request = build_agent_request(text=read_text(task["source_path"]),
            forecaster_type=str(task["forecaster_type"]), previous_expectation=expected_previous,
            inflation_target=float(task["inflation_target"]), seed=int(task["agent_seed"]),
            meeting_number=int(task["meeting_number"]), model=model,
            reasoning_effort=reasoning_effort, temperature=temperature)
        if row.get("runner_dry_run") or row.get("request_input_hash") != request.input_hash or any(row.get(key) != value for key, value in specification_metadata().items()):
            raise ValueError("Cached agent source/prompt/model/specification differs")

        done_ids.add(task_id)
        current_expectation = float(row["new_expectation"])

    return done_ids, current_expectation


def _execute_agent_sequence(
    sequence_tasks: list[dict],
    initial_previous_expectation: float | None,
    runner: LLMRunner,
    model: str,
    reasoning_effort: str,
    temperature: float,
) -> tuple[list[dict], dict[str, int]]:
    records: list[dict] = []
    stats = {"failed": 0, "dry_run": 0, "parse_failed": 0, "usage_limited": 0}
    current_expectation = initial_previous_expectation

    for task in sorted(sequence_tasks, key=lambda row: int(row["sequence_index"])):
        try:
            previous_expectation = (
                float(current_expectation) if current_expectation is not None else float(task["initial_expectation"])
            )
            text = read_text(task["source_path"])
            request = build_agent_request(
                text=text,
                forecaster_type=str(task["forecaster_type"]),
                previous_expectation=previous_expectation,
                inflation_target=float(task["inflation_target"]),
                seed=int(task["agent_seed"]),
                meeting_number=int(task["meeting_number"]),
                model=model,
                reasoning_effort=reasoning_effort,
                temperature=temperature,
            )
            response = runner.generate(request)

            if response.dry_run:
                status = "dry_run"
                perceived_tone = None
                new_expectation = None
                output_hash = None
                stats["dry_run"] += 1
            elif (response.metadata or {}).get("returncode") not in (None, 0) and any(
                pattern in response.text.lower() for pattern in USAGE_LIMIT_PATTERNS
            ):
                status = "usage_limited"
                perceived_tone = None
                new_expectation = None
                output_hash = None
                stats["usage_limited"] += 1
            elif (response.metadata or {}).get("returncode") not in (None, 0):
                status = "failed"
                perceived_tone = None
                new_expectation = None
                output_hash = _sha256_text(response.text)
                stats["failed"] += 1
            else:
                try:
                    decision = parse_agent_decision(response.text)
                    status = "completed"
                    perceived_tone = decision.perceived_tone
                    new_expectation = decision.new_expectation
                    output_hash = _sha256_text(response.text)
                    current_expectation = decision.new_expectation
                except Exception as exc:
                    status = "parse_failed"
                    perceived_tone = None
                    new_expectation = None
                    output_hash = _sha256_text(response.text)
                    stats["parse_failed"] += 1
                    response = response.__class__(
                        text=response.text,
                        runner=response.runner,
                        model=response.model,
                        input_hash=response.input_hash,
                        created_at=response.created_at,
                        dry_run=response.dry_run,
                        metadata={**(response.metadata or {}), "parse_error": str(exc)},
                    )

            records.append(
                {
                    "task_id": task["task_id"],
                    "status": status,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "runner": response.runner,
                    "runner_dry_run": response.dry_run,
                    "model": response.model,
                    "reasoning_effort": reasoning_effort,
                    "temperature": temperature,
                    "agent_id": task["agent_id"],
                    "agent_seed": task["agent_seed"],
                    "state_group": task.get("state_group", "baseline"),
                    "scenario": task.get("scenario"),
                    "corpus_iteration": task.get("corpus_iteration"),
                    "sequence_index": task["sequence_index"],
                    "meeting_number": task["meeting_number"],
                    "forecaster_type": task["forecaster_type"],
                    "inflation_target": task["inflation_target"],
                    "previous_expectation": previous_expectation,
                    "perceived_tone": perceived_tone,
                    "new_expectation": new_expectation,
                    "request_input_hash": response.input_hash,
                    "output_text_hash": output_hash,
                    "response_metadata": response.metadata,
                }
            )
            if status != "completed":
                break
        except Exception as exc:
            stats["failed"] += 1
            records.append(
                {
                    "task_id": task.get("task_id"),
                    "status": "failed",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            break
    return records, stats


def execute_agent_manifest(
    manifest_path: str | Path,
    output_jsonl: str | Path,
    runner: LLMRunner,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
    max_workers: int = 1,
) -> dict:
    spec = load_spec(require_frozen=True)
    validate_artifact_path(manifest_path, spec)
    output_jsonl = validate_artifact_path(output_jsonl, spec)
    if (model, reasoning_effort) != (spec["model"], spec["reasoning_effort"]):
        raise ValueError("Agent model/effort disagree with the specification")
    tasks = read_jsonl(manifest_path)
    existing = read_jsonl(output_jsonl) if resume else []
    resumable_statuses = set(COMPLETED_STATUSES)
    if runner.name == "dry-run":
        resumable_statuses.add("dry_run")

    grouped_all: dict[tuple[str, str, int], list[dict]] = {}
    for task in tasks:
        grouped_all.setdefault(_state_key(task), []).append(task)

    latest_rows = _latest_rows_by_task(existing) if resume else {}
    done_ids: set[str] = set()
    state: dict[tuple[str, str, int], float] = {}
    for key, group_tasks in grouped_all.items():
        group_done, group_state = _valid_resume_prefix(group_tasks, latest_rows, resumable_statuses,
                                                      model, reasoning_effort, temperature)
        done_ids.update(group_done)
        if group_state is not None:
            state[key] = group_state

    manifest_ids = {str(task.get("task_id")) for task in tasks}
    resumable_latest_ids = {
        task_id
        for task_id, row in latest_rows.items()
        if row.get("status") in resumable_statuses and task_id in manifest_ids
    }
    invalidated_existing = len(resumable_latest_ids - done_ids)

    pending = [task for task in tasks if str(task.get("task_id")) not in done_ids]
    pending.sort(
        key=lambda row: (
            str(row.get("state_group", "baseline")),
            str(row["forecaster_type"]),
            int(row["agent_id"]),
            int(row["sequence_index"]),
        )
    )
    if limit is not None:
        pending = pending[:limit]

    records: list[dict] = []
    failed = 0
    dry_run = 0
    parse_failed = 0
    usage_limited = 0

    grouped: dict[tuple[str, str, int], list[dict]] = {}
    for task in pending:
        grouped.setdefault(_state_key(task), []).append(task)

    max_workers = max(1, int(max_workers))
    if max_workers == 1 or len(grouped) <= 1:
        results = [
            _execute_agent_sequence(group_tasks, state.get(key), runner, model, reasoning_effort, temperature)
            for key, group_tasks in grouped.items()
        ]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _execute_agent_sequence,
                    group_tasks,
                    state.get(key),
                    runner,
                    model,
                    reasoning_effort,
                    temperature,
                )
                for key, group_tasks in grouped.items()
            ]
            for future in as_completed(futures):
                results.append(future.result())

    for group_records, stats in results:
        records.extend(group_records)
        failed += stats["failed"]
        dry_run += stats["dry_run"]
        parse_failed += stats["parse_failed"]
        usage_limited += stats["usage_limited"]

    for record in records:
        record.update(specification_metadata(spec))
    written = append_jsonl(output_jsonl, records)
    attempted = len(records)
    return {
        "manifest": str(manifest_path),
        "output": str(output_jsonl),
        "runner": runner.name,
        "tasks_total": len(tasks),
        "skipped_existing": len(done_ids) if resume else 0,
        "pending_selected": len(pending),
        "attempted": attempted,
        "records_written": written,
        "dry_run": dry_run,
        "usage_limited": usage_limited,
        "failed": failed,
        "parse_failed": parse_failed,
        "resume": resume,
        "max_workers": max_workers,
        "groups_attempted": len(grouped),
        "invalidated_existing": invalidated_existing if resume else 0,
    }
