from __future__ import annotations

import argparse
import hashlib
import csv
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path
from prompt_iv.storage.sqlite import connect_database
from prompt_iv.econometrics import residualize_by_group
from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.io.minutes import read_text, segment_text
from prompt_iv.pipelines.tone import score_text_columns
from prompt_iv.tone import SentimentAnalyzer


RUN_ID = None
REQUIRED_STANCES = ("neutral", "dove_low", "dove_high", "hawk_low", "hawk_high")
TEXT_COLUMNS = (
    "original_text",
    "neutral_text",
    "dove_low_text",
    "dove_high_text",
    "hawk_low_text",
    "hawk_high_text",
)
DOSE_MAP = {
    "dove_high": -2.0,
    "dove_low": -1.0,
    "neutral": 0.0,
    "hawk_low": 1.0,
    "hawk_high": 2.0,
}

WRAPPER_RE = re.compile(
    r"(^\s*(aqui|segue|claro|vers[aÃ£]o)\b)|(```)|(\btexto reescrito\b)",
    flags=re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or "", flags=re.UNICODE))


def paragraph_count(text: str) -> int:
    return len([line for line in str(text or "").splitlines() if line.strip()])


def table_lines(text: str) -> list[str]:
    return [
        line.rstrip()
        for line in str(text or "").splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]


def bool_from_db(value: Any) -> bool:
    return bool(int(value or 0))


