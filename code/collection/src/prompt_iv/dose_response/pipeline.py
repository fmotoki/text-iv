from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import random
import re

import numpy as np
import pandas as pd

from prompt_iv.econometrics import add_constant, ols, tsls_just_identified
from prompt_iv.io.jsonl import append_jsonl, read_jsonl, write_jsonl
from prompt_iv.llm.runner import LLMRequest, LLMRunner
from prompt_iv.tone import SentimentAnalyzer


SEMANTIC_PROMPT_VERSION = "semantic_tone_v0.1"
PAIRWISE_PROMPT_VERSION = "pairwise_tone_v0.1"


TEXT_COLUMNS = {
    "original": "original_text",
    "hawk": "hawk_text",
    "dove": "dove_text",
}


SEMANTIC_SYSTEM_PROMPT = """Voce e um classificador cego de tom em comunicacao de banco central.
Voce nao sabe se o texto e original ou contrafactual.
Classifique apenas o tom percebido do texto, considerando contexto e redacao institucional.
Nao conte palavras isoladas mecanicamente; avalie o sentido do texto como comunicacao monetaria.

Retorne somente JSON valido, sem markdown:
{
  "stance_score": number,  // -1 = muito dovish, 0 = neutro, 1 = muito hawkish
  "label": "dovish" | "neutral" | "hawkish",
  "confidence": number,   // entre 0 e 1
  "rationale_short": string
}
"""


