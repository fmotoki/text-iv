from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import math
import random
import re

import numpy as np
import pandas as pd

from prompt_iv.paths import load_spec, validate_artifact_path
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
    if not math.isfinite(number) or number < lower or number > upper:
        raise ValueError(f"{name}={number} outside [{lower}, {upper}]")
    return number


# This alternate API retains its three-role prompt protocol. It cannot populate
# the selected six-arm scorer directory or stand in for six-arm dose evidence.
def _three_role_context(corpus_csv: str | Path, output_path: str | Path) -> tuple[dict, pd.DataFrame]:
    spec=load_spec(require_frozen=True)
    if spec["prompt_versions"]["alternate_three_role_semantic"]!=SEMANTIC_PROMPT_VERSION or spec["prompt_versions"]["alternate_three_role_pairwise"]!=PAIRWISE_PROMPT_VERSION:
        raise ValueError("Alternate scorer protocol differs from specification")
    corpus_path=validate_artifact_path(Path(corpus_csv).resolve(),spec)
    output=validate_artifact_path(Path(output_path).resolve(),spec)
    expected_corpus=Path(spec["paths"]["collections"])/spec["run_ids"]["main_text"]/"counterfactual_corpus.csv"
    six_arm_directory=Path(spec["run_root"])/"data/interim/dose_response"/spec["run_ids"]["blinded_scoring"]
    if corpus_path!=expected_corpus.resolve() or output.is_relative_to(six_arm_directory):
        raise ValueError("Three-role validation must use the main source and a separate explicit destination")
    corpus=pd.read_csv(corpus_path)
    keys=list(zip(corpus.meeting_number.astype(int),corpus.iteration.astype(int)))
    expected={(m,i) for m in spec["sample"]["meeting_numbers"] for i in range(1,spec["draws"]["main"]+1)}
    if len(keys)!=len(set(keys)) or set(keys)!=expected or not corpus["complete"].eq(True).all():
        raise ValueError("Three-role scoring requires exact complete main cells")
    for column in TEXT_COLUMNS.values():
        if column not in corpus or not corpus[column].map(lambda text: isinstance(text,str) and bool(text.strip())).all():
            raise ValueError("Missing three-role source text")
    return spec,corpus


def build_semantic_manifest(corpus_csv: str | Path, output_jsonl: str | Path, seed: int = 123) -> dict[str, Any]:
    spec,corpus = _three_role_context(corpus_csv,output_jsonl)
    records: list[dict[str, Any]] = []
    for _, row in corpus.iterrows():
        for role, column in TEXT_COLUMNS.items():
            text = str(row[column])
            text_hash = _sha256_text(text)
            records.append(
                {
                    "task_id": f"semantic-{int(row['meeting_number']):03d}-{int(row['iteration']):02d}-{role}-{text_hash[:10]}",
                    "anonymous_id": f"TXT-{text_hash[:12]}",
                    "run_id": spec["run_ids"]["blinded_scoring"],
                    "source_run_id": spec["run_ids"]["main_text"],
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "text_role": role,
                    "source_path": row.get("source_path"),
                    "text_hash": text_hash,
                    "prompt_version": SEMANTIC_PROMPT_VERSION,
                    "prompt_hash": _sha256_text(SEMANTIC_SYSTEM_PROMPT),
                    "text": text,
                }
            )
    rng = random.Random(seed)
    rng.shuffle(records)
    for record in records:
        record["request_input_hash"]=_semantic_request(record,spec["model"],spec["reasoning_effort"],1.0).input_hash
    if Path(output_jsonl).exists() and read_jsonl(output_jsonl)!=records:
        raise ValueError("Existing semantic manifest differs from selected preparation")
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


def _compatible_completed_ids(output_jsonl: Path, tasks: list[dict[str, Any]], request_builder: Any, model: str, reasoning_effort: str, temperature: float, resume: bool = True) -> set[str]:
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
        for field, expected in (("run_id",spec["run_ids"]["blinded_scoring"]),("source_run_id",spec["run_ids"]["main_text"]),("spec_fingerprint",spec["fingerprint"]),("model",model),("reasoning_effort",reasoning_effort)):
            if task.get(field) != expected:
                raise ValueError(f"Scoring manifest identity mismatch: {field}")
        hashes = (("text", "text_hash"),) if "text" in task else (("text_a", "text_a_hash"),("text_b", "text_b_hash"))
        if any(_sha256_text(task[text]) != task[digest] for text,digest in hashes):
            raise ValueError("Scoring manifest text hash mismatch")
        if task.get("request_input_hash")!=request_builder(task,model,reasoning_effort,temperature).input_hash:
            raise ValueError("Prepared scoring request changed")
        expected_prompt = SEMANTIC_SYSTEM_PROMPT if "text" in task else PAIRWISE_SYSTEM_PROMPT
        if task.get("prompt_hash") != _sha256_text(expected_prompt):
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