def load_run_status(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT rewrite_tasks_total, rewrite_completed_latest, rewrite_result_lines
        FROM runs
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Run {run_id!r} not found in SQLite database.")
    total = int(row["rewrite_tasks_total"])
    completed = int(row["rewrite_completed_latest"])
    by_status = [
        dict(item)
        for item in conn.execute(
            """
            SELECT stance, status, COUNT(*) AS n
            FROM rewrite_results_latest
            WHERE run_id = ?
            GROUP BY stance, status
            ORDER BY stance, status
            """,
            (run_id,),
        )
    ]
    return {
        "run_id": run_id,
        "rewrite_tasks_total": total,
        "rewrite_completed_latest": completed,
        "remaining": total - completed,
        "pct_completed": completed / total * 100.0 if total else math.nan,
        "rewrite_result_lines": int(row["rewrite_result_lines"]),
        "by_status": by_status,
    }


def load_latest_rows(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in conn.execute(
        """
        SELECT
            task_id, event_line_number, status, created_at, meeting_number,
            source_path, iteration, block_index, total_blocks, stance,
            prompt_version, model, reasoning_effort, temperature,
            manifest_input_hash, request_input_hash, output_text_hash,
            numeric_ok, generated_text, retry_attempts_used, raw_json
        FROM rewrite_results_latest
        WHERE run_id = ?
        ORDER BY meeting_number, iteration, block_index, stance
        """,
        (run_id,),
    ):
        record = dict(row)
        record["numeric_ok"] = bool_from_db(record["numeric_ok"])
        rows.append(record)
    return rows


def original_block(row: dict[str, Any]) -> str:
    full_text = read_text(row["source_path"])
    blocks = segment_text(full_text, parts=int(row["total_blocks"]))
    block_index = int(row["block_index"]) - 1
    if block_index < 0 or block_index >= len(blocks):
        raise ValueError(f"block_index out of range for task {row.get('task_id')!r}")
    return blocks[block_index]


def evaluate_rows(rows: list[dict[str, Any]], output_json: Path, details_csv: Path) -> dict[str, Any]:
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
            "model": row.get("model"),
            "numeric_ok_recorded": row.get("numeric_ok"),
            "has_generated_text": bool(generated_text),
            "retry_attempts_used": row.get("retry_attempts_used"),
        }
        if generated_text:
            source = original_block(row)
            numeric_ok, missing, extra = check_numeric_multiset(source, str(generated_text))
            source_words = word_count(source)
            generated_words = word_count(str(generated_text))
            source_table_lines = table_lines(source)
            generated_table_lines = table_lines(str(generated_text))
            source_tone = analyzer.analyze(source)
            generated_tone = analyzer.analyze(str(generated_text))
            record.update(
                {
                    "numeric_ok_recomputed": numeric_ok,
                    "numeric_missing_count": len(missing),
                    "numeric_extra_count": len(extra),
                    "original_words": source_words,
                    "generated_words": generated_words,
                    "length_ratio": generated_words / source_words if source_words else None,
                    "original_paragraphs": paragraph_count(source),
                    "generated_paragraphs": paragraph_count(str(generated_text)),
                    "paragraph_count_ok": paragraph_count(source) == paragraph_count(str(generated_text)),
                    "original_table_lines": len(source_table_lines),
                    "generated_table_lines": len(generated_table_lines),
                    "table_format_ok": source_table_lines == generated_table_lines if source_table_lines else True,
                    "original_tone_score": source_tone.tone_score,
                    "generated_tone_score": generated_tone.tone_score,
                    "tone_delta": generated_tone.tone_score - source_tone.tone_score,
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
        "source": "sqlite:rewrite_results_latest",
        "records": int(len(detail_df)),
        "status_counts": status_counts,
        "completed": int(len(completed)),
        "with_generated_text": int(len(generated)),
        "numeric_ok_recomputed": count_true("numeric_ok_recomputed"),
        "paragraph_count_ok": count_true("paragraph_count_ok"),
        "table_format_ok": count_true("table_format_ok"),
        "wrapper_flags": count_true("wrapper_flag"),
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

    details_csv.parent.mkdir(parents=True, exist_ok=True)
    detail_df.to_csv(details_csv, index=False)
    summary["details_csv"] = str(details_csv)
    summary["output_json"] = str(output_json)
    write_json(output_json, summary)
    return summary


def complete_stance_text(rows: list[dict[str, Any]], total_blocks: int) -> str | None:
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


def assemble_corpus(rows: list[dict[str, Any]], output_csv: Path) -> pd.DataFrame:
    completed = [
        row
        for row in rows
        if row.get("status") == "completed" and row.get("numeric_ok") is True
    ]
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in completed:
        key = (int(row["meeting_number"]), int(row["iteration"]))
        groups.setdefault(key, []).append(row)

    records: list[dict[str, Any]] = []
    for (meeting_number, iteration), group_rows in sorted(groups.items()):
        source_paths = sorted({str(row["source_path"]) for row in group_rows})
        if len(source_paths) != 1:
            continue
        total_blocks = max(int(row["total_blocks"]) for row in group_rows)
        record = {
            "meeting_number": meeting_number,
            "iteration": iteration,
            "source_path": source_paths[0],
            "original_text": read_text(source_paths[0]),
        }
        complete = True
        for stance in REQUIRED_STANCES:
            stance_rows = [row for row in group_rows if str(row["stance"]) == stance]
            text = complete_stance_text(stance_rows, total_blocks)
            if text is None:
                complete = False
                break
            record[f"{stance}_text"] = text
        if complete:
            record["complete"] = True
            records.append(record)

    df = pd.DataFrame(records)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    return df


def fe_slope_stats(y: pd.Series, x: pd.Series, groups: pd.Series) -> dict[str, float]:
    y_res = residualize_by_group(y, groups).to_numpy(dtype=float)
    x_res = residualize_by_group(x, groups).to_numpy(dtype=float)
    n = int(len(y_res))
    fe_groups = int(pd.Series(groups).nunique())
    denom = float(np.dot(x_res, x_res))
    if abs(denom) < 1e-12:
        return {
            "coef": math.nan,
            "se": math.nan,
            "t": math.nan,
            "f": math.nan,
            "r2_within": math.nan,
            "n": float(n),
            "fe_groups": float(fe_groups),
            "df_resid": math.nan,
        }
    coef = float(np.dot(x_res, y_res) / denom)
    residuals = y_res - coef * x_res
    ssr = float(np.dot(residuals, residuals))
    tss = float(np.dot(y_res - float(np.mean(y_res)), y_res - float(np.mean(y_res))))
    df_resid = max(n - fe_groups - 1, 1)
    sigma2 = ssr / df_resid
    se = math.sqrt(sigma2 / denom)
    t_stat = coef / se if se else math.nan
    return {
        "coef": coef,
        "se": float(se),
        "t": float(t_stat),
        "f": float(t_stat * t_stat) if not math.isnan(t_stat) else math.nan,
        "r2_within": float(1.0 - ssr / tss) if tss else math.nan,
        "n": float(n),
        "fe_groups": float(fe_groups),
        "df_resid": float(df_resid),
    }


def neutral_falsification(scored: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    details = scored[["meeting_number", "iteration", "original_text_score", "neutral_text_score"]].copy()
    details["pair_fe"] = details["meeting_number"].astype(str) + "::" + details["iteration"].astype(str)
    details["original_tone_pp"] = details["original_text_score"] * 100.0
    details["neutral_tone_pp"] = details["neutral_text_score"] * 100.0
    details["neutral_minus_original_pp"] = details["neutral_tone_pp"] - details["original_tone_pp"]
    diff = details["neutral_minus_original_pp"].dropna()
    se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else math.nan
    t_stat = float(diff.mean() / se) if se else math.nan
    summary = {
        "n": int(len(diff)),
        "mean_neutral_minus_original_pp": float(diff.mean()),
        "sd_neutral_minus_original_pp": float(diff.std(ddof=1)),
        "se_mean": se,
        "t_mean_zero": t_stat,
        "f_mean_zero": float(t_stat * t_stat) if not math.isnan(t_stat) else math.nan,
        "mean_abs_neutral_minus_original_pp": float(diff.abs().mean()),
        "p05": float(diff.quantile(0.05)),
        "p50": float(diff.quantile(0.50)),
        "p95": float(diff.quantile(0.95)),
    }
    return details, summary


def build_step3_long(scored: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for _, row in scored.iterrows():
        pair_fe = f"{int(row['meeting_number'])}::{int(row['iteration'])}"
        for role, dose in DOSE_MAP.items():
            score_col = f"{role}_text_score"
            wc_col = f"{role}_text_word_count"
            records.append(
                {
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "pair_fe": pair_fe,
                    "role": role,
                    "dose": dose,
                    "tone_pp": float(row[score_col]) * 100.0,
                    "word_count": float(row[wc_col]),
                }
            )
    return pd.DataFrame(records)


def dose_response(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    long = build_step3_long(scored)
    stats = fe_slope_stats(long["tone_pp"], long["dose"], long["pair_fe"])
    by_role = (
        long.groupby(["role", "dose"], as_index=False)
        .agg(
            n=("tone_pp", "count"),
            mean_tone_pp=("tone_pp", "mean"),
            sd_tone_pp=("tone_pp", "std"),
            mean_word_count=("word_count", "mean"),
        )
        .sort_values("dose")
        .reset_index(drop=True)
    )

    role_means = dict(zip(by_role["role"], by_role["mean_tone_pp"]))
    adjacent = {}
    for left, right in [("dove_high", "dove_low"), ("dove_low", "neutral"), ("neutral", "hawk_low"), ("hawk_low", "hawk_high")]:
        adjacent[f"{right}_minus_{left}"] = float(role_means[right] - role_means[left])

    order = ["dove_high", "dove_low", "neutral", "hawk_low", "hawk_high"]
    monotone_flags = []
    for _, row in long.pivot_table(index=["meeting_number", "iteration"], columns="role", values="tone_pp").reset_index().iterrows():
        values = [float(row[role]) for role in order]
        monotone_flags.append(all(values[index] <= values[index + 1] for index in range(len(values) - 1)))

    summary = {
        "n_rows": int(len(long)),
        "n_cells": int(scored[["meeting_number", "iteration"]].drop_duplicates().shape[0]),
        "dose_slope_pp_per_level": float(stats["coef"]),
        "dose_slope_se": float(stats["se"]),
        "dose_slope_t": float(stats["t"]),
        "dose_slope_f": float(stats["f"]),
        "dose_slope_r2_within": float(stats["r2_within"]),
        "monotone_cell_share": float(np.mean(monotone_flags)) if monotone_flags else math.nan,
        "adjacent_mean_differences_pp": adjacent,
    }
    return long, by_role, summary


def numeric_retry_summary(rows: list[dict[str, Any]]) -> pd.DataFrame:
    details = pd.DataFrame(
        [
            {
                "task_id": row.get("task_id"),
                "stance": row.get("stance"),
                "meeting_number": row.get("meeting_number"),
                "iteration": row.get("iteration"),
                "retry_attempts_used": int(row.get("retry_attempts_used") or 0),
            }
            for row in rows
            if row.get("status") == "completed"
        ]
    )
    if details.empty:
        return pd.DataFrame()
    details["had_numeric_retry"] = details["retry_attempts_used"] > 0
    details["llm_calls"] = 1 + details["retry_attempts_used"]
    summaries = []
    groups: list[tuple[str, str, pd.DataFrame]] = [("overall", "all", details)]
    groups.extend(("stance", str(key), group) for key, group in details.groupby("stance", sort=True))
    for scope, stance, group in groups:
        rejected = int(group["retry_attempts_used"].sum())
        calls = int(group["llm_calls"].sum())
        summaries.append(
            {
                "scope": scope,
                "stance": stance,
                "tasks": int(len(group)),
                "tasks_with_numeric_retry": int(group["had_numeric_retry"].sum()),
                "task_retry_rate": float(group["had_numeric_retry"].mean()),
                "numeric_rejected_attempts": rejected,
                "llm_calls": calls,
                "attempt_rejection_rate": rejected / calls if calls else math.nan,
                "mean_retry_attempts": float(group["retry_attempts_used"].mean()),
            }
        )
    return pd.DataFrame(summaries)


def run_audit(run_id: str, run_dir: Path, rows: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    scored = pd.read_csv(run_dir / "counterfactual_tone_scores.csv", low_memory=False)
    neutral_details, neutral_summary = neutral_falsification(scored)
    dose_long, dose_by_role, dose_summary = dose_response(scored)
    retries = numeric_retry_summary(rows)

    paths = {
        "neutral_details": out_dir / f"{run_id}_neutral_falsification_details.csv",
        "dose_long": out_dir / f"{run_id}_four_level_dose_long.csv",
        "dose_by_role": out_dir / f"{run_id}_four_level_dose_by_role.csv",
        "numeric_retry_summary": out_dir / f"{run_id}_numeric_retry_summary.csv",
        "audit_report": out_dir / f"{run_id}_step3_report.json",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    neutral_details.to_csv(paths["neutral_details"], index=False)
    dose_long.to_csv(paths["dose_long"], index=False)
    dose_by_role.to_csv(paths["dose_by_role"], index=False)
    retries.to_csv(paths["numeric_retry_summary"], index=False)

    status_counts = pd.Series([row.get("status", "unknown") for row in rows]).value_counts().to_dict()
    report = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "status": {
            "source": "sqlite:rewrite_results_latest",
            "unique": int(len(rows)),
            "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        },
        "neutral_falsification": neutral_summary,
        "four_level_dose_response": dose_summary,
        "outputs": {key: str(path) for key, path in paths.items()},
    }
    write_json(paths["audit_report"], report)
    return report


def update_corpus_texts(conn: sqlite3.Connection, run_id: str, corpus: pd.DataFrame) -> int:
    conn.execute("DELETE FROM corpus_texts WHERE run_id = ?", (run_id,))
    rows = 0
    for _, row in corpus.iterrows():
        for column in TEXT_COLUMNS:
            role = column[: -len("_text")]
            text = str(row[column])
            conn.execute(
                """
                INSERT OR REPLACE INTO corpus_texts (
                    run_id, meeting_number, iteration, role, source_path,
                    complete, text, text_hash, char_count, word_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(row["meeting_number"]),
                    int(row["iteration"]),
                    role,
                    row.get("source_path"),
                    1 if bool(row.get("complete")) else 0,
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    len(text),
                    word_count(text),
                ),
            )
            rows += 1
    conn.execute("UPDATE runs SET corpus_rows = ?, ingested_at = ? WHERE run_id = ?", (len(corpus), utc_now(), run_id))
    return rows


def update_tone_scores(conn: sqlite3.Connection, run_id: str, tone: pd.DataFrame) -> int:
    conn.execute("DELETE FROM tone_scores WHERE run_id = ?", (run_id,))
    rows = 0
    for _, row in tone.iterrows():
        for column in TEXT_COLUMNS:
            role = column[: -len("_text")]
            score_col = f"{column}_score"
            if score_col not in tone.columns:
                continue
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
                    int(row["meeting_number"]),
                    int(row["iteration"]),
                    role,
                    row.get("source_path"),
                    1 if bool(row.get("complete")) else 0,
                    float(row[score_col]),
                    int(row[f"{column}_hawk_count"]),
                    int(row[f"{column}_dove_count"]),
                    int(row[f"{column}_word_count"]),
                    json.dumps(
                        {
                            "score": float(row[score_col]),
                            "hawk_count": int(row[f"{column}_hawk_count"]),
                            "dove_count": int(row[f"{column}_dove_count"]),
                            "word_count": int(row[f"{column}_word_count"]),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            rows += 1
    conn.execute("UPDATE runs SET tone_rows = ?, ingested_at = ? WHERE run_id = ?", (len(tone), utc_now(), run_id))
    return rows


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_integration_summary(run_id: str, step3_report: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    d5_report = read_json_if_exists(out_dir / "v10_referee_d5_25draws_20260620_d5_audit_report.json")
    scrambled = read_json_if_exists(out_dir / "v10_referee_d5_25draws_20260620_scrambled_z_summary.json")
    summary = {
        "created_at": utc_now(),
        "main_d5_report": d5_report,
        "scrambled_z_summary": scrambled,
        "step3_report": step3_report,
        "interpretation_checks": {
            "d5_available": d5_report is not None,
            "scrambled_z_available": scrambled is not None,
            "neutral_falsification_n": step3_report["neutral_falsification"]["n"],
            "dose_response_n_cells": step3_report["four_level_dose_response"]["n_cells"],
            "dose_response_monotone_cell_share": step3_report["four_level_dose_response"]["monotone_cell_share"],
            "dose_response_slope_pp_per_level": step3_report["four_level_dose_response"]["dose_slope_pp_per_level"],
        },
    }
    path = out_dir / f"{run_id}_d5_step3_integration_summary.json"
    write_json(path, summary)
    summary["output"] = str(path)
    return summary


def assert_complete(status: dict[str, Any]) -> None:
    if status["remaining"] != 0:
        raise RuntimeError(
            f"Run is not complete: {status['rewrite_completed_latest']}/"
            f"{status['rewrite_tasks_total']} completed, remaining={status['remaining']}."
        )
    bad = [row for row in status["by_status"] if row["status"] != "completed"]
    if bad:
        raise RuntimeError(f"Run has non-completed latest rows: {bad}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Postprocess V10 referee step-3 DB-first collection.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    source_root = args.root.resolve()
    spec = load_spec(source_root=source_root,require_frozen=True)
    if args.run_id!=spec["run_ids"]["dose_text"]:
        raise ValueError("Postprocessing requires the selected dose text run")
    db_path = validate_artifact_path(args.database,spec)
    if db_path != Path(spec["paths"]["database"]):
        raise ValueError("Postprocessing database differs from selected warehouse")
    run_dir = validate_artifact_path(Path(spec["paths"]["collections"]) / args.run_id,spec)
    out_dir = validate_artifact_path(Path(spec["paths"]["tables"]),spec)
    run_dir.mkdir(parents=True,exist_ok=True)

    quality_report_path = run_dir / "counterfactual_quality_report.json"
    quality_details_path = run_dir / "counterfactual_quality_details.csv"
    corpus_path = run_dir / "counterfactual_corpus.csv"
    tone_path = run_dir / "counterfactual_tone_scores.csv"
    status_path = run_dir / "db_postprocess_status.json"

    conn = connect_database(db_path)
    try:
        status = load_run_status(conn, args.run_id)
        assert_complete(status)
        metadata = json.loads(conn.execute("SELECT metadata_json FROM runs WHERE run_id=?",(args.run_id,)).fetchone()[0] or "{}")
        if metadata.get("replication_config",{}).get("fingerprint")!=spec["fingerprint"]:
            raise ValueError("Dose source run specification mismatch")
        rows = load_latest_rows(conn,args.run_id)
        expected_keys={(m,i,block,stance) for m in spec["sample"]["meeting_numbers"] for i in range(1,spec["draws"]["dose"]+1) for block in range(1,spec["blocks"]+1) for stance in REQUIRED_STANCES}
        actual_keys=[(int(row["meeting_number"]),int(row["iteration"]),int(row["block_index"]),row["stance"]) for row in rows]
        if len(actual_keys)!=len(set(actual_keys)) or set(actual_keys)!=expected_keys:
            raise ValueError("Latest rewrite rows do not cover the exact selected Cartesian product")
        tasks={row["task_id"]:dict(row) for row in conn.execute("SELECT task_id,input_hash,original_text_hash FROM rewrite_tasks WHERE run_id=?",(args.run_id,))}
        if set(tasks)!={row["task_id"] for row in rows}:
            raise ValueError("Rewrite task/result identities differ")
        for row in rows:
            raw=json.loads(row["raw_json"])
            task=tasks[row["task_id"]]
            if row["model"]!=spec["model"] or row["reasoning_effort"]!=spec["reasoning_effort"] or raw.get("runner_dry_run") is not False or raw.get("fingerprint")!=spec["fingerprint"]:
                raise ValueError("Rewrite result is not a real completed selected-model result")
            if raw.get("response_metadata",{}).get("returncode",0)!=0:
                raise ValueError("Rewrite runner rejected the result")
            expected_path=source_root / "data/raw/copom_minutes" / f"ata{row['meeting_number']}.txt"
            if Path(row["source_path"]).resolve()!=expected_path.resolve():
                raise ValueError("Rewrite original path differs from selected immutable minute")
            if row["manifest_input_hash"]!=task["input_hash"] or hashlib.sha256(original_block(row).encode("utf-8")).hexdigest()!=task["original_text_hash"]:
                raise ValueError("Rewrite task/source hash mismatch")
            if not row.get("generated_text") or hashlib.sha256(row["generated_text"].encode("utf-8")).hexdigest()!=row["output_text_hash"]:
                raise ValueError("Rewrite output hash mismatch")
        quality = evaluate_rows(rows, quality_report_path, quality_details_path)
        if quality["numeric_ok_recomputed"]!=len(rows):
            raise ValueError("Recomputed numeric-preservation gate failed")
        corpus = assemble_corpus(rows, corpus_path)
        expected_cells={(m,i) for m in spec["sample"]["meeting_numbers"] for i in range(1,spec["draws"]["dose"]+1)}
        cells=list(zip(corpus["meeting_number"].astype(int),corpus["iteration"].astype(int)))
        if len(cells)!=len(set(cells)) or set(cells)!=expected_cells:
            raise ValueError("Assembled corpus is missing selected meeting/draw cells")
        tone = score_text_columns(corpus_path, TEXT_COLUMNS, tone_path, drop_text=True)
        step3_report = {"status":"deferred_to_analysis","fingerprint":spec["fingerprint"]}
        with conn:
            corpus_text_rows = update_corpus_texts(conn, args.run_id, corpus)
            tone_score_rows = update_tone_scores(conn, args.run_id, tone)
        integration = {"status":"deferred_to_analysis"}
        result = {
            "run_id": args.run_id,
            "database": str(db_path),
            "status": status,
            "quality": quality,
            "corpus": {"rows": int(len(corpus)), "output": str(corpus_path)},
            "tone_scores": {"rows": int(len(tone)), "output": str(tone_path)},
            "sqlite_updates": {
                "corpus_text_rows": int(corpus_text_rows),
                "tone_score_rows": int(tone_score_rows),
            },
            "step3_report": step3_report,
            "integration_summary": integration,
        }
        write_json(status_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