PAIRWISE_SYSTEM_PROMPT = """Voce e um classificador cego de tom relativo em comunicacao de banco central.
Voce recebera dois textos anonimizados, A e B.
Indique qual texto comunica uma postura monetaria mais hawkish, considerando contexto e redacao institucional.
Nao use o tamanho do texto ou a mera contagem de palavras como criterio principal.

Retorne somente JSON valido, sem markdown:
{
  "more_hawkish": "A" | "B" | "tie",
  "confidence": number,   // entre 0 e 1
  "rationale_short": string
}
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _clean_label(value: Any) -> str:
    label = str(value or "").strip().lower()
    if label not in {"dovish", "neutral", "hawkish"}:
        raise ValueError(f"Invalid semantic label: {value!r}")
    return label


def _clean_pairwise(value: Any) -> str:
    answer = str(value or "").strip().upper()
    if answer == "TIE":
        return "tie"
    if answer not in {"A", "B"}:
        raise ValueError(f"Invalid pairwise answer: {value!r}")
    return answer


def _clean_score(value: Any, lower: float, upper: float, name: str) -> float:
    number = float(value)
    if number < lower or number > upper:
        raise ValueError(f"{name}={number} outside [{lower}, {upper}]")
    return number


def build_semantic_manifest(corpus_csv: str | Path, output_jsonl: str | Path, seed: int = 123) -> dict[str, Any]:
    corpus = pd.read_csv(corpus_csv)
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        for role, column in TEXT_COLUMNS.items():
            text = str(row[column])
            text_hash = _sha256_text(text)
            records.append(
                {
                    "task_id": f"semantic-{int(row['meeting_number']):03d}-{int(row['iteration']):02d}-{role}-{text_hash[:10]}",
                    "anonymous_id": f"TXT-{text_hash[:12]}",
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "text_role": role,
                    "source_path": row.get("source_path"),
                    "text_hash": text_hash,
                    "prompt_version": SEMANTIC_PROMPT_VERSION,
                    "text": text,
                }
            )
    rng = random.Random(seed)
    rng.shuffle(records)
    written = write_jsonl(output_jsonl, records)
    return {"records": len(records), "records_written": written, "output": str(output_jsonl), "seed": seed}


def _semantic_request(record: dict[str, Any], model: str, reasoning_effort: str, temperature: float) -> LLMRequest:
    user_prompt = (
        f"ID: {record['anonymous_id']}\n\n"
        f"Texto:\n{record['text']}\n\n"
        "Classifique o tom conforme o schema JSON solicitado."
    )
    return LLMRequest(
        system_prompt=SEMANTIC_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        metadata={
            "task": "semantic_tone_classification",
            "task_id": record["task_id"],
            "anonymous_id": record["anonymous_id"],
            "prompt_version": SEMANTIC_PROMPT_VERSION,
            "text_hash": record["text_hash"],
        },
    )


def execute_semantic_manifest(
    manifest_path: str | Path,
    output_jsonl: str | Path,
    runner: LLMRunner,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_path)
    existing = read_jsonl(output_jsonl) if resume else []
    done_statuses = {"completed"} | ({"dry_run"} if runner.name == "dry-run" else set())
    done_ids = {str(row.get("task_id")) for row in existing if row.get("status") in done_statuses}
    pending = [task for task in tasks if str(task.get("task_id")) not in done_ids]
    if limit is not None:
        pending = pending[:limit]

    records: list[dict[str, Any]] = []
    failed = 0
    dry_run = 0
    for task in pending:
        try:
            request = _semantic_request(task, model, reasoning_effort, temperature)
            response = runner.generate(request)
            base = {
                "task_id": task["task_id"],
                "created_at": _now(),
                "meeting_number": task["meeting_number"],
                "iteration": task["iteration"],
                "text_role": task["text_role"],
                "anonymous_id": task["anonymous_id"],
                "text_hash": task["text_hash"],
                "prompt_version": SEMANTIC_PROMPT_VERSION,
                "runner": response.runner,
                "runner_dry_run": response.dry_run,
                "model": response.model,
                "reasoning_effort": reasoning_effort,
                "temperature": temperature,
                "request_input_hash": response.input_hash,
                "response_metadata": response.metadata,
            }
            if response.dry_run:
                dry_run += 1
                records.append({**base, "status": "dry_run"})
                continue
            returncode = (response.metadata or {}).get("returncode")
            if returncode not in (None, 0):
                raise RuntimeError(f"LLM runner returned nonzero exit code {returncode}: {response.text[:300]}")
            parsed = _extract_json_object(response.text)
            records.append(
                {
                    **base,
                    "status": "completed",
                    "stance_score": _clean_score(parsed.get("stance_score"), -1.0, 1.0, "stance_score"),
                    "label": _clean_label(parsed.get("label")),
                    "confidence": _clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence"),
                    "rationale_short": str(parsed.get("rationale_short", ""))[:500],
                    "raw_response_hash": _sha256_text(response.text),
                }
            )
        except Exception as exc:
            failed += 1
            records.append(
                {
                    "task_id": task.get("task_id"),
                    "created_at": _now(),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    written = append_jsonl(output_jsonl, records)
    return {
        "manifest": str(manifest_path),
        "output": str(output_jsonl),
        "runner": runner.name,
        "tasks_total": len(tasks),
        "skipped_existing": len(done_ids) if resume else 0,
        "attempted": len(pending),
        "records_written": written,
        "dry_run": dry_run,
        "failed": failed,
        "resume": resume,
    }


def build_pairwise_manifest(corpus_csv: str | Path, output_jsonl: str | Path, seed: int = 456) -> dict[str, Any]:
    corpus = pd.read_csv(corpus_csv)
    rng = random.Random(seed)
    pair_specs = [("original", "hawk"), ("original", "dove"), ("hawk", "dove")]
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        texts = {role: str(row[column]) for role, column in TEXT_COLUMNS.items()}
        for left_role, right_role in pair_specs:
            ordered = [(left_role, texts[left_role]), (right_role, texts[right_role])]
            rng.shuffle(ordered)
            pair_hash = _sha256_text(texts[left_role] + "\n---PAIR---\n" + texts[right_role])
            records.append(
                {
                    "task_id": (
                        f"pairwise-{int(row['meeting_number']):03d}-{int(row['iteration']):02d}"
                        f"-{left_role}-vs-{right_role}-{pair_hash[:10]}"
                    ),
                    "anonymous_pair_id": f"PAIR-{pair_hash[:12]}",
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "pair_type": f"{left_role}_vs_{right_role}",
                    "role_a": ordered[0][0],
                    "role_b": ordered[1][0],
                    "text_a_hash": _sha256_text(ordered[0][1]),
                    "text_b_hash": _sha256_text(ordered[1][1]),
                    "prompt_version": PAIRWISE_PROMPT_VERSION,
                    "text_a": ordered[0][1],
                    "text_b": ordered[1][1],
                }
            )
    rng.shuffle(records)
    written = write_jsonl(output_jsonl, records)
    return {"records": len(records), "records_written": written, "output": str(output_jsonl), "seed": seed}


def _pairwise_request(record: dict[str, Any], model: str, reasoning_effort: str, temperature: float) -> LLMRequest:
    user_prompt = (
        f"ID: {record['anonymous_pair_id']}\n\n"
        f"Texto A:\n{record['text_a']}\n\n"
        f"Texto B:\n{record['text_b']}\n\n"
        "Indique qual texto e mais hawkish conforme o schema JSON solicitado."
    )
    return LLMRequest(
        system_prompt=PAIRWISE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        metadata={
            "task": "pairwise_tone_classification",
            "task_id": record["task_id"],
            "anonymous_pair_id": record["anonymous_pair_id"],
            "prompt_version": PAIRWISE_PROMPT_VERSION,
        },
    )


def execute_pairwise_manifest(
    manifest_path: str | Path,
    output_jsonl: str | Path,
    runner: LLMRunner,
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_path)
    existing = read_jsonl(output_jsonl) if resume else []
    done_statuses = {"completed"} | ({"dry_run"} if runner.name == "dry-run" else set())
    done_ids = {str(row.get("task_id")) for row in existing if row.get("status") in done_statuses}
    pending = [task for task in tasks if str(task.get("task_id")) not in done_ids]
    if limit is not None:
        pending = pending[:limit]

    records: list[dict[str, Any]] = []
    failed = 0
    dry_run = 0
    for task in pending:
        try:
            request = _pairwise_request(task, model, reasoning_effort, temperature)
            response = runner.generate(request)
            base = {
                "task_id": task["task_id"],
                "created_at": _now(),
                "meeting_number": task["meeting_number"],
                "iteration": task["iteration"],
                "pair_type": task["pair_type"],
                "role_a": task["role_a"],
                "role_b": task["role_b"],
                "anonymous_pair_id": task["anonymous_pair_id"],
                "text_a_hash": task["text_a_hash"],
                "text_b_hash": task["text_b_hash"],
                "prompt_version": PAIRWISE_PROMPT_VERSION,
                "runner": response.runner,
                "runner_dry_run": response.dry_run,
                "model": response.model,
                "reasoning_effort": reasoning_effort,
                "temperature": temperature,
                "request_input_hash": response.input_hash,
                "response_metadata": response.metadata,
            }
            if response.dry_run:
                dry_run += 1
                records.append({**base, "status": "dry_run"})
                continue
            returncode = (response.metadata or {}).get("returncode")
            if returncode not in (None, 0):
                raise RuntimeError(f"LLM runner returned nonzero exit code {returncode}: {response.text[:300]}")
            parsed = _extract_json_object(response.text)
            answer = _clean_pairwise(parsed.get("more_hawkish"))
            more_hawkish_role = None
            if answer == "A":
                more_hawkish_role = task["role_a"]
            elif answer == "B":
                more_hawkish_role = task["role_b"]
            elif answer == "tie":
                more_hawkish_role = "tie"
            records.append(
                {
                    **base,
                    "status": "completed",
                    "more_hawkish": answer,
                    "more_hawkish_role": more_hawkish_role,
                    "confidence": _clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence"),
                    "rationale_short": str(parsed.get("rationale_short", ""))[:500],
                    "raw_response_hash": _sha256_text(response.text),
                }
            )
        except Exception as exc:
            failed += 1
            records.append(
                {
                    "task_id": task.get("task_id"),
                    "created_at": _now(),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    written = append_jsonl(output_jsonl, records)
    return {
        "manifest": str(manifest_path),
        "output": str(output_jsonl),
        "runner": runner.name,
        "tasks_total": len(tasks),
        "skipped_existing": len(done_ids) if resume else 0,
        "attempted": len(pending),
        "records_written": written,
        "dry_run": dry_run,
        "failed": failed,
        "resume": resume,
    }


def _pairwise_scores(pairwise_rows: list[dict[str, Any]]) -> dict[tuple[int, int, str], dict[str, float]]:
    scores: dict[tuple[int, int, str], dict[str, float]] = {}
    for row in pairwise_rows:
        if row.get("status") != "completed":
            continue
        meeting = int(row["meeting_number"])
        iteration = int(row["iteration"])
        roles = [str(row["role_a"]), str(row["role_b"])]
        for role in roles:
            scores.setdefault((meeting, iteration, role), {"score": 0.0, "comparisons": 0.0})
        winner = row.get("more_hawkish_role")
        if winner == "tie":
            for role in roles:
                scores[(meeting, iteration, role)]["comparisons"] += 1.0
        elif winner in roles:
            loser = roles[1] if winner == roles[0] else roles[0]
            scores[(meeting, iteration, str(winner))]["score"] += 1.0
            scores[(meeting, iteration, str(winner))]["comparisons"] += 1.0
            scores[(meeting, iteration, loser)]["score"] -= 1.0
            scores[(meeting, iteration, loser)]["comparisons"] += 1.0
    return scores


def build_realized_dose_dataset(
    corpus_csv: str | Path,
    semantic_results_jsonl: str | Path,
    pairwise_results_jsonl: str | Path | None,
    output_csv: str | Path,
) -> pd.DataFrame:
    corpus = pd.read_csv(corpus_csv)
    semantic_rows = [row for row in read_jsonl(semantic_results_jsonl) if row.get("status") == "completed"]
    semantic_by_key = {
        (int(row["meeting_number"]), int(row["iteration"]), str(row["text_role"])): row
        for row in semantic_rows
    }
    pairwise_rows = read_jsonl(pairwise_results_jsonl) if pairwise_results_jsonl else []
    pairwise_by_key = _pairwise_scores(pairwise_rows)
    analyzer = SentimentAnalyzer()
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        meeting = int(row["meeting_number"])
        iteration = int(row["iteration"])
        for role, column in TEXT_COLUMNS.items():
            text = str(row[column])
            lex = analyzer.analyze(text)
            semantic = semantic_by_key.get((meeting, iteration, role), {})
            pairwise = pairwise_by_key.get((meeting, iteration, role), {"score": np.nan, "comparisons": 0.0})
            pairwise_score = (
                pairwise["score"] / pairwise["comparisons"] if pairwise.get("comparisons", 0.0) else np.nan
            )
            records.append(
                {
                    "meeting_number": meeting,
                    "iteration": iteration,
                    "text_role": role,
                    "assigned_hawk": 1 if role == "hawk" else 0 if role == "dove" else np.nan,
                    "assigned_dove": 1 if role == "dove" else 0 if role == "hawk" else np.nan,
                    "text_hash": _sha256_text(text),
                    "lexicon_score": lex.tone_score,
                    "lexicon_hawk_count": lex.hawk_count,
                    "lexicon_dove_count": lex.dove_count,
                    "word_count": lex.word_count,
                    "semantic_score": semantic.get("stance_score", np.nan),
                    "semantic_label": semantic.get("label"),
                    "semantic_confidence": semantic.get("confidence", np.nan),
                    "semantic_task_id": semantic.get("task_id"),
                    "pairwise_score": pairwise_score,
                    "pairwise_comparisons": pairwise.get("comparisons", 0.0),
                }
            )
    df = pd.DataFrame(records)
    for measure in ["lexicon_score", "semantic_score", "pairwise_score"]:
        original = df[df["text_role"] == "original"][
            ["meeting_number", "iteration", measure]
        ].rename(columns={measure: f"{measure}_original"})
        df = df.merge(original, on=["meeting_number", "iteration"], how="left")
        df[f"{measure}_relative_to_original"] = df[measure] - df[f"{measure}_original"]
    path = Path(output_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def estimate_dose_response(
    dose_csv: str | Path,
    output_json: str | Path | None = None,
    outcome_column: str | None = None,
    dose_columns: tuple[str, ...] = ("semantic_score", "lexicon_score", "pairwise_score"),
) -> dict[str, Any]:
    df = pd.read_csv(dose_csv)
    arms = df[df["text_role"].isin(["hawk", "dove"])].copy()
    report: dict[str, Any] = {
        "dose_csv": str(dose_csv),
        "n_rows": int(len(df)),
        "n_treated_arms": int(len(arms)),
        "meetings": int(df["meeting_number"].nunique()) if "meeting_number" in df else 0,
        "first_stage": {},
        "outcome": {"requested": outcome_column, "available": bool(outcome_column and outcome_column in df.columns)},
    }
    for column in dose_columns:
        if column not in arms.columns:
            continue
        valid = arms.dropna(subset=[column, "assigned_hawk"])
        if valid.empty:
            continue
        by_arm = valid.groupby("text_role")[column].mean().to_dict()
        hawk_mean = by_arm.get("hawk")
        dove_mean = by_arm.get("dove")
        report["first_stage"][column] = {
            "hawk_mean": None if hawk_mean is None else float(hawk_mean),
            "dove_mean": None if dove_mean is None else float(dove_mean),
            "hawk_minus_dove": None
            if hawk_mean is None or dove_mean is None
            else float(hawk_mean - dove_mean),
            "n": int(len(valid)),
        }

    if outcome_column and outcome_column in arms.columns:
        y_df = arms.dropna(subset=[outcome_column, "assigned_hawk", "meeting_number", "iteration"]).copy()
        if len(y_df) >= 2:
            report["outcome"]["itt_assigned_hawk"] = ols(
                y_df[outcome_column].to_numpy(dtype=float),
                add_constant(y_df, ["assigned_hawk"]),
            )
            report["outcome"]["itt_assigned_hawk"]["beta"] = [
                float(x) for x in report["outcome"]["itt_assigned_hawk"]["beta"]
            ]
            for column in dose_columns:
                if column not in y_df.columns:
                    continue
                valid = y_df.dropna(subset=[column])
                if len(valid) < 2:
                    continue
                ols_report = ols(valid[outcome_column].to_numpy(dtype=float), add_constant(valid, [column]))
                ols_report["beta"] = [float(x) for x in ols_report["beta"]]
                item: dict[str, Any] = {"ols": ols_report}
                groups = valid["meeting_number"].astype(str) + "-" + valid["iteration"].astype(str)
                try:
                    item["iv_assigned_hawk"] = tsls_just_identified(
                        valid[outcome_column],
                        valid[column],
                        valid["assigned_hawk"],
                        groups,
                    )
                except Exception as exc:
                    item["iv_assigned_hawk_error"] = str(exc)
                report["outcome"][column] = item
    else:
        report["outcome"]["skipped_reason"] = "No outcome column supplied or found; first-stage dose diagnostics only."

    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