def execute_semantic_manifest(
    manifest_path: str | Path,
    output_jsonl: str | Path,
    runner: LLMRunner,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_path)
    done_ids = _compatible_completed_ids(Path(output_jsonl),tasks,_semantic_request,model,reasoning_effort,temperature,resume)
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
            if response.model!=model or response.input_hash!=request.input_hash:
                raise ValueError("Returned scorer model/request identity mismatch")
            base = {
                "task_id": task["task_id"],
                **{field:task[field] for field in ("run_id","source_run_id","spec_fingerprint","prompt_hash")},
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
            if returncode != 0:
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
    spec,corpus = _three_role_context(corpus_csv,output_jsonl)
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
                    "run_id": spec["run_ids"]["blinded_scoring"],
                    "source_run_id": spec["run_ids"]["main_text"],
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "meeting_number": int(row["meeting_number"]),
                    "iteration": int(row["iteration"]),
                    "pair_type": f"{left_role}_vs_{right_role}",
                    "role_a": ordered[0][0],
                    "role_b": ordered[1][0],
                    "text_a_hash": _sha256_text(ordered[0][1]),
                    "text_b_hash": _sha256_text(ordered[1][1]),
                    "prompt_version": PAIRWISE_PROMPT_VERSION,
                    "prompt_hash": _sha256_text(PAIRWISE_SYSTEM_PROMPT),
                    "text_a": ordered[0][1],
                    "text_b": ordered[1][1],
                }
            )
    rng.shuffle(records)
    for record in records:
        record["request_input_hash"]=_pairwise_request(record,spec["model"],spec["reasoning_effort"],1.0).input_hash
    if Path(output_jsonl).exists() and read_jsonl(output_jsonl)!=records:
        raise ValueError("Existing pairwise manifest differs from selected preparation")
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
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    limit: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    tasks = read_jsonl(manifest_path)
    done_ids = _compatible_completed_ids(Path(output_jsonl),tasks,_pairwise_request,model,reasoning_effort,temperature,resume)
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
            if response.model!=model or response.input_hash!=request.input_hash:
                raise ValueError("Returned scorer model/request identity mismatch")
            base = {
                "task_id": task["task_id"],
                **{field:task[field] for field in ("run_id","source_run_id","spec_fingerprint","prompt_hash")},
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
            if returncode != 0:
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
    spec, corpus = _three_role_context(corpus_csv, output_csv)
    semantic_results=Path(semantic_results_jsonl)
    if pairwise_results_jsonl is None:
        raise ValueError("Complete pairwise results are required")
    pairwise_results=Path(pairwise_results_jsonl)
    semantic_tasks = read_jsonl(semantic_results.parent / "semantic_tone_manifest.jsonl")
    pairwise_tasks = read_jsonl(pairwise_results.parent / "pairwise_tone_manifest.jsonl")
    expected_semantic = {(int(row.meeting_number),int(row.iteration),role) for row in corpus.itertuples() for role in TEXT_COLUMNS}
    expected_pairs = {(int(row.meeting_number),int(row.iteration),f"{left}_vs_{right}") for row in corpus.itertuples() for left,right in (("original","hawk"),("original","dove"),("hawk","dove"))}
    semantic_keys = [(int(row["meeting_number"]),int(row["iteration"]),row["text_role"]) for row in semantic_tasks]
    pair_keys = [(int(row["meeting_number"]),int(row["iteration"]),row["pair_type"]) for row in pairwise_tasks]
    if len(set(semantic_keys))!=len(semantic_keys) or set(semantic_keys)!=expected_semantic or len(set(pair_keys))!=len(pair_keys) or set(pair_keys)!=expected_pairs:
        raise ValueError("Scoring manifests do not cover exact semantic/pairwise keys")
    accepted_semantic = _compatible_completed_ids(semantic_results,semantic_tasks,_semantic_request,spec["model"],spec["reasoning_effort"],1.0)
    accepted_pairwise = _compatible_completed_ids(pairwise_results,pairwise_tasks,_pairwise_request,spec["model"],spec["reasoning_effort"],1.0)
    if len(accepted_semantic)!=len(semantic_tasks) or len(accepted_pairwise)!=len(pairwise_tasks):
        raise ValueError("Realized doses require every selected real completed score")
    latest_semantic = {row["task_id"]:row for row in read_jsonl(semantic_results)}
    latest_pairwise = {row["task_id"]:row for row in read_jsonl(pairwise_results)}
    semantic_by_key = {(int(row["meeting_number"]),int(row["iteration"]),str(row["text_role"])):row for row in latest_semantic.values()}
    pairwise_rows = list(latest_pairwise.values())
    source_hashes = {(int(row.meeting_number),int(row.iteration),role):_sha256_text(getattr(row,column)) for row in corpus.itertuples() for role,column in TEXT_COLUMNS.items()}
    for key,row in semantic_by_key.items():
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
                    "run_id": spec["run_ids"]["main_text"],
                    "source_run_id": spec["run_ids"]["main_text"],
                    "scorer_run_id": spec["run_ids"]["blinded_scoring"],
                    "scoring_protocol": "three_role_alternate",
                    "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"],
                    "spec_fingerprint": spec["fingerprint"],
                    "status": "completed",
                    "runner_dry_run": False,
                    "source_text_hash": _sha256_text(text),
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
    spec=load_spec(require_frozen=True)
    validate_artifact_path(Path(dose_csv).resolve(),spec)
    if output_json:
        validate_artifact_path(Path(output_json).resolve(),spec)
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
