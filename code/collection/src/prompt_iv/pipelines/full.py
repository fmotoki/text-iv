from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from prompt_iv.agents.execution import execute_agent_manifest
from prompt_iv.agents.simulation import build_agent_plan, summarize_agent_plan, write_agent_plan_manifest
from prompt_iv.generation.corpus import assemble_counterfactual_corpus
from prompt_iv.generation.execution import execute_rewrite_manifest
from prompt_iv.llm.runner import DryRunRunner
from prompt_iv.paths import load_paths
from prompt_iv.pipelines.empirical import estimate_baseline
from prompt_iv.pipelines.generation import prepare_generation_plan
from prompt_iv.pipelines.data_inventory import summarize_data_dir
from prompt_iv.pipelines.smoke import run_smoke
from prompt_iv.pipelines.tone import score_minutes_folder


def run_safe_pipeline(
    root: str | Path | None = None,
    max_meetings: int = 2,
    rewrite_limit: int = 2,
    agent_limit: int = 2,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    """Run the refactored pipeline in safe mode.

    This command intentionally does not create new LLM collections. It prepares
    manifests, consumes a small dry-run slice, scores copied minutes, summarizes
    organized data, and reproduces baseline estimates from copied processed data.
    """
    paths = load_paths(Path(root) if root else None)
    minutes_dir = paths.raw_data_dir / "copom_minutes"
    if not minutes_dir.exists():
        minutes_dir = paths.minutes_dir

    report: dict[str, Any] = {
        "mode": "safe_dry_run",
        "root": str(paths.root),
        "real_llm_calls": False,
    }

    report["smoke"] = run_smoke(paths.root)

    rewrite_manifest = paths.interim_data_dir / "counterfactuals" / "rewrite_task_manifest.jsonl"
    report["generation_plan"] = prepare_generation_plan(
        minutes_dir=minutes_dir,
        output_jsonl=rewrite_manifest,
        stances=("hawk", "dove"),
        iterations=1,
        parts=5,
        max_meetings=max_meetings,
    )

    rewrite_results = paths.interim_data_dir / "counterfactuals" / "rewrite_results.jsonl"
    report["counterfactual_execution"] = execute_rewrite_manifest(
        manifest_path=rewrite_manifest,
        output_jsonl=rewrite_results,
        runner=DryRunRunner(log_path=paths.logs_dir / "pipeline_counterfactual_dry_run.jsonl"),
        limit=rewrite_limit,
        resume=True,
    )

    counterfactual_corpus = paths.interim_data_dir / "counterfactuals" / "counterfactual_corpus.csv"
    assembled = assemble_counterfactual_corpus(rewrite_results, output_csv=counterfactual_corpus)
    report["counterfactual_corpus"] = {
        "rows": int(len(assembled)),
        "output": str(counterfactual_corpus),
        "complete_real_rows_required_for_scoring": True,
    }

    minutes_scores = paths.interim_data_dir / "tone" / "minutes_tone_scores.csv"
    minutes_df = score_minutes_folder(minutes_dir, minutes_scores)
    report["tone_minutes"] = {"rows": int(len(minutes_df)), "output": str(minutes_scores)}

    agent_manifest = paths.interim_data_dir / "agents" / "agent_task_manifest.jsonl"
    agent_tasks = build_agent_plan(minutes_dir=minutes_dir, max_meetings=max_meetings)
    write_agent_plan_manifest(agent_tasks, agent_manifest)
    agent_summary = summarize_agent_plan(agent_tasks)
    agent_summary["output"] = str(agent_manifest)
    report["agent_plan"] = agent_summary

    agent_results = paths.interim_data_dir / "agents" / "agent_results.jsonl"
    report["agent_execution"] = execute_agent_manifest(
        manifest_path=agent_manifest,
        output_jsonl=agent_results,
        runner=DryRunRunner(log_path=paths.logs_dir / "pipeline_agent_dry_run.jsonl"),
        limit=agent_limit,
        resume=True,
    )

    data_summary = paths.root / "docs" / "data_summary.csv"
    data_df = summarize_data_dir(paths.root / "data")
    data_summary.parent.mkdir(parents=True, exist_ok=True)
    data_df.to_csv(data_summary, index=False)
    report["data_summary"] = {"rows": int(len(data_df)), "output": str(data_summary)}

    baseline_output = paths.root / "outputs" / "tables" / "baseline_estimates.json"
    baseline_dataset = paths.root / "outputs" / "work" / "gpt55_calibration_data.dta"
    if baseline_dataset.exists():
        baseline = estimate_baseline(baseline_dataset, baseline_output)
        report["baseline"] = {
            "dataset": str(baseline_dataset),
            "output": str(baseline_output),
            "n_meetings": baseline["n_meetings"],
            "mixture_r2": baseline["mixture"]["r2"],
            "focus_tone_observational": baseline["focus_ar"]["beta_focus"],
            "focus_persistence": baseline["focus_ar"]["rho_focus"],
            "design_status": baseline["design_status"],
        }
    else:
        report["baseline"] = {"skipped": True, "reason": f"missing {baseline_dataset}"}

    if output_json:
        out = Path(output_json)
        if not out.is_absolute():
            out = paths.root / out
    else:
        out = paths.root / "outputs" / "logs" / "safe_pipeline_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(out)
    return report
