from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path
from prompt_iv.econometrics import residualize_by_group
from prompt_iv.io.jsonl import append_jsonl, read_jsonl, write_jsonl
from prompt_iv.llm.runner import CodexCliRunner, DryRunRunner, LLMRequest, LLMRunner
from prompt_iv.tone import SentimentAnalyzer


RUN_ID = None
ROLES = ("original", "dove_high", "dove_low", "neutral", "hawk_low", "hawk_high")
ROLE_COLUMNS = {role: f"{role}_text" for role in ROLES}
DOSE_MAP = {
    "dove_high": -2.0,
    "dove_low": -1.0,
    "neutral": 0.0,
    "hawk_low": 1.0,
    "hawk_high": 2.0,
}
ORDERED_DOSE_ROLES = ("dove_high", "dove_low", "neutral", "hawk_low", "hawk_high")
PAIR_SPECS = (
    ("original", "neutral"),
    ("dove_high", "dove_low"),
    ("dove_low", "neutral"),
    ("neutral", "hawk_low"),
    ("hawk_low", "hawk_high"),
)
USAGE_LIMIT_PATTERNS = ("usage limit", "hit your usage limit")
SEMANTIC_PROMPT_VERSION = "semantic_step3_tone_v0.2"
PAIRWISE_PROMPT_VERSION = "pairwise_step3_tone_v0.1"


SEMANTIC_SYSTEM_PROMPT = """Voce e um classificador cego de tom em comunicacao de banco central.
Voce nao sabe se o texto e original ou contrafactual.
Classifique apenas o tom percebido do texto, considerando contexto e redacao institucional.
Nao conte palavras isoladas mecanicamente; avalie o sentido do texto como comunicacao monetaria.

Retorne somente JSON valido, sem markdown:
{
  "stance_score": number,
  "label": "dovish" | "neutral" | "hawkish",
  "confidence": number,
  "rationale_short": string
}

Restricoes obrigatorias:
- "stance_score" deve estar no intervalo fechado [-1, 1].
- Use -1 para tom muito dovish, 0 para neutro e 1 para muito hawkish.
- Nunca retorne valores menores que -1 ou maiores que 1; se o texto parecer extremo, use exatamente -1 ou 1.
- "confidence" deve estar no intervalo fechado [0, 1].
"""


