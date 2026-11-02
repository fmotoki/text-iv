from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import hashlib

import pandas as pd

from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.io.jsonl import read_jsonl
from prompt_iv.io.minutes import read_text, segment_text
from prompt_iv.tone import SentimentAnalyzer
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path


WRAPPER_RE = re.compile(
    r"(^\s*(aqui|segue|claro|vers[aã]o)\b)|(```)|(\btexto reescrito\b)",
    flags=re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or "", flags=re.UNICODE))


def _paragraph_count(text: str) -> int:
    return len([line for line in str(text or "").splitlines() if line.strip()])


def _table_lines(text: str) -> list[str]:
    return [
        line.rstrip()
        for line in str(text or "").splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]


def _original_block(row: dict[str, Any]) -> str:
    full_text = read_text(row["source_path"])
    blocks = segment_text(full_text, parts=int(row["total_blocks"]))
    block_index = int(row["block_index"]) - 1
    if block_index < 0 or block_index >= len(blocks):
        raise ValueError(f"block_index out of range for task {row.get('task_id')!r}")
    return blocks[block_index]


def evaluate_counterfactual_results(
    results_jsonl: str | Path,
    output_json: str | Path | None = None,
    details_csv: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    spec = load_spec(require_frozen=True)
    validate_artifact_path(results_jsonl, spec)
    if output_json:
        output_json = validate_artifact_path(output_json, spec)
    if details_csv:
        details_csv = validate_artifact_path(details_csv, spec)
    if manifest_path is None:
        raise ValueError("Quality acceptance requires the exact planned rewrite manifest")
    manifest = read_jsonl(validate_artifact_path(manifest_path, spec))
    rows = read_jsonl(results_jsonl)
    event_count = len(rows)
    latest = {str(row["task_id"]): row for row in rows if row.get("task_id")}
    expected = {str(row["task_id"]): row for row in manifest}
    if len(expected) != len(manifest) or set(latest) != set(expected):
        raise ValueError("Latest rewrite task keys do not equal the planned Cartesian product")
    for task_id, task in expected.items():
        row = latest[task_id]
        if row.get("status") != "completed" or row.get("runner_dry_run") or row.get("numeric_ok") is not True or not row.get("generated_text"):
            raise ValueError(f"Rewrite task is incomplete or not numeric-valid: {task_id}")
        if row.get("manifest_input_hash") != task["input_hash"] or row.get("model") != spec["model"] or row.get("reasoning_effort") != spec["reasoning_effort"] or any(row.get(key) != value for key, value in specification_metadata(spec).items()):
            raise ValueError(f"Rewrite source/model/specification mismatch: {task_id}")
        if hashlib.sha256(_original_block(task).encode("utf-8")).hexdigest() != task["original_text_hash"]:
            raise ValueError(f"Original source hash changed for {task_id}")
    rows = list(latest.values())
    analyzer = SentimentAnalyzer()
    details: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}

    for row in rows:
        status = str(row.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
        generated_text = row.get("generated_text")

        record: dict[str, Any] = {
            "task_id": row.get("task_id"),
            "status": status,
            "meeting_number": row.get("meeting_number"),
            "iteration": row.get("iteration"),
            "block_index": row.get("block_index"),
            "total_blocks": row.get("total_blocks"),
            "stance": row.get("stance"),
            "prompt_version": row.get("prompt_version"),
            "runner": row.get("runner"),
            "model": row.get("model"),
            "numeric_ok_recorded": row.get("numeric_ok"),
            "has_generated_text": bool(generated_text),
        }

        if generated_text:
            original_text = _original_block(row)
            numeric_ok, missing, extra = check_numeric_multiset(original_text, str(generated_text))
            original_words = _word_count(original_text)
            generated_words = _word_count(str(generated_text))
            original_paragraphs = _paragraph_count(original_text)
            generated_paragraphs = _paragraph_count(str(generated_text))
            original_table_lines = _table_lines(original_text)
            generated_table_lines = _table_lines(str(generated_text))
            table_format_ok = True
            if original_table_lines:
                table_format_ok = original_table_lines == generated_table_lines
            original_tone = analyzer.analyze(original_text)
            generated_tone = analyzer.analyze(str(generated_text))
            tone_delta = generated_tone.tone_score - original_tone.tone_score
            stance = str(row.get("stance", "")).lower()
            if stance == "hawk":
                lexicon_direction_ok = tone_delta >= 0
            elif stance == "dove":
                lexicon_direction_ok = tone_delta <= 0
            else:
                lexicon_direction_ok = None

            record.update(
                {
                    "numeric_ok_recomputed": numeric_ok,
                    "numeric_missing_count": len(missing),
                    "numeric_extra_count": len(extra),
                    "original_words": original_words,
                    "generated_words": generated_words,
                    "length_ratio": generated_words / original_words if original_words else None,
                    "original_paragraphs": original_paragraphs,
                    "generated_paragraphs": generated_paragraphs,
                    "paragraph_count_ok": original_paragraphs == generated_paragraphs,
                    "original_table_lines": len(original_table_lines),
                    "generated_table_lines": len(generated_table_lines),
                    "table_format_ok": table_format_ok,
                    "original_tone_score": original_tone.tone_score,
                    "generated_tone_score": generated_tone.tone_score,
                    "tone_delta": tone_delta,
                    "lexicon_direction_ok": lexicon_direction_ok,
                    "wrapper_flag": bool(WRAPPER_RE.search(str(generated_text))),
                }
            )
        details.append(record)

    detail_df = pd.DataFrame(details)
    completed = detail_df[detail_df["status"] == "completed"] if not detail_df.empty else detail_df
    generated = detail_df[detail_df["has_generated_text"] == True] if not detail_df.empty else detail_df

    def count_true(column: str) -> int:
        if column not in detail_df:
            return 0
        return int(detail_df[column].fillna(False).astype(bool).sum())

    summary: dict[str, Any] = {
        "results": str(results_jsonl),
        "event_records_retained": event_count,
        "planned_tasks": len(expected),
        "acceptance_passed": all(row.get("numeric_ok_recomputed") is True for row in details),
        "records": int(len(detail_df)),
        "status_counts": status_counts,
        "completed": int(len(completed)),
        "with_generated_text": int(len(generated)),
        "numeric_ok_recomputed": count_true("numeric_ok_recomputed"),
        "paragraph_count_ok": count_true("paragraph_count_ok"),
        "table_format_ok": count_true("table_format_ok"),
        "wrapper_flags": count_true("wrapper_flag"),
        "lexicon_direction_ok": count_true("lexicon_direction_ok"),
    }
    if not generated.empty and "length_ratio" in generated:
        summary["length_ratio"] = {
            "min": float(generated["length_ratio"].min()),
            "mean": float(generated["length_ratio"].mean()),
            "max": float(generated["length_ratio"].max()),
        }
    if not generated.empty and "tone_delta" in generated:
        summary["tone_delta_by_stance"] = {
            stance: float(group["tone_delta"].mean())
            for stance, group in generated.groupby("stance", dropna=True)
            if len(group)
        }

    if details_csv:
        details_path = Path(details_csv)
        details_path.parent.mkdir(parents=True, exist_ok=True)
        detail_df.to_csv(details_path, index=False)
        summary["details_csv"] = str(details_path)

    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["output_json"] = str(output_path)

    return summary
