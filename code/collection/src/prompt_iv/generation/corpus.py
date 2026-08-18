from __future__ import annotations

from pathlib import Path

import pandas as pd

from prompt_iv.io.jsonl import read_jsonl
from prompt_iv.io.minutes import read_text


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
) -> pd.DataFrame:
    rows = [
        row
        for row in read_jsonl(results_jsonl)
        if row.get("status") == "completed" and row.get("numeric_ok") is True
    ]

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
    if output_csv:
        path = Path(output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    return df
