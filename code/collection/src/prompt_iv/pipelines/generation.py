from __future__ import annotations

from pathlib import Path
from typing import Iterable

from prompt_iv.generation.tasks import build_rewrite_tasks, summarize_rewrite_tasks, write_rewrite_task_manifest


def prepare_generation_plan(
    minutes_dir: str | Path,
    output_jsonl: str | Path,
    stances: Iterable[str] = ("hawk", "dove"),
    iterations: int = 1,
    parts: int = 5,
    max_meetings: int | None = None,
    meeting_numbers: Iterable[int] | None = None,
    prompt_version: str | None = None,
) -> dict:
    tasks = build_rewrite_tasks(
        minutes_dir=minutes_dir,
        stances=stances,
        iterations=iterations,
        parts=parts,
        max_meetings=max_meetings,
        meeting_numbers=meeting_numbers,
        prompt_version=prompt_version,
    )
    written = write_rewrite_task_manifest(tasks, output_jsonl)
    summary = summarize_rewrite_tasks(tasks)
    summary.update(
        {
            "output": str(output_jsonl),
            "records_written": written,
            "dry_run": True,
        }
    )
    return summary