PAIRWISE_SYSTEM_PROMPT = """Voce e um classificador cego de tom relativo em comunicacao de banco central.
Voce recebera dois textos anonimizados, A e B.
Indique qual texto comunica uma postura monetaria mais hawkish, considerando contexto e redacao institucional.
Nao use o tamanho do texto ou a mera contagem de palavras como criterio principal.

Retorne somente JSON valido, sem markdown:
{
  "more_hawkish": "A" | "B" | "tie",
  "confidence": number,
  "rationale_short": string
}
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def extract_json_object(text: str) -> dict[str, Any]:
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


def clean_label(value: Any) -> str:
    label = str(value or "").strip().lower()
    if label not in {"dovish", "neutral", "hawkish"}:
        raise ValueError(f"Invalid semantic label: {value!r}")
    return label


def clean_pairwise(value: Any) -> str:
    answer = str(value or "").strip().upper()
    if answer == "TIE":
        return "tie"
    if answer not in {"A", "B"}:
        raise ValueError(f"Invalid pairwise answer: {value!r}")
    return answer


def clean_score(value: Any, lower: float, upper: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < lower or number > upper:
        raise ValueError(f"{name}={number} outside [{lower}, {upper}]")
    return number


def clean_stance_score(value: Any) -> tuple[float, float, bool]:
    raw = clean_score(value, -1.0, 1.0, "stance_score")
    return raw, raw, False


# Standard JSON/pandas operations do not enforce this source/scorer cohort contract.
# This guard couples exact source cells, immutable specification and protected paths.
def scoring_context(corpus_csv: Path, output_path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    spec = load_spec(require_frozen=True)
    if spec["prompt_versions"]["semantic"]!=SEMANTIC_PROMPT_VERSION or spec["prompt_versions"]["pairwise"]!=PAIRWISE_PROMPT_VERSION:
        raise ValueError("Dedicated scorer prompt protocol differs from specification")
    if spec.get("pairwise_pairs")!={f"{left}_vs_{right}":[left,right] for left,right in PAIR_SPECS}:
        raise ValueError("Pairwise protocol differs from the shared specification")
    validate_artifact_path(Path(corpus_csv).resolve(), spec)
    validate_artifact_path(Path(output_path).resolve(), spec)
    expected_corpus = Path(spec["paths"]["collections"]) / spec["run_ids"]["dose_text"] / "counterfactual_corpus.csv"
    if Path(corpus_csv).resolve() != expected_corpus.resolve():
        raise ValueError("Scorer source is not the selected dose corpus")
    corpus = pd.read_csv(corpus_csv)
    expected = {(m, i) for m in spec["sample"]["meeting_numbers"] for i in range(1, spec["draws"]["dose"] + 1)}
    keys = list(zip(corpus["meeting_number"].astype(int), corpus["iteration"].astype(int)))
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("Dose scoring requires the exact meeting/draw Cartesian product")
    if "complete" not in corpus or not corpus["complete"].eq(True).all():
        raise ValueError("Dose corpus contains incomplete cells")
    for column in ROLE_COLUMNS.values():
        if column not in corpus or not corpus[column].map(lambda text: isinstance(text, str) and bool(text.strip())).all():
            raise ValueError("Dose scoring source text is missing")
    return spec, corpus


def build_semantic_manifest(corpus_csv: Path, output_jsonl: Path, seed: int) -> dict[str, Any]:
    spec, corpus = scoring_context(corpus_csv, output_jsonl)
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        for role, column in ROLE_COLUMNS.items():
            text = str(row[column])
            text_hash = sha256_text(text)
            records.append(
                {
                    "task_id": f"semantic-step3-{int(row['meeting_number']):03d}-{int(row['iteration']):02d}-{role}-{text_hash[:10]}",
                    "anonymous_id": f"TXT-{text_hash[:12]}",
                    "run_id": spec["run_ids"]["blinded_scoring"],
                    "source_run_id": spec["run_ids"]["dose_text"],
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "text_role": role,
                    "assigned_dose": DOSE_MAP.get(role),
                    "source_path": row.get("source_path"),
                    "text_hash": text_hash,
                    "prompt_version": SEMANTIC_PROMPT_VERSION,
                    "prompt_hash": sha256_text(SEMANTIC_SYSTEM_PROMPT),
                    "text": text,
                }
            )
    rng = random.Random(seed)
    rng.shuffle(records)
    for record in records:
        record["request_input_hash"] = semantic_request(record,spec["model"],spec["reasoning_effort"],1.0).input_hash
    if Path(output_jsonl).exists() and read_jsonl(output_jsonl)!=records:
        raise ValueError("Existing scoring manifest differs from selected preparation")
    written = write_jsonl(output_jsonl, records)
    return {"records": len(records), "records_written": written, "output": str(output_jsonl), "seed": seed}


def build_pairwise_manifest(corpus_csv: Path, output_jsonl: Path, seed: int) -> dict[str, Any]:
    spec, corpus = scoring_context(corpus_csv, output_jsonl)
    rng = random.Random(seed)
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        texts = {role: str(row[column]) for role, column in ROLE_COLUMNS.items()}
        for left_role, right_role in PAIR_SPECS:
            ordered = [(left_role, texts[left_role]), (right_role, texts[right_role])]
            rng.shuffle(ordered)
            pair_hash = sha256_text(texts[left_role] + "\n---PAIR---\n" + texts[right_role])
            records.append(
                {
                    "task_id": (
                        f"pairwise-step3-{int(row['meeting_number']):03d}-{int(row['iteration']):02d}"
                        f"-{left_role}-vs-{right_role}-{pair_hash[:10]}"
                    ),
                    "anonymous_pair_id": f"PAIR-{pair_hash[:12]}",
                    "run_id": spec["run_ids"]["blinded_scoring"],
                    "source_run_id": spec["run_ids"]["dose_text"],
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "pair_type": f"{left_role}_vs_{right_role}",
                    "role_a": ordered[0][0],
                    "role_b": ordered[1][0],
                    "text_a_hash": sha256_text(ordered[0][1]),
                    "text_b_hash": sha256_text(ordered[1][1]),
                    "prompt_version": PAIRWISE_PROMPT_VERSION,
                    "prompt_hash": sha256_text(PAIRWISE_SYSTEM_PROMPT),
                    "text_a": ordered[0][1],
                    "text_b": ordered[1][1],
                }
            )
    rng.shuffle(records)
    for record in records:
        record["request_input_hash"] = pairwise_request(record,spec["model"],spec["reasoning_effort"],1.0).input_hash
    if Path(output_jsonl).exists() and read_jsonl(output_jsonl)!=records:
        raise ValueError("Existing scoring manifest differs from selected preparation")
    written = write_jsonl(output_jsonl, records)
    return {"records": len(records), "records_written": written, "output": str(output_jsonl), "seed": seed}


def semantic_request(record: dict[str, Any], model: str, reasoning_effort: str, temperature: float) -> LLMRequest:
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
            "task": "step3_semantic_tone_classification",
            "task_id": record["task_id"],
            "anonymous_id": record["anonymous_id"],
            "prompt_version": record["prompt_version"],
            "text_hash": record["text_hash"],
        },
    )


def pairwise_request(record: dict[str, Any], model: str, reasoning_effort: str, temperature: float) -> LLMRequest:
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
            "task": "step3_pairwise_tone_classification",
            "task_id": record["task_id"],
            "anonymous_pair_id": record["anonymous_pair_id"],
            "prompt_version": record["prompt_version"],
        },
    )


def done_ids(output_jsonl: Path, tasks: list[dict[str, Any]], request_builder: Any, model: str, reasoning_effort: str, temperature: float, resume: bool = True) -> set[str]:
    # A task ID alone cannot prove cache compatibility. request_builder is the
    # existing request-constructor callback, which supplies the canonical hash.
    spec = load_spec(require_frozen=True)
    validate_artifact_path(output_jsonl.resolve(), spec)
    if model != spec["model"] or reasoning_effort != spec["reasoning_effort"]:
        raise ValueError("Scoring model/effort disagree with the selected specification")
    by_id = {str(task["task_id"]): task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("Duplicate scoring task IDs")
    for task in tasks:
        for field, expected in (("run_id",spec["run_ids"]["blinded_scoring"]),("source_run_id",spec["run_ids"]["dose_text"]),("spec_fingerprint",spec["fingerprint"]),("model",model),("reasoning_effort",reasoning_effort)):
            if task.get(field) != expected:
                raise ValueError(f"Scoring manifest identity mismatch: {field}")
        hashes = (("text", "text_hash"),) if "text" in task else (("text_a", "text_a_hash"),("text_b", "text_b_hash"))
        if any(sha256_text(task[text]) != task[digest] for text,digest in hashes):
            raise ValueError("Scoring manifest text hash mismatch")
        if task.get("request_input_hash")!=request_builder(task,model,reasoning_effort,temperature).input_hash:
            raise ValueError("Prepared scoring request changed")
        expected_prompt = SEMANTIC_SYSTEM_PROMPT if "text" in task else PAIRWISE_SYSTEM_PROMPT
        if task.get("prompt_hash") != sha256_text(expected_prompt):
            raise ValueError("Scoring prompt changed after preparation")
    latest = {}
    for row in read_jsonl(output_jsonl):
        task_id = str(row.get("task_id"))
        if task_id not in by_id:
            raise ValueError("Scoring results contain an unselected task")
        task = by_id[task_id]
        if row.get("status") in {"completed","dry_run"}:
            for field in ("run_id","source_run_id","spec_fingerprint","prompt_version","prompt_hash","model","reasoning_effort","meeting_number","iteration","text_role","pair_type","role_a","role_b","text_hash","text_a_hash","text_b_hash"):
                if field in task and row.get(field) != task[field]:
                    raise ValueError(f"Cached scoring provenance changed: {field}")
            if row.get("request_input_hash") != request_builder(task,model,reasoning_effort,temperature).input_hash:
                raise ValueError("Cached scoring request changed")
        if row.get("status")=="completed":
            if row.get("runner_dry_run") is not False or row.get("response_metadata",{}).get("returncode")!=0 or not re.fullmatch(r"[0-9a-f]{64}",str(row.get("raw_response_hash",""))):
                raise ValueError("Cached score is not a verified real completed response")
        latest[task_id] = row
    if not resume:
        return set()
    return {task_id for task_id,row in latest.items() if row.get("status")=="completed" and row.get("runner_dry_run") is False}


def execute_semantic(
    manifest_jsonl: Path,
    output_jsonl: Path,
    runner: LLMRunner,
    model: str,
    reasoning_effort: str,
    temperature: float,
    limit: int | None,
    resume: bool,
    max_workers: int,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_jsonl)
    completed_ids = done_ids(output_jsonl, tasks, semantic_request, model, reasoning_effort, temperature, resume)
    pending = [task for task in tasks if str(task.get("task_id")) not in completed_ids]
    if limit is not None:
        pending = pending[:limit]
    stats = {"completed": 0, "failed": 0, "dry_run": 0, "usage_limited": 0}

    def run_one(task: dict[str, Any]) -> dict[str, Any]:
        base = {
            "task_id": task["task_id"],
            **{field: task[field] for field in ("run_id","source_run_id","spec_fingerprint","prompt_hash")},
            "created_at": now(),
            "meeting_number": task["meeting_number"],
            "iteration": task["iteration"],
            "text_role": task["text_role"],
            "assigned_dose": task.get("assigned_dose"),
            "anonymous_id": task["anonymous_id"],
            "text_hash": task["text_hash"],
            "prompt_version": task["prompt_version"],
        }
        try:
            request = semantic_request(task, model, reasoning_effort, temperature)
            response = runner.generate(request)
            if response.model != model or response.input_hash != request.input_hash:
                raise ValueError("Returned semantic model/request identity mismatch")
            base.update(
                {
                    "runner": response.runner,
                    "runner_dry_run": response.dry_run,
                    "model": response.model,
                    "reasoning_effort": reasoning_effort,
                    "temperature": temperature,
                    "request_input_hash": response.input_hash,
                    "response_metadata": response.metadata,
                }
            )
            returncode = (response.metadata or {}).get("returncode")
            if response.dry_run:
                return {**base, "status": "dry_run"}
            elif returncode != 0 and any(pattern in response.text.lower() for pattern in USAGE_LIMIT_PATTERNS):
                return {**base, "status": "usage_limited", "error": response.text[:1000]}
            elif returncode != 0:
                raise RuntimeError(f"LLM runner returned nonzero exit code {returncode}: {response.text[:300]}")
            else:
                parsed = extract_json_object(response.text)
                stance_score, raw_stance_score, score_clipped = clean_stance_score(parsed.get("stance_score"))
                label = clean_label(parsed.get("label"))
                confidence = clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence")
                return {
                    **base,
                    "status": "completed",
                    "stance_score": stance_score,
                    "raw_stance_score": raw_stance_score,
                    "stance_score_clipped": score_clipped,
                    "label": label,
                    "confidence": confidence,
                    "rationale_short": str(parsed.get("rationale_short", ""))[:500],
                    "raw_response_hash": sha256_text(response.text),
                }
        except Exception as exc:
            return {**base, "status": "failed", "error_type": type(exc).__name__, "error": str(exc)}

    written = 0
    workers = max(1, int(max_workers))
    if pending:
        if workers == 1:
            iterator = (run_one(task) for task in pending)
            for record in iterator:
                status = str(record.get("status", "unknown"))
                if status in stats:
                    stats[status] += 1
                written += append_jsonl(output_jsonl, [record])
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(run_one, task) for task in pending]
                for future in as_completed(futures):
                    record = future.result()
                    status = str(record.get("status", "unknown"))
                    if status in stats:
                        stats[status] += 1
                    written += append_jsonl(output_jsonl, [record])
    return {
        "manifest": str(manifest_jsonl),
        "output": str(output_jsonl),
        "tasks_total": len(tasks),
        "skipped_existing": len(completed_ids) if resume else 0,
        "attempted": len(pending),
        "records_written": written,
        "max_workers": workers,
        **stats,
    }


def execute_pairwise(
    manifest_jsonl: Path,
    output_jsonl: Path,
    runner: LLMRunner,
    model: str,
    reasoning_effort: str,
    temperature: float,
    limit: int | None,
    resume: bool,
    max_workers: int,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_jsonl)
    completed_ids = done_ids(output_jsonl, tasks, pairwise_request, model, reasoning_effort, temperature, resume)
    pending = [task for task in tasks if str(task.get("task_id")) not in completed_ids]
    if limit is not None:
        pending = pending[:limit]
    stats = {"completed": 0, "failed": 0, "dry_run": 0, "usage_limited": 0}

    def run_one(task: dict[str, Any]) -> dict[str, Any]:
        base = {
            "task_id": task["task_id"],
            **{field: task[field] for field in ("run_id","source_run_id","spec_fingerprint","prompt_hash")},
            "created_at": now(),
            "meeting_number": task["meeting_number"],
            "iteration": task["iteration"],
            "pair_type": task["pair_type"],
            "role_a": task["role_a"],
            "role_b": task["role_b"],
            "anonymous_pair_id": task["anonymous_pair_id"],
            "text_a_hash": task["text_a_hash"],
            "text_b_hash": task["text_b_hash"],
            "prompt_version": task["prompt_version"],
        }
        try:
            request = pairwise_request(task, model, reasoning_effort, temperature)
            response = runner.generate(request)
            if response.model != model or response.input_hash != request.input_hash:
                raise ValueError("Returned pairwise model/request identity mismatch")
            base.update(
                {
                    "runner": response.runner,
                    "runner_dry_run": response.dry_run,
                    "model": response.model,
                    "reasoning_effort": reasoning_effort,
                    "temperature": temperature,
                    "request_input_hash": response.input_hash,
                    "response_metadata": response.metadata,
                }
            )
            returncode = (response.metadata or {}).get("returncode")
            if response.dry_run:
                return {**base, "status": "dry_run"}
            elif returncode != 0 and any(pattern in response.text.lower() for pattern in USAGE_LIMIT_PATTERNS):
                return {**base, "status": "usage_limited", "error": response.text[:1000]}
            elif returncode != 0:
                raise RuntimeError(f"LLM runner returned nonzero exit code {returncode}: {response.text[:300]}")
            else:
                parsed = extract_json_object(response.text)
                answer = clean_pairwise(parsed.get("more_hawkish"))
                confidence = clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence")
                more_hawkish_role = "tie"
                if answer == "A":
                    more_hawkish_role = task["role_a"]
                elif answer == "B":
                    more_hawkish_role = task["role_b"]
                return {
                    **base,
                    "status": "completed",
                    "more_hawkish": answer,
                    "more_hawkish_role": more_hawkish_role,
                    "confidence": confidence,
                    "rationale_short": str(parsed.get("rationale_short", ""))[:500],
                    "raw_response_hash": sha256_text(response.text),
                }
        except Exception as exc:
            return {**base, "status": "failed", "error_type": type(exc).__name__, "error": str(exc)}

    written = 0
    workers = max(1, int(max_workers))
    if pending:
        if workers == 1:
            iterator = (run_one(task) for task in pending)
            for record in iterator:
                status = str(record.get("status", "unknown"))
                if status in stats:
                    stats[status] += 1
                written += append_jsonl(output_jsonl, [record])
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(run_one, task) for task in pending]
                for future in as_completed(futures):
                    record = future.result()
                    status = str(record.get("status", "unknown"))
                    if status in stats:
                        stats[status] += 1
                    written += append_jsonl(output_jsonl, [record])
    return {
        "manifest": str(manifest_jsonl),
        "output": str(output_jsonl),
        "tasks_total": len(tasks),
        "skipped_existing": len(completed_ids) if resume else 0,
        "attempted": len(pending),
        "records_written": written,
        "max_workers": workers,
        **stats,
    }


def pairwise_scores(rows: list[dict[str, Any]]) -> dict[tuple[int, int, str], dict[str, float]]:
    scores: dict[tuple[int, int, str], dict[str, float]] = {}
    for row in rows:
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


def build_realized_dataset(
    corpus_csv: Path,
    semantic_results: Path,
    pairwise_results: Path,
    output_csv: Path,
) -> pd.DataFrame:
    spec, corpus = scoring_context(corpus_csv, output_csv)
    semantic_tasks = read_jsonl(semantic_results.parent / "semantic_tone_manifest.jsonl")
    pairwise_tasks = read_jsonl(pairwise_results.parent / "pairwise_tone_manifest.jsonl")
    expected_semantic = {(int(row.meeting_number),int(row.iteration),role) for row in corpus.itertuples() for role in ROLES}
    expected_pairs = {(int(row.meeting_number),int(row.iteration),f"{left}_vs_{right}") for row in corpus.itertuples() for left,right in PAIR_SPECS}
    semantic_keys = [(int(row["meeting_number"]),int(row["iteration"]),row["text_role"]) for row in semantic_tasks]
    pair_keys = [(int(row["meeting_number"]),int(row["iteration"]),row["pair_type"]) for row in pairwise_tasks]
    if len(set(semantic_keys))!=len(semantic_keys) or set(semantic_keys)!=expected_semantic or len(set(pair_keys))!=len(pair_keys) or set(pair_keys)!=expected_pairs:
        raise ValueError("Scoring manifests do not cover exact semantic/pairwise keys")
    accepted_semantic = done_ids(semantic_results,semantic_tasks,semantic_request,spec["model"],spec["reasoning_effort"],1.0)
    accepted_pairwise = done_ids(pairwise_results,pairwise_tasks,pairwise_request,spec["model"],spec["reasoning_effort"],1.0)
    if len(accepted_semantic)!=len(semantic_tasks) or len(accepted_pairwise)!=len(pairwise_tasks):
        raise ValueError("Realized doses require every selected real completed score")
    latest_semantic = {row["task_id"]:row for row in read_jsonl(semantic_results)}
    latest_pairwise = {row["task_id"]:row for row in read_jsonl(pairwise_results)}
    semantic = {(int(row["meeting_number"]),int(row["iteration"]),str(row["text_role"])):row for row in latest_semantic.values()}
    pairwise_rows = list(latest_pairwise.values())
    source_hashes = {(int(row.meeting_number),int(row.iteration),role):sha256_text(getattr(row,column)) for row in corpus.itertuples() for role,column in ROLE_COLUMNS.items()}
    for key,row in semantic.items():
        if row["text_hash"]!=source_hashes[key] or not math.isfinite(float(row["stance_score"])) or not -1<=float(row["stance_score"])<=1:
            raise ValueError("Semantic score/source hash does not match the selected text")
    for row in pairwise_rows:
        expected_pair=tuple(row["pair_type"].split("_vs_"))
        if {row["role_a"],row["role_b"]}!=set(expected_pair):
            raise ValueError("Pairwise roles differ from the named comparator")
        winner=row["role_a"] if row["more_hawkish"]=="A" else row["role_b"] if row["more_hawkish"]=="B" else "tie" if row["more_hawkish"]=="tie" else None
        if winner!=row["more_hawkish_role"]:
            raise ValueError("Pairwise encoded winner disagrees with its role")
        for arm in ("a","b"):
            key=(int(row["meeting_number"]),int(row["iteration"]),row[f"role_{arm}"])
            if row[f"text_{arm}_hash"]!=source_hashes[key]:
                raise ValueError("Pairwise score/source hash mismatch")
        if row["more_hawkish_role"] not in ("tie",row["role_a"],row["role_b"]):
            raise ValueError("Invalid pairwise winner")
    pairwise_by_key = pairwise_scores(pairwise_rows)
    analyzer = SentimentAnalyzer()
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        meeting = int(row["meeting_number"])
        iteration = int(row["iteration"])
        for role, column in ROLE_COLUMNS.items():
            text = str(row[column])
            lex = analyzer.analyze(text)
            sem = semantic.get((meeting, iteration, role), {})
            pair = pairwise_by_key.get((meeting, iteration, role), {"score": np.nan, "comparisons": 0.0})
            comparisons = float(pair.get("comparisons") or 0.0)
            pair_score = float(pair["score"]) / comparisons if comparisons else np.nan
            records.append(
                {
                    "meeting_number": meeting,
                    "iteration": iteration,
                    "run_id": spec["run_ids"]["dose_text"],
                    "scorer_run_id": spec["run_ids"]["blinded_scoring"],
                    "source_run_id": spec["run_ids"]["dose_text"],
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "status": "completed",
                    "runner_dry_run": False,
                    "source_text_hash": sha256_text(text),
                    "pair_fe": f"{meeting}::{iteration}",
                    "text_role": role,
                    "assigned_dose": DOSE_MAP.get(role, np.nan),
                    "text_hash": sha256_text(text),
                    "lexicon_score": lex.tone_score,
                    "lexicon_hawk_count": lex.hawk_count,
                    "lexicon_dove_count": lex.dove_count,
                    "word_count": lex.word_count,
                    "semantic_score": sem.get("stance_score", np.nan),
                    "semantic_label": sem.get("label"),
                    "semantic_confidence": sem.get("confidence", np.nan),
                    "semantic_task_id": sem.get("task_id"),
                    "pairwise_score": pair_score,
                    "pairwise_comparisons": comparisons,
                }
            )
    df = pd.DataFrame(records)
    for measure in ["lexicon_score", "semantic_score", "pairwise_score"]:
        neutral = df[df["text_role"] == "neutral"][["meeting_number", "iteration", measure]].rename(
            columns={measure: f"{measure}_neutral"}
        )
        original = df[df["text_role"] == "original"][["meeting_number", "iteration", measure]].rename(
            columns={measure: f"{measure}_original"}
        )
        df = df.merge(neutral, on=["meeting_number", "iteration"], how="left")
        df = df.merge(original, on=["meeting_number", "iteration"], how="left")
        df[f"{measure}_relative_to_neutral"] = df[measure] - df[f"{measure}_neutral"]
        df[f"{measure}_relative_to_original"] = df[measure] - df[f"{measure}_original"]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    return df


def fe_slope(y: pd.Series, x: pd.Series, groups: pd.Series) -> dict[str, float]:
    valid = pd.DataFrame({"y": y, "x": x, "g": groups}).dropna()
    y_res = residualize_by_group(valid["y"], valid["g"]).to_numpy(dtype=float)
    x_res = residualize_by_group(valid["x"], valid["g"]).to_numpy(dtype=float)
    denom = float(np.dot(x_res, x_res))
    n = int(len(valid))
    fe_groups = int(valid["g"].nunique())
    if abs(denom) < 1e-12 or n == 0:
        return {"coef": math.nan, "se": math.nan, "t": math.nan, "f": math.nan, "n": n, "fe_groups": fe_groups}
    coef = float(np.dot(x_res, y_res) / denom)
    residuals = y_res - coef * x_res
    df_resid = max(n - fe_groups - 1, 1)
    sigma2 = float(np.dot(residuals, residuals)) / df_resid
    se = math.sqrt(sigma2 / denom)
    t_stat = coef / se if se else math.nan
    return {"coef": coef, "se": se, "t": t_stat, "f": t_stat * t_stat if not math.isnan(t_stat) else math.nan, "n": n, "fe_groups": fe_groups}


def estimate_realized_dose(dose_csv: Path, output_json: Path) -> dict[str, Any]:
    df = pd.read_csv(dose_csv)
    treated = df[df["text_role"].isin(ORDERED_DOSE_ROLES)].copy()
    report: dict[str, Any] = {
        "dose_csv": str(dose_csv),
        "n_rows": int(len(df)),
        "n_treated_rows": int(len(treated)),
        "n_cells": int(treated[["meeting_number", "iteration"]].drop_duplicates().shape[0]),
        "by_role": {},
        "first_stage": {},
    }
    for measure in ["lexicon_score", "semantic_score", "pairwise_score"]:
        if measure not in treated.columns:
            continue
        valid = treated.dropna(subset=[measure, "assigned_dose"])
        if valid.empty:
            continue
        by_role = (
            valid.groupby(["text_role", "assigned_dose"], as_index=False)
            .agg(n=(measure, "count"), mean=(measure, "mean"), sd=(measure, "std"))
            .sort_values("assigned_dose")
        )
        role_means = dict(zip(by_role["text_role"], by_role["mean"]))
        adjacent = {}
        for left, right in [("dove_high", "dove_low"), ("dove_low", "neutral"), ("neutral", "hawk_low"), ("hawk_low", "hawk_high")]:
            if left in role_means and right in role_means:
                adjacent[f"{right}_minus_{left}"] = float(role_means[right] - role_means[left])
        pivot = valid.pivot_table(index=["meeting_number", "iteration"], columns="text_role", values=measure)
        monotone = []
        for _, row in pivot.dropna(subset=list(ORDERED_DOSE_ROLES)).iterrows():
            values = [float(row[role]) for role in ORDERED_DOSE_ROLES]
            monotone.append(all(values[index] <= values[index + 1] for index in range(len(values) - 1)))
        report["by_role"][measure] = by_role.to_dict(orient="records")
        report["first_stage"][measure] = {
            **fe_slope(valid[measure], valid["assigned_dose"], valid["pair_fe"]),
            "adjacent_mean_differences": adjacent,
            "monotone_cell_share": float(np.mean(monotone)) if monotone else math.nan,
        }
    write_json(output_json, report)
    return report


def count_status(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in read_jsonl(path):
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_runner(args: argparse.Namespace) -> LLMRunner:
    if args.runner == "codex":
        return CodexCliRunner(
            allow_real_llm=args.allow_real_llm,
            log_path=args.log_path,
            timeout_seconds=args.timeout_seconds,
        )
    return DryRunRunner(log_path=args.log_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run blinded semantic/pairwise validation for step-3 dose-response.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    parser.add_argument("--allow-real-llm", action="store_true")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--stage", choices=["prepare", "semantic", "pairwise", "dataset", "all"], default="all")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--semantic-seed", type=int, default=123)
    parser.add_argument("--pairwise-seed", type=int, default=456)
    args = parser.parse_args()
    args.root = args.root.resolve()
    spec = load_spec(source_root=args.root,require_frozen=True)
    if args.run_id!=spec["run_ids"]["blinded_scoring"] or args.source_run_id!=spec["run_ids"]["dose_text"]:
        raise ValueError("Select exactly the specification scorer/source run IDs")
    if args.model!=spec["model"] or args.reasoning_effort!=spec["reasoning_effort"] or args.temperature!=1.0:
        raise ValueError("Scoring configuration differs from the selected design")
    run_dir = validate_artifact_path(Path(spec["paths"]["collections"]) / args.source_run_id,spec)
    corpus = run_dir / "counterfactual_corpus.csv"
    validation_dir = validate_artifact_path(Path(spec["run_root"]) / "data/interim/dose_response" / args.run_id,spec)
    outputs_dir = validate_artifact_path(Path(spec["paths"]["tables"]),spec)
    args.log_path = validate_artifact_path(Path(spec["paths"]["logs"]) / "step3_blinded_validation.jsonl",spec)
    validation_dir.mkdir(parents=True,exist_ok=True)

    semantic_manifest = validation_dir / "semantic_tone_manifest.jsonl"
    semantic_results = validation_dir / "semantic_tone_results.jsonl"
    pairwise_manifest = validation_dir / "pairwise_tone_manifest.jsonl"
    pairwise_results = validation_dir / "pairwise_tone_results.jsonl"
    dose_csv = validation_dir / "realized_dose_dataset.csv"
    dose_report = outputs_dir / f"{args.run_id}_realized_dose_report.json"

    result: dict[str, Any] = {"run_id": args.run_id, "validation_dir": str(validation_dir)}
    if args.stage in {"prepare", "all"}:
        result["semantic_manifest"] = build_semantic_manifest(corpus, semantic_manifest, seed=args.semantic_seed)
        result["pairwise_manifest"] = build_pairwise_manifest(corpus, pairwise_manifest, seed=args.pairwise_seed)
    if args.stage in {"semantic", "all"}:
        runner = build_runner(args)
        result["semantic_execution"] = execute_semantic(
            semantic_manifest,
            semantic_results,
            runner,
            args.model,
            args.reasoning_effort,
            args.temperature,
            args.limit,
            resume=not args.no_resume,
            max_workers=args.max_workers,
        )
    if args.stage in {"pairwise", "all"}:
        runner = build_runner(args)
        result["pairwise_execution"] = execute_pairwise(
            pairwise_manifest,
            pairwise_results,
            runner,
            args.model,
            args.reasoning_effort,
            args.temperature,
            args.limit,
            resume=not args.no_resume,
            max_workers=args.max_workers,
        )
    if args.stage in {"dataset", "all"}:
        if semantic_results.exists() and pairwise_results.exists():
            dose = build_realized_dataset(corpus, semantic_results, pairwise_results, dose_csv)
            result["realized_dose_dataset"] = {"rows": int(len(dose)), "output": str(dose_csv)}
            result["realized_dose_report"] = {"status":"deferred_to_analysis","output":str(dose_report)}
        else:
            raise ValueError("Semantic or pairwise results are missing")
    result["status_counts"] = {
        "semantic": count_status(semantic_results) if semantic_results.exists() else {},
        "pairwise": count_status(pairwise_results) if pairwise_results.exists() else {},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
