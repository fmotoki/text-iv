from __future__ import annotations

from pathlib import Path
import hashlib

import pandas as pd

from prompt_iv.io.jsonl import read_jsonl
from prompt_iv.io.minutes import read_text
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path


def _complete_stance_text(rows: list[dict], total_blocks: int) -> str | None:
    by_block: dict[int, str] = {}
    for row in rows:
        block_index = int(row["block_index"])
        text = row.get("generated_text")
        if not text:
            return None
        by_block[block_index] = str(text)
    if sorted(by_block) != list(range(1, total_blocks + 1)):
        return None
    return "\n\n".join(by_block[index] for index in range(1, total_blocks + 1))


def assemble_counterfactual_corpus(
    results_jsonl: str | Path,
    output_csv: str | Path | None = None,
    required_stances: tuple[str, ...] = ("hawk", "dove"),
    require_complete: bool = True,
    manifest_path: str | Path | None = None,
) -> pd.DataFrame:
    spec = load_spec(require_frozen=True)
    validate_artifact_path(results_jsonl, spec)
    if output_csv:
        output_csv = validate_artifact_path(output_csv, spec)
    latest = {str(row["task_id"]): row for row in read_jsonl(results_jsonl) if row.get("task_id")}
    if manifest_path is None:
        raise ValueError("Assembly requires the exact planned rewrite manifest")
    planned = read_jsonl(validate_artifact_path(manifest_path, spec))
    if len({row["task_id"] for row in planned}) != len(planned) or set(latest) != {row["task_id"] for row in planned}:
        raise ValueError("Assembly requires the exact latest planned task keys")
    for task in planned:
        row = latest[task["task_id"]]
        if row.get("status") != "completed" or row.get("runner_dry_run") or row.get("numeric_ok") is not True or not row.get("generated_text"):
            raise ValueError(f"Incomplete or invalid latest rewrite task: {task['task_id']}")
        if row.get("manifest_input_hash") != task["input_hash"] or row.get("model") != spec["model"] or row.get("reasoning_effort") != spec["reasoning_effort"] or any(row.get(key) != value for key, value in specification_metadata(spec).items()):
            raise ValueError("Assembly source/model/specification mismatch")
        if hashlib.sha256(str(row["generated_text"]).encode("utf-8")).hexdigest() != row.get("output_text_hash"):
            raise ValueError("Generated text hash changed before assembly")
    rows = list(latest.values())

    groups: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        key = (int(row["meeting_number"]), int(row["iteration"]))
        groups.setdefault(key, []).append(row)

    records: list[dict] = []
    for (meeting_number, iteration), group_rows in sorted(groups.items()):
        source_paths = sorted({str(row["source_path"]) for row in group_rows})
        if len(source_paths) != 1:
            if require_complete:
                continue
            source_path = source_paths[0] if source_paths else ""
        else:
            source_path = source_paths[0]

        total_blocks = max(int(row["total_blocks"]) for row in group_rows)
        record = {
            "meeting_number": meeting_number,
            "iteration": iteration,
            "source_path": source_path,
            "original_text": read_text(source_path) if source_path else "",
        }

        complete = True
        for stance in sorted({str(row["stance"]) for row in group_rows} | set(required_stances)):
            stance_rows = [row for row in group_rows if str(row["stance"]) == stance]
            text = _complete_stance_text(stance_rows, total_blocks)
            if text is None:
                complete = False
                if require_complete:
                    break
            record[f"{stance}_text"] = text
        if require_complete and (not complete or any(record.get(f"{stance}_text") is None for stance in required_stances)):
            continue
        record["complete"] = complete
        records.append(record)

    df = pd.DataFrame(records)
    if require_complete and len(df) != len({(int(row["meeting_number"]), int(row["iteration"])) for row in planned}):
        raise ValueError("Assembly omitted an intended meeting/draw cell")
    if output_csv:
        path = Path(output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df
