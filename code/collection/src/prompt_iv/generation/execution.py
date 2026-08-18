from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json

from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.generation.prompts import build_rewrite_prompt_versioned, normalize_prompt_version
from prompt_iv.generation.tasks import build_rewrite_user_prompt
from prompt_iv.io.jsonl import append_jsonl, read_jsonl
from prompt_iv.io.minutes import read_text, segment_text
from prompt_iv.llm.runner import LLMRequest, LLMRunner
from prompt_iv.storage.sqlite import (
    append_rewrite_result_event,
    connect_database,
    load_pending_rewrite_tasks,
    refresh_rewrite_run_summary,
)


COMPLETED_STATUSES = {"completed"}
USAGE_LIMIT_PATTERNS = ("usage limit", "hit your usage limit")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_request_from_manifest(
    record: dict,
    model: str,
    reasoning_effort: str,
    temperature: float,
    missing: list[str] | None = None,
    extra: list[str] | None = None,
    retry_attempt: int = 0,
) -> tuple[LLMRequest, str]:
    full_text = read_text(record["source_path"])
    blocks = segment_text(full_text, parts=int(record["total_blocks"]))
    block_index = int(record["block_index"]) - 1
    if block_index < 0 or block_index >= len(blocks):
        raise ValueError(f"block_index out of range for task {record['task_id']!r}")

    original_text = blocks[block_index]
    stance = str(record["stance"])
    prompt_version = normalize_prompt_version(record.get("prompt_version"))
    system_prompt = build_rewrite_prompt_versioned(stance, prompt_version)
    user_prompt = build_rewrite_user_prompt(
        original_text,
        stance,
        missing=missing or (),
        extra=extra or (),
        prompt_version=prompt_version,
    )
    request = LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        metadata={
            "task": "counterfactual_rewrite",
            "task_id": record["task_id"],
            "meeting_number": record["meeting_number"],
            "iteration": record["iteration"],
            "block_index": record["block_index"],
            "total_blocks": record["total_blocks"],
            "stance": stance,
            "prompt_version": prompt_version,
            "manifest_input_hash": record.get("input_hash"),
            "retry_attempt": retry_attempt,
        },
    )
    return request, original_text


def _execute_one_rewrite_task(
    task: dict,
    runner: LLMRunner,
    model: str,
    reasoning_effort: str,
    temperature: float,
    store_text: bool,
    max_retries: int,
) -> tuple[dict, dict[str, int]]:
    stats = {"failed": 0, "numeric_failed": 0, "dry_run": 0, "retry_calls": 0}
    try:
        missing: list[str] = []
        extra: list[str] = []
        response = None
        original_text = ""
        numeric_ok = None
        attempts_used = 0
        for attempt in range(max_retries + 1):
            attempts_used = attempt
            request, original_text = _build_request_from_manifest(
                task,
                model,
                reasoning_effort,
                temperature,
                missing=missing,
                extra=extra,
                retry_attempt=attempt,
            )
            response = runner.generate(request)
            if attempt:
                stats["retry_calls"] += 1
            if response.dry_run:
                break
            returncode = (response.metadata or {}).get("returncode")
            if returncode not in (None, 0):
                if any(pattern in response.text.lower() for pattern in USAGE_LIMIT_PATTERNS):
                    stats["usage_limited"] = stats.get("usage_limited", 0) + 1
                    return (
                        {
                            "task_id": task.get("task_id"),
                            "status": "usage_limited",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "meeting_number": task.get("meeting_number"),
                            "source_path": task.get("source_path"),
                            "iteration": task.get("iteration"),
                            "block_index": task.get("block_index"),
                            "total_blocks": task.get("total_blocks"),
                            "stance": task.get("stance"),
                            "prompt_version": task.get("prompt_version"),
                            "runner": response.runner,
                            "runner_dry_run": response.dry_run,
                            "model": response.model,
                            "reasoning_effort": reasoning_effort,
                            "temperature": temperature,
                            "manifest_input_hash": task.get("input_hash"),
                            "request_input_hash": response.input_hash,
                            "output_text_hash": None,
                            "numeric_ok": None,
                            "numeric_missing": [],
                            "numeric_extra": [],
                            "retry_attempts_used": attempts_used,
                            "generated_text": None,
                            "response_metadata": response.metadata,
                            "blocked_reason": "codex_cli_usage_limit",
                        },
                        stats,
                    )
                raise RuntimeError(f"LLM runner returned nonzero exit code {returncode}: {response.text[:300]}")
            numeric_ok, missing, extra = check_numeric_multiset(original_text, response.text)
            if numeric_ok:
                break

        if response is None:
            raise RuntimeError("LLM runner did not return a response.")
        if response.dry_run:
            status = "dry_run"
            numeric_ok = None
            missing = []
            extra = []
            output_hash = None
            generated_text = None
            stats["dry_run"] += 1
        else:
            status = "completed" if numeric_ok else "numeric_failed"
            output_hash = _sha256_text(response.text)
            generated_text = response.text if store_text else None
            stats["numeric_failed"] += 0 if numeric_ok else 1

        return (
            {
                "task_id": task["task_id"],
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "meeting_number": task.get("meeting_number"),
                "source_path": task.get("source_path"),
                "iteration": task.get("iteration"),
                "block_index": task.get("block_index"),
                "total_blocks": task.get("total_blocks"),
                "stance": task.get("stance"),
                "prompt_version": task.get("prompt_version"),
                "runner": response.runner,
                "runner_dry_run": response.dry_run,
                "model": response.model,
                "reasoning_effort": reasoning_effort,
                "temperature": temperature,
                "manifest_input_hash": task.get("input_hash"),
                "request_input_hash": response.input_hash,
                "output_text_hash": output_hash,
                "numeric_ok": numeric_ok,
                "numeric_missing": missing,
                "numeric_extra": extra,
                "retry_attempts_used": attempts_used,
                "generated_text": generated_text,
                "response_metadata": response.metadata,
            },
            stats,
        )
    except Exception as exc:
        stats["failed"] += 1
        return (
            {
                "task_id": task.get("task_id"),
                "status": "failed",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            stats,
        )


def execute_rewrite_manifest(
    manifest_path: str | Path,
    output_jsonl: str | Path,
    runner: LLMRunner,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
    store_text: bool = True,
    max_retries: int = 2,
    max_workers: int = 1,
) -> dict:
    tasks = read_jsonl(manifest_path)
    existing = read_jsonl(output_jsonl) if resume else []
    resumable_statuses = set(COMPLETED_STATUSES)
    if runner.name == "dry-run":
        resumable_statuses.add("dry_run")
    done_ids = {
        str(row.get("task_id"))
        for row in existing
        if row.get("status") in resumable_statuses and row.get("task_id") is not None
    }

    pending = [task for task in tasks if str(task.get("task_id")) not in done_ids]
    if limit is not None:
        pending = pending[:limit]

    failed = 0
    numeric_failed = 0
    dry_run = 0
    usage_limited = 0
    retry_calls = 0
    written = 0

    def consume_result(record: dict, stats: dict[str, int]) -> None:
        nonlocal failed, numeric_failed, dry_run, usage_limited, retry_calls, written
        written += append_jsonl(output_jsonl, [record])
        failed += stats["failed"]
        numeric_failed += stats["numeric_failed"]
        dry_run += stats["dry_run"]
        usage_limited += stats.get("usage_limited", 0)
        retry_calls += stats["retry_calls"]

    max_workers = max(1, int(max_workers))
    if max_workers == 1 or len(pending) <= 1:
        for task in pending:
            record, stats = _execute_one_rewrite_task(
                task,
                runner,
                model,
                reasoning_effort,
                temperature,
                store_text,
                max_retries,
            )
            consume_result(record, stats)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _execute_one_rewrite_task,
                    task,
                    runner,
                    model,
                    reasoning_effort,
                    temperature,
                    store_text,
                    max_retries,
                )
                for task in pending
            ]
            for future in as_completed(futures):
                record, stats = future.result()
                consume_result(record, stats)
    return {
        "manifest": str(manifest_path),
        "output": str(output_jsonl),
        "runner": runner.name,
        "tasks_total": len(tasks),
        "skipped_existing": len(done_ids) if resume else 0,
        "attempted": len(pending),
        "records_written": written,
        "dry_run": dry_run,
        "usage_limited": usage_limited,
        "failed": failed,
        "numeric_failed": numeric_failed,
        "resume": resume,
        "store_text": store_text,
        "max_retries": max_retries,
        "max_workers": max_workers,
        "retry_calls": retry_calls,
    }


