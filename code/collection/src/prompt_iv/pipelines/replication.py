from __future__ import annotations

from pathlib import Path
from typing import Iterable
import json
import random

import pandas as pd

from prompt_iv.agents.prompts import build_forecaster_prompt
from prompt_iv.agents.targets import inflation_target_for_meeting
from prompt_iv.econometrics import tsls_just_identified
from prompt_iv.generation.prompts import normalize_prompt_version
from prompt_iv.pipelines.empirical import MIXTURE_COLUMNS, estimate_baseline
from prompt_iv.pipelines.generation import prepare_generation_plan


FORECASTER_ALIASES = {
    "anchored": "anchored",
    "unanchored": "unanchored",
}


def _normalize_items(values: Iterable[str]) -> list[str]:
    return [str(value).strip().lower() for value in values if str(value).strip()]


def _safe_text_filename(meeting_number: int) -> str:
    return f"ata{meeting_number:03d}.txt"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def prepare_replication_run(
    root: str | Path,
    run_id: str,
    minutes_dir: str | Path,
    iterations: int = 5,
    parts: int = 5,
    stances: Iterable[str] = ("hawk", "dove"),
    model: str = "gpt-5.5",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    max_meetings: int | None = None,
    prompt_version: str | None = None,
) -> dict:
    root = Path(root)
    run_dir = root / "data" / "interim" / "replications" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    normalized_prompt_version = normalize_prompt_version(prompt_version)

    manifest = run_dir / "rewrite_manifest.jsonl"
    generation = prepare_generation_plan(
        minutes_dir=minutes_dir,
        output_jsonl=manifest,
        stances=stances,
        iterations=iterations,
        parts=parts,
        max_meetings=max_meetings,
        prompt_version=normalized_prompt_version,
    )
    config = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "minutes_dir": str(minutes_dir),
        "iterations": iterations,
        "parts": parts,
        "stances": list(_normalize_items(stances)),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "temperature": temperature,
        "max_meetings": max_meetings,
        "prompt_version": normalized_prompt_version,
        "rewrite_manifest": str(manifest),
        "rewrite_results": str(run_dir / "rewrite_results.jsonl"),
        "counterfactual_corpus": str(run_dir / "counterfactual_corpus.csv"),
    }
    _write_json(run_dir / "replication_config.json", config)
    return {"config": config, "generation": generation}