def execute_rewrite_database(
    database_path: str | Path,
    run_id: str,
    runner: LLMRunner,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
    store_text: bool = True,
    max_retries: int = 2,
    max_workers: int = 1,
) -> dict:
    done_statuses = set(COMPLETED_STATUSES)
    if runner.name == "dry-run":
        done_statuses.add("dry_run")

    conn = connect_database(database_path)
    try:
        tasks, tasks_total, done_existing = load_pending_rewrite_tasks(
            conn,
            run_id=run_id,
            limit=limit,
            resume=resume,
            done_statuses=done_statuses,
        )
        completed_before = conn.execute(
            "SELECT COUNT(*) FROM rewrite_results_latest WHERE run_id = ? AND status = 'completed'",
            (run_id,),
        ).fetchone()[0]

        failed = 0
        numeric_failed = 0
        dry_run = 0
        usage_limited = 0
        retry_calls = 0
        written = 0

        def consume_result(record: dict, stats: dict[str, int]) -> None:
            nonlocal failed, numeric_failed, dry_run, usage_limited, retry_calls, written
            with conn:
                append_rewrite_result_event(conn, run_id, record)
                refresh_rewrite_run_summary(conn, run_id)
            written += 1
            failed += stats["failed"]
            numeric_failed += stats["numeric_failed"]
            dry_run += stats["dry_run"]
            usage_limited += stats.get("usage_limited", 0)
            retry_calls += stats["retry_calls"]

        max_workers = max(1, int(max_workers))
        if max_workers == 1 or len(tasks) <= 1:
            for task in tasks:
                record, stats = _execute_one_rewrite_task(
                    task,
                    runner,
                    model,
                    reasoning_effort,
                    temperature,
                    store_text,
                    max_retries,
                )
                consume_result(record, stats)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(
                        _execute_one_rewrite_task,
                        task,
                        runner,
                        model,
                        reasoning_effort,
                        temperature,
                        store_text,
                        max_retries,
                    )
                    for task in tasks
                ]
                for future in as_completed(futures):
                    record, stats = future.result()
                    consume_result(record, stats)

        summary = refresh_rewrite_run_summary(conn, run_id)
        completed_after = summary["rewrite_completed_latest"]
        return {
            "database": str(database_path),
            "run_id": run_id,
            "runner": runner.name,
            "tasks_total": tasks_total,
            "skipped_existing": done_existing if resume else 0,
            "attempted": len(tasks),
            "records_written": written,
            "dry_run": dry_run,
            "usage_limited": usage_limited,
            "failed": failed,
            "numeric_failed": numeric_failed,
            "resume": resume,
            "store_text": store_text,
            "max_retries": max_retries,
            "max_workers": max_workers,
            "retry_calls": retry_calls,
            "completed_before": completed_before,
            "completed_after": completed_after,
            "remaining_after": max(tasks_total - completed_after, 0),
        }
    finally:
        conn.close()