def build_counterfactual_agent_manifest(
    corpus_csv: str | Path,
    output_jsonl: str | Path,
    text_dir: str | Path,
    scenarios: Iterable[str] = ("hawk", "dove"),
    forecaster_types: Iterable[str] = ("anchored", "unanchored"),
    num_agents: int = 1,
    initial_expectation: float = 3.80,
    random_seed: int = 123,
) -> dict:
    corpus_csv = Path(corpus_csv)
    output_jsonl = Path(output_jsonl)
    text_dir = Path(text_dir)
    scenarios = _normalize_items(scenarios)
    forecaster_types = _normalize_items(forecaster_types)
    unknown = sorted(set(forecaster_types) - set(FORECASTER_ALIASES))
    if unknown:
        raise ValueError(f"unknown forecaster type(s): {unknown}")
    if num_agents < 1:
        raise ValueError("num_agents must be at least 1")

    df = pd.read_csv(corpus_csv, low_memory=False)
    required = {"meeting_number", "iteration"} | {f"{scenario}_text" for scenario in scenarios}
    missing = sorted(required - set(df.columns))
    if missing:
        raise KeyError(f"Missing required corpus columns: {missing}")
    df = df.sort_values(["iteration", "meeting_number"]).reset_index(drop=True)

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(random_seed)
    group_seeds: dict[tuple[int, str, str, int], int] = {}
    records_written = 0
    text_files_written = 0

    with output_jsonl.open("w", encoding="utf-8") as out:
        for iteration, iteration_df in df.groupby("iteration", sort=True):
            iteration_int = int(iteration)
            for scenario in scenarios:
                text_column = f"{scenario}_text"
                scenario_df = iteration_df.sort_values("meeting_number").reset_index(drop=True)
                for forecaster_type in forecaster_types:
                    for agent_id in range(1, num_agents + 1):
                        seed_key = (iteration_int, scenario, forecaster_type, agent_id)
                        group_seeds.setdefault(seed_key, rng.randint(0, 1_000_000_000))
                        state_group = f"cf-iter-{iteration_int:02d}-{scenario}"
                        for sequence_index, row in enumerate(scenario_df.itertuples(index=False), start=1):
                            meeting_number = int(getattr(row, "meeting_number"))
                            text = str(getattr(row, text_column))
                            text_path = text_dir / f"iteration-{iteration_int:02d}" / scenario / _safe_text_filename(meeting_number)
                            if not text_path.exists() or text_path.read_text(encoding="utf-8") != text:
                                text_path.parent.mkdir(parents=True, exist_ok=True)
                                text_path.write_text(text, encoding="utf-8")
                                text_files_written += 1

                            prompt_family = build_forecaster_prompt(
                                forecaster_type,
                                previous_inflation=0.0,
                                target=0.0,
                            )
                            task_id = (
                                f"cf-iter-{iteration_int:02d}"
                                f"-{scenario}"
                                f"-{forecaster_type}"
                                f"-agent-{agent_id:03d}"
                                f"-seq-{sequence_index:03d}"
                                f"-meeting-{meeting_number:03d}"
                            )
                            record = {
                                "task_id": task_id,
                                "state_group": state_group,
                                "scenario": scenario,
                                "corpus_iteration": iteration_int,
                                "agent_id": agent_id,
                                "agent_seed": group_seeds[seed_key],
                                "sequence_index": sequence_index,
                                "meeting_number": meeting_number,
                                "source_path": str(text_path),
                                "source_corpus": str(corpus_csv),
                                "forecaster_type": forecaster_type,
                                "inflation_target": inflation_target_for_meeting(meeting_number),
                                "initial_expectation": initial_expectation,
                                "previous_expectation_required": sequence_index > 1,
                                "prompt_family_hash": _hash_text(prompt_family),
                            }
                            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                            records_written += 1

    return {
        "corpus": str(corpus_csv),
        "output": str(output_jsonl),
        "text_dir": str(text_dir),
        "records_written": records_written,
        "text_files_written": text_files_written,
        "iterations": sorted(int(x) for x in df["iteration"].dropna().unique()),
        "meetings": int(df["meeting_number"].nunique()),
        "scenarios": scenarios,
        "forecaster_types": forecaster_types,
        "num_agents": num_agents,
    }


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assemble_counterfactual_agent_results(
    results_jsonl: str | Path,
    long_output_csv: str | Path | None = None,
    wide_output_csv: str | Path | None = None,
) -> dict:
    latest_completed: dict[str, dict] = {}
    completed_without_task_id: list[dict] = []
    with Path(results_jsonl).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") != "completed":
                continue
            task_id = row.get("task_id")
            if task_id is None:
                completed_without_task_id.append(row)
                continue
            latest_completed[str(task_id)] = row

    rows = []
    for row in [*completed_without_task_id, *latest_completed.values()]:
        forecaster_type = str(row.get("forecaster_type"))
        rows.append(
            {
                "iteration": int(row.get("corpus_iteration")),
                "scenario": str(row.get("scenario")),
                "reuniao": int(row.get("meeting_number")),
                "forecaster_type": forecaster_type,
                "forecaster_alias": FORECASTER_ALIASES.get(forecaster_type, forecaster_type),
                "agent_id": int(row.get("agent_id")),
                "agent_seed": int(row.get("agent_seed")),
                "expectativa_anterior": float(row.get("previous_expectation")),
                "nova_expectativa": float(row.get("new_expectation")),
                "tom_percebido": float(row.get("perceived_tone")),
            }
        )

    long_df = pd.DataFrame(rows)
    if long_df.empty:
        wide_df = pd.DataFrame()
    else:
        collapsed = (
            long_df.groupby(["reuniao", "iteration", "scenario", "forecaster_alias"], as_index=False)
            .agg(nova_expectativa=("nova_expectativa", "mean"), tom_percebido=("tom_percebido", "mean"))
        )
        pieces = []
        for metric in ["nova_expectativa", "tom_percebido"]:
            pivot = collapsed.pivot_table(
                index=["reuniao", "iteration"],
                columns=["scenario", "forecaster_alias"],
                values=metric,
                aggfunc="mean",
            )
            pivot.columns = [f"{metric}_{scenario}_{alias}" for scenario, alias in pivot.columns]
            pieces.append(pivot.reset_index())
        wide_df = pieces[0]
        for piece in pieces[1:]:
            wide_df = wide_df.merge(piece, on=["reuniao", "iteration"], how="outer")
        wide_df = wide_df.sort_values(["iteration", "reuniao"]).reset_index(drop=True)

    if long_output_csv:
        path = Path(long_output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        long_df.to_csv(path, index=False)
    if wide_output_csv:
        path = Path(wide_output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        wide_df.to_csv(path, index=False)

    return {
        "results": str(results_jsonl),
        "completed_rows": int(len(long_df)),
        "unique_completed_tasks": int(len(latest_completed)),
        "wide_rows": int(len(wide_df)),
        "long_output": str(long_output_csv) if long_output_csv else None,
        "wide_output": str(wide_output_csv) if wide_output_csv else None,
    }


def estimate_replication(
    baseline_dataset: str | Path,
    tone_scores_csv: str | Path,
    agent_wide_csv: str | Path,
    output_json: str | Path | None = None,
    draw_output_csv: str | Path | None = None,
) -> dict:
    baseline = estimate_baseline(baseline_dataset)
    base_df = pd.read_stata(baseline_dataset).sort_values("reuniao").reset_index(drop=True)
    base_cols = [
        "reuniao",
        "inflacao12m",
        "score_orig",
        *MIXTURE_COLUMNS,
    ]
    base = base_df[base_cols].drop_duplicates("reuniao").copy()

    tone = pd.read_csv(tone_scores_csv, low_memory=False).copy()
    if "meeting_number" in tone.columns and "reuniao" not in tone.columns:
        tone = tone.rename(columns={"meeting_number": "reuniao"})
    for scenario in ["hawk", "dove"]:
        score_col = f"{scenario}_text_score"
        wc_col = f"{scenario}_text_word_count"
        if score_col not in tone.columns and f"score_{scenario}" in tone.columns:
            tone[score_col] = tone[f"score_{scenario}"]
        if wc_col not in tone.columns and f"wc_{scenario}" in tone.columns:
            tone[wc_col] = tone[f"wc_{scenario}"]
    if "original_text_score" in tone.columns:
        tone["score_orig_replication"] = tone["original_text_score"] * 100.0

    agents = pd.read_csv(agent_wide_csv, low_memory=False)
    data = tone.merge(agents, on=["reuniao", "iteration"], how="inner").merge(base, on="reuniao", how="inner")

    beta = baseline["mixture"]["beta"]
    required_cf = [
        "nova_expectativa_dove_anchored",
        "nova_expectativa_hawk_anchored",
        "nova_expectativa_dove_unanchored",
        "nova_expectativa_hawk_unanchored",
        "dove_text_word_count",
        "hawk_text_word_count",
        "dove_text_score",
        "hawk_text_score",
        "ibcbr_agro",
        "ibcbr_service",
        "ibcbr_industry",
    ]
    data = data.dropna(subset=required_cf).copy()

    for scenario in ["dove", "hawk"]:
        data[f"llm_comp_{scenario}"] = (
            beta[1] * data[f"nova_expectativa_{scenario}_anchored"]
            + beta[2] * data[f"nova_expectativa_{scenario}_unanchored"]
            + beta[3] * data[f"{scenario}_text_word_count"]
            + beta[4] * data["ibcbr_agro"]
            + beta[5] * data["ibcbr_service"]
            + beta[6] * data["ibcbr_industry"]
            + beta[0]
        )

    iv_rows = []
    for scenario in ["hawk", "dove"]:
        scenario_df = data[["reuniao", "iteration", f"llm_comp_{scenario}", f"{scenario}_text_score"]].copy()
        scenario_df["scenario"] = scenario
        scenario_df["hawk"] = 1 if scenario == "hawk" else 0
        scenario_df = scenario_df.rename(
            columns={f"llm_comp_{scenario}": "llm_comp", f"{scenario}_text_score": "tone"}
        )
        scenario_df["tone"] = scenario_df["tone"] * 100.0
        scenario_df["pair_fe"] = scenario_df["reuniao"].astype(str) + "::" + scenario_df["iteration"].astype(str)
        iv_rows.append(scenario_df)
    iv_df = pd.concat(iv_rows, ignore_index=True)

    pooled_meeting_fe = tsls_just_identified(iv_df["llm_comp"], iv_df["tone"], iv_df["hawk"], iv_df["reuniao"])
    pooled_pair_fe = tsls_just_identified(iv_df["llm_comp"], iv_df["tone"], iv_df["hawk"], iv_df["pair_fe"])

    draw_reports = []
    for iteration, group in iv_df.groupby("iteration", sort=True):
        if group["reuniao"].nunique() < 2:
            continue
        draw_iv = tsls_just_identified(group["llm_comp"], group["tone"], group["hawk"], group["reuniao"])
        draw_reports.append(
            {
                "iteration": int(iteration),
                "beta_iv": float(draw_iv["beta_iv"]),
                "first_stage_coef": float(draw_iv["first_stage_coef"]),
                "n": int(draw_iv["n"]),
                "clusters": int(draw_iv["clusters"]),
            }
        )
    draw_df = pd.DataFrame(draw_reports)
    if draw_output_csv:
        path = Path(draw_output_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        draw_df.to_csv(path, index=False)

    beta_baseline = float(baseline["prompt_iv"]["beta_iv"])
    beta_rep = float(pooled_pair_fe["beta_iv"])
    diagnostics = {
        "tone_gap_hawk_minus_dove_pp": float((data["hawk_text_score"] - data["dove_text_score"]).mean() * 100.0),
        "llm_comp_gap_hawk_minus_dove": float((data["llm_comp_hawk"] - data["llm_comp_dove"]).mean()),
        "anchored_gap_hawk_minus_dove": float(
            (data["nova_expectativa_hawk_anchored"] - data["nova_expectativa_dove_anchored"]).mean()
        ),
        "unanchored_gap_hawk_minus_dove": float(
            (data["nova_expectativa_hawk_unanchored"] - data["nova_expectativa_dove_unanchored"]).mean()
        ),
    }
    report = {
        "baseline_dataset": str(baseline_dataset),
        "tone_scores": str(tone_scores_csv),
        "agent_wide": str(agent_wide_csv),
        "rows_after_merge": int(len(data)),
        "meetings": int(data["reuniao"].nunique()) if not data.empty else 0,
        "iterations": sorted(int(x) for x in data["iteration"].dropna().unique()) if not data.empty else [],
        "baseline_beta_iv": beta_baseline,
        "pooled_meeting_fe": pooled_meeting_fe,
        "pooled_pair_fe": pooled_pair_fe,
        "draw_estimates": draw_reports,
        "draw_summary": {
            "mean_beta_iv": float(draw_df["beta_iv"].mean()) if not draw_df.empty else None,
            "min_beta_iv": float(draw_df["beta_iv"].min()) if not draw_df.empty else None,
            "max_beta_iv": float(draw_df["beta_iv"].max()) if not draw_df.empty else None,
        },
        "replication_vs_baseline": {
            "same_sign": bool(beta_baseline * beta_rep > 0),
            "ratio_pair_fe_to_baseline": float(beta_rep / beta_baseline) if beta_baseline else None,
        },
        "diagnostics": diagnostics,
        "baseline": {
            "mixture_r2": baseline["mixture"]["r2"],
            "beta_iv_profile_composite": baseline["prompt_iv"]["beta_iv"],
            "focus_tone_observational": baseline["focus_ar"]["beta_focus"],
            "focus_persistence": baseline["focus_ar"]["rho_focus"],
            "design_status": baseline["design_status"],
        },
    }
    if output_json:
        _write_json(Path(output_json), report)
    return report
