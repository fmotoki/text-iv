from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from prompt_iv.agents.common_prior import (
    audit_arm_specific_prior_run,
    audit_common_prior_gate,
    audit_common_prior_run,
    audit_original_prior_run,
    execute_arm_specific_prior_run,
    execute_common_prior_run,
    execute_original_prior_run,
    prepare_arm_specific_prior_run,
    prepare_common_prior_run,
    prepare_original_prior_run,
)
from prompt_iv.agents.execution import execute_agent_manifest
from prompt_iv.agents.simulation import build_agent_plan, summarize_agent_plan, write_agent_plan_manifest
from prompt_iv.dose_response import (
    build_pairwise_manifest,
    build_realized_dose_dataset,
    build_semantic_manifest,
    estimate_dose_response,
    execute_pairwise_manifest,
    execute_semantic_manifest,
)
from prompt_iv.generation.corpus import assemble_counterfactual_corpus
from prompt_iv.generation.execution import execute_rewrite_database, execute_rewrite_manifest
from prompt_iv.generation.quality import evaluate_counterfactual_results
from prompt_iv.llm.runner import CodexCliRunner, DryRunRunner, LLMRequest
from prompt_iv.paths import load_paths, load_spec, validate_artifact_path
from prompt_iv.pipelines.full import run_safe_pipeline
from prompt_iv.pipelines.generation import prepare_generation_plan
from prompt_iv.pipelines.replication import (
    assemble_counterfactual_agent_results,
    build_counterfactual_agent_manifest,
    estimate_replication,
    prepare_replication_run,
)
from prompt_iv.pipelines.smoke import run_smoke
from prompt_iv.pipelines.data_inventory import summarize_data_dir
from prompt_iv.pipelines.tone import score_counterfactual_minutes, score_minutes_folder
from prompt_iv.pipelines.empirical import estimate_baseline
from prompt_iv.referees.backlog import parse_revision_plan, write_backlog_yaml
from prompt_iv.storage import sync_project_database
from prompt_iv.storage.sqlite import connect_database, sync_minutes
from prompt_iv.tone import SentimentAnalyzer


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_int_csv(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _meeting_selection(args: argparse.Namespace) -> list[int]:
    explicit = _split_int_csv(getattr(args, "meeting_numbers", None))
    if explicit:
        return explicit
    first = int(args.first_meeting)
    last = int(args.last_meeting)
    if last < first:
        raise SystemExit("--last-meeting must be greater than or equal to --first-meeting")
    return list(range(first, last + 1))


def _default_minutes_dir(paths) -> Path:
    copied = paths.raw_data_dir / "copom_minutes"
    return copied if copied.exists() else paths.minutes_dir


def _resolve_project_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _build_runner(args: argparse.Namespace, log_path: Path):
    if args.runner == "codex":
        return CodexCliRunner(allow_real_llm=args.allow_real_llm, log_path=log_path, timeout_seconds=args.timeout_seconds)
    return DryRunRunner(log_path=log_path)


def cmd_smoke_test(args: argparse.Namespace) -> int:
    result = run_smoke(Path(args.root) if args.root else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_score_text(args: argparse.Namespace) -> int:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        raise SystemExit("Provide --file or --text.")
    result = SentimentAnalyzer().analyze(text)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    codex = CodexCliRunner(log_path=paths.logs_dir / "llm_check.jsonl")
    ok, detail = codex.check_available()
    dry = DryRunRunner(log_path=paths.logs_dir / "llm_check.jsonl")
    response = dry.generate(LLMRequest(system_prompt="Check only.", user_prompt="Do not call a real LLM."))
    result = {
        "codex_cli_available": ok,
        "codex_cli_detail": detail,
        "dry_run_ok": response.dry_run,
        "log": str(paths.logs_dir / "llm_check.jsonl"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 2


def cmd_extract_backlog(args: argparse.Namespace) -> int:
    items = parse_revision_plan(args.source)
    write_backlog_yaml(items, args.output)
    print(json.dumps({"source": args.source, "output": args.output, "items": len(items)}, indent=2))
    return 0


def cmd_sync_database(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    minutes_dir = Path(args.minutes_dir) if args.minutes_dir else _default_minutes_dir(paths)
    if not minutes_dir.is_absolute():
        minutes_dir = paths.root / minutes_dir
    run_dirs = [_resolve_project_path(paths.root, item) for item in args.run_dir] if args.run_dir else None
    if args.replace:
        raise ValueError("Prospective collection never replaces a database")
    if args.minutes_only:
        if run_dirs:
            raise ValueError("--minutes-only cannot include --run-dir")
        conn = connect_database(args.output)
        try:
            with conn:
                count = sync_minutes(conn, paths.root, minutes_dir)
                found = {int(row[0]) for row in conn.execute("SELECT meeting_number FROM minutes")}
                if not set(paths.specification["sample"]["meeting_numbers"]).issubset(found):
                    raise ValueError("Selected original minutes are incomplete")
            summary = {"database": args.output, "minutes_ingested": count, "minutes_only": True}
        finally:
            conn.close()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if not run_dirs:
        raise ValueError("Provide explicit --run-dir selections. Automatic all-directory sync is disabled.")
    summary = sync_project_database(
        db_path=_resolve_project_path(paths.root, args.output),
        root=paths.root,
        minutes_dir=minutes_dir,
        run_dirs=run_dirs,
        replace=args.replace,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_generate_counterfactuals(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    minutes_dir = Path(args.minutes_dir) if args.minutes_dir else _default_minutes_dir(paths)
    output = Path(args.output)
    if not minutes_dir.is_absolute():
        minutes_dir = paths.root / minutes_dir
    if not output.is_absolute():
        output = paths.root / output
    summary = prepare_generation_plan(
        minutes_dir=minutes_dir,
        output_jsonl=output,
        stances=_split_csv(args.stances),
        iterations=args.iterations,
        parts=args.parts,
        max_meetings=args.max_meetings,
        meeting_numbers=_meeting_selection(args),
        prompt_version=args.prompt_version,
    )
    runner = DryRunRunner(log_path=paths.logs_dir / "generation_plan_dry_run.jsonl")
    runner.generate(
        LLMRequest(
            system_prompt="Counterfactual generation planning only.",
            user_prompt=json.dumps(summary, ensure_ascii=False, sort_keys=True),
            metadata={"command": "generate-counterfactuals", "real_llm": False},
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_run_agents(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    minutes_dir = Path(args.minutes_dir) if args.minutes_dir else _default_minutes_dir(paths)
    output = Path(args.output)
    if not minutes_dir.is_absolute():
        minutes_dir = paths.root / minutes_dir
    if not output.is_absolute():
        output = paths.root / output
    tasks = build_agent_plan(
        minutes_dir=minutes_dir,
        forecaster_types=_split_csv(args.forecaster_types),
        num_agents=args.num_agents,
        initial_expectation=args.initial_expectation,
        random_seed=args.random_seed,
        max_meetings=args.max_meetings,
        meeting_numbers=paths.specification["sample"]["meeting_numbers"],
    )
    written = write_agent_plan_manifest(tasks, output)
    summary = summarize_agent_plan(tasks)
    summary.update({"output": str(output), "records_written": written, "dry_run": True})
    runner = DryRunRunner(log_path=paths.logs_dir / "agent_plan_dry_run.jsonl")
    runner.generate(
        LLMRequest(
            system_prompt="Synthetic forecaster planning only.",
            user_prompt=json.dumps(summary, ensure_ascii=False, sort_keys=True),
            metadata={"command": "run-agents", "real_llm": False},
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_prepare_original_priors_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    summary = prepare_original_prior_run(
        database,
        args.run_id,
        meeting_numbers=_meeting_selection(args),
        forecaster_types=_split_csv(args.forecaster_types),
        initial_expectation=args.initial_expectation,
        random_seed=args.random_seed,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_original_priors_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    runner = _build_runner(args, None)
    summary = execute_original_prior_run(
        database,
        args.run_id,
        runner=runner,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(summary["statuses"].get(status, 0) for status in ("failed", "parse_failed", "usage_limited"))
    return 0 if bad == 0 else 1


def cmd_prepare_common_prior_agents_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    iterations = _split_int_csv(args.iterations)
    if not iterations:
        raise SystemExit("--iterations must contain at least one draw number")
    summary = prepare_common_prior_run(
        database,
        args.run_id,
        source_run_id=args.source_run_id,
        prior_run_id=args.prior_run_id,
        meeting_numbers=_meeting_selection(args),
        iterations=iterations,
        roles=_split_csv(args.roles),
        forecaster_types=_split_csv(args.forecaster_types),
        random_seed=args.random_seed,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_common_prior_agents_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    runner = _build_runner(args, None)
    summary = execute_common_prior_run(
        database,
        args.run_id,
        runner=runner,
        limit=args.limit,
        resume=not args.no_resume,
        max_workers=args.max_workers,
        iterations=_split_int_csv(args.iterations),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(summary["statuses"].get(status, 0) for status in ("failed", "parse_failed", "usage_limited"))
    return 0 if bad == 0 else 1


def cmd_audit_common_prior_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    iterations = _split_int_csv(args.iterations)
    common_audit = (
        audit_common_prior_gate(database, args.run_id, iterations=iterations)
        if iterations
        else audit_common_prior_run(database, args.run_id)
    )
    report: dict[str, object] = {"common_prior": common_audit}
    if args.prior_run_id:
        report["original_priors"] = audit_original_prior_run(database, args.prior_run_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ready = all(bool(item["ready"]) for item in report.values())
    return 0 if ready else 1


# argparse requires one callback for this existing audit primitive.
def cmd_audit_original_priors_db(args: argparse.Namespace) -> int:
    report = audit_original_prior_run(args.database, args.run_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


def cmd_prepare_arm_specific_priors_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    iterations = _split_int_csv(args.iterations)
    if not iterations:
        raise SystemExit("--iterations must contain at least one draw number")
    summary = prepare_arm_specific_prior_run(
        database,
        args.run_id,
        source_run_id=args.source_run_id,
        meeting_numbers=_meeting_selection(args),
        iterations=iterations,
        roles=_split_csv(args.roles),
        forecaster_types=_split_csv(args.forecaster_types),
        initial_expectation=args.initial_expectation,
        random_seed=args.random_seed,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_arm_specific_priors_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    runner = _build_runner(args, None)
    summary = execute_arm_specific_prior_run(
        database,
        args.run_id,
        runner=runner,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(
        summary["statuses"].get(status, 0)
        for status in ("failed", "parse_failed", "usage_limited")
    )
    return 0 if bad == 0 else 1


def cmd_audit_arm_specific_priors_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    report = audit_arm_specific_prior_run(database, args.run_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 1


def cmd_prepare_replication(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    minutes_dir = Path(args.minutes_dir) if args.minutes_dir else _default_minutes_dir(paths)
    if not minutes_dir.is_absolute():
        minutes_dir = paths.root / minutes_dir
    summary = prepare_replication_run(
        root=paths.root,
        run_id=args.run_id,
        minutes_dir=minutes_dir,
        iterations=args.iterations,
        parts=args.parts,
        stances=_split_csv(args.stances),
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        max_meetings=args.max_meetings,
        prompt_version=args.prompt_version,
        meeting_numbers=_meeting_selection(args),
        run_directory=args.run_directory,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_prepare_counterfactual_agents(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    corpus = _resolve_project_path(paths.root, args.corpus)
    output = _resolve_project_path(paths.root, args.output)
    text_dir = _resolve_project_path(paths.root, args.text_dir)
    summary = build_counterfactual_agent_manifest(
        corpus_csv=corpus,
        output_jsonl=output,
        text_dir=text_dir,
        scenarios=_split_csv(args.scenarios),
        forecaster_types=_split_csv(args.forecaster_types),
        num_agents=args.num_agents,
        initial_expectation=args.initial_expectation,
        random_seed=args.random_seed,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_assemble_counterfactual_agents(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    results = _resolve_project_path(paths.root, args.results)
    long_output = _resolve_project_path(paths.root, args.long_output)
    wide_output = _resolve_project_path(paths.root, args.wide_output)
    summary = assemble_counterfactual_agent_results(
        results_jsonl=results,
        long_output_csv=long_output,
        wide_output_csv=wide_output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_estimate_replication(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    baseline = _resolve_project_path(paths.root, args.baseline_dataset)
    tone_scores = _resolve_project_path(paths.root, args.tone_scores)
    agent_wide = _resolve_project_path(paths.root, args.agent_wide)
    output = _resolve_project_path(paths.root, args.output)
    draw_output = _resolve_project_path(paths.root, args.draw_output)
    report = estimate_replication(
        baseline_dataset=baseline,
        tone_scores_csv=tone_scores,
        agent_wide_csv=agent_wide,
        output_json=output,
        draw_output_csv=draw_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_counterfactuals(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    manifest = _resolve_project_path(paths.root, args.manifest)
    output = _resolve_project_path(paths.root, args.output)
    runner = _build_runner(args, paths.logs_dir / "counterfactual_execution.jsonl")
    summary = execute_rewrite_manifest(
        manifest_path=manifest,
        output_jsonl=output,
        runner=runner,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        limit=args.limit,
        resume=not args.no_resume,
        max_retries=args.max_retries,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(summary.get(status, 0) for status in ("failed", "numeric_failed", "parse_failed", "usage_limited"))
    return 0 if bad == 0 else 1


def cmd_execute_counterfactuals_db(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    database = _resolve_project_path(paths.root, args.database)
    runner = _build_runner(args, None)
    summary = execute_rewrite_database(
        database_path=database,
        run_id=args.run_id,
        runner=runner,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        limit=args.limit,
        resume=not args.no_resume,
        max_retries=args.max_retries,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(summary.get(status, 0) for status in ("failed", "numeric_failed", "parse_failed", "usage_limited"))
    return 0 if bad == 0 else 1


def cmd_assemble_counterfactuals(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    results = _resolve_project_path(paths.root, args.results)
    output = _resolve_project_path(paths.root, args.output)
    df = assemble_counterfactual_corpus(
        results_jsonl=results,
        output_csv=output,
        required_stances=tuple(_split_csv(args.required_stances)),
        require_complete=not args.allow_incomplete,
        manifest_path=args.manifest,
    )
    print(json.dumps({"rows": len(df), "output": str(output), "columns": list(df.columns)}, ensure_ascii=False, indent=2))
    return 0


def cmd_evaluate_counterfactuals(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    results = _resolve_project_path(paths.root, args.results)
    output = _resolve_project_path(paths.root, args.output)
    details = _resolve_project_path(paths.root, args.details_output)
    summary = evaluate_counterfactual_results(results, output_json=output, details_csv=details,
                                              manifest_path=args.manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["acceptance_passed"] else 1


def cmd_prepare_semantic_tone(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    corpus = _resolve_project_path(paths.root, args.corpus)
    output = _resolve_project_path(paths.root, args.output)
    summary = build_semantic_manifest(corpus, output, seed=args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_semantic_tone(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    manifest = _resolve_project_path(paths.root, args.manifest)
    output = _resolve_project_path(paths.root, args.output)
    runner = _build_runner(args, paths.logs_dir / "semantic_tone_execution.jsonl")
    summary = execute_semantic_manifest(
        manifest_path=manifest,
        output_jsonl=output,
        runner=runner,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        limit=args.limit,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 and summary.get("parse_failed", 0) == 0 else 1


def cmd_prepare_pairwise_tone(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    corpus = _resolve_project_path(paths.root, args.corpus)
    output = _resolve_project_path(paths.root, args.output)
    summary = build_pairwise_manifest(corpus, output, seed=args.seed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_pairwise_tone(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    manifest = _resolve_project_path(paths.root, args.manifest)
    output = _resolve_project_path(paths.root, args.output)
    runner = _build_runner(args, paths.logs_dir / "pairwise_tone_execution.jsonl")
    summary = execute_pairwise_manifest(
        manifest_path=manifest,
        output_jsonl=output,
        runner=runner,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        limit=args.limit,
        resume=not args.no_resume,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


def cmd_build_dose_dataset(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    corpus = _resolve_project_path(paths.root, args.corpus)
    semantic = _resolve_project_path(paths.root, args.semantic_results)
    pairwise = _resolve_project_path(paths.root, args.pairwise_results) if args.pairwise_results else None
    output = _resolve_project_path(paths.root, args.output)
    df = build_realized_dose_dataset(corpus, semantic, pairwise, output)
    print(json.dumps({"rows": len(df), "output": str(output), "columns": list(df.columns)}, ensure_ascii=False, indent=2))
    return 0


def cmd_estimate_dose_response(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    dose_data = _resolve_project_path(paths.root, args.dose_data)
    output = _resolve_project_path(paths.root, args.output)
    report = estimate_dose_response(dose_data, output_json=output, outcome_column=args.outcome_column)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_execute_agents(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    manifest = _resolve_project_path(paths.root, args.manifest)
    output = _resolve_project_path(paths.root, args.output)
    runner = _build_runner(args, paths.logs_dir / "agent_execution.jsonl")
    summary = execute_agent_manifest(
        manifest_path=manifest,
        output_jsonl=output,
        runner=runner,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        temperature=args.temperature,
        limit=args.limit,
        resume=not args.no_resume,
        max_workers=args.max_workers,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    bad = sum(summary.get(status, 0) for status in ("failed", "parse_failed", "usage_limited"))
    return 0 if bad == 0 else 1


def cmd_score_minutes(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    minutes_dir = Path(args.minutes_dir) if args.minutes_dir else paths.minutes_dir
    output = Path(args.output) if args.output else paths.interim_data_dir / "tone" / "minutes_tone_scores.csv"
    if not output.is_absolute():
        output = paths.root / output
    df = score_minutes_folder(minutes_dir, output)
    print(json.dumps({"minutes": len(df), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def cmd_score_counterfactuals(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    input_csv = Path(args.input)
    output = Path(args.output)
    if not input_csv.is_absolute():
        input_csv = paths.root / input_csv
    if not output.is_absolute():
        output = paths.root / output
    df = score_counterfactual_minutes(input_csv, output_csv=output, drop_text=not args.keep_text)
    print(json.dumps({"rows": len(df), "columns": len(df.columns), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def cmd_summarize_data(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    folder = Path(args.folder)
    if not folder.is_absolute():
        folder = paths.root / folder
    df = summarize_data_dir(folder)
    output = Path(args.output) if args.output else paths.root / "docs" / "data_summary.csv"
    if not output.is_absolute():
        output = paths.root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)
    print(json.dumps({"tables": len(df), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


def cmd_estimate_baseline(args: argparse.Namespace) -> int:
    paths = load_paths(Path(args.root) if args.root else None)
    dataset = Path(args.dataset)
    output = Path(args.output)
    if not dataset.is_absolute():
        dataset = paths.root / dataset
    if not output.is_absolute():
        output = paths.root / output
    report = estimate_baseline(dataset, output_json=output)
    print(
        json.dumps(
            {
                "dataset": str(dataset),
                "output": str(output),
                "n_meetings": report["n_meetings"],
                "mixture_r2": report["mixture"]["r2"],
                "llm_fit_r2": report["llm_fit"]["r2"],
                "focus_tone_observational": report["focus_ar"]["beta_focus"],
                "focus_persistence": report["focus_ar"]["rho_focus"],
                "design_status": report["design_status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_run_pipeline(args: argparse.Namespace) -> int:
    report = run_safe_pipeline(
        root=Path(args.root) if args.root else None,
        max_meetings=args.max_meetings,
        rewrite_limit=args.rewrite_limit,
        agent_limit=args.agent_limit,
        output_json=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt-iv")
    parser.add_argument("--spec", help="Explicit specification. Alternatively set REPLICATION_SPEC_PATH.")
    parser.add_argument("--root", help="Project root. Defaults to auto-discovery.")
    sub = parser.add_subparsers(dest="command", required=True)

    smoke = sub.add_parser("smoke-test", help="Run a safe smoke test without real LLM calls.")
    smoke.set_defaults(func=cmd_smoke_test)

    score = sub.add_parser("score-text", help="Score text with the Prompt-IV lexicon.")
    score.add_argument("--file", help="UTF-8 text file to score.")
    score.add_argument("--text", help="Inline text to score.")
    score.set_defaults(func=cmd_score_text)

    check = sub.add_parser("llm-check", help="Check local Codex CLI availability and dry-run logging.")
    check.set_defaults(func=cmd_llm_check)

    backlog = sub.add_parser("extract-backlog", help="Extract D/T tasks from a revision-plan TeX file.")
    backlog.add_argument("--source", required=True)
    backlog.add_argument("--output", default="docs/backlog.yaml")
    backlog.set_defaults(func=cmd_extract_backlog)

    gen = sub.add_parser("generate-counterfactuals", help="Prepare a dry-run task manifest for counterfactual rewrites.")
    gen.add_argument("--minutes-dir", help="Defaults to data/raw/copom_minutes if available, otherwise legacy texto/atas.")
    gen.add_argument("--output", default="data/interim/counterfactuals/rewrite_task_manifest.jsonl")
    gen.add_argument("--stances", default="hawk,dove", help="Comma-separated stances: hawk,dove,neutral.")
    gen.add_argument("--iterations", type=int, default=1)
    gen.add_argument("--parts", type=int, default=5)
    gen.add_argument("--max-meetings", type=int, help="Limit the number of meetings for smoke planning.")
    gen.add_argument("--meeting-numbers", help="Comma-separated meeting numbers to include, e.g. 28,29.")
    gen.add_argument("--first-meeting", type=int, default=203)
    gen.add_argument("--last-meeting", type=int, default=281)
    gen.add_argument(
        "--prompt-version",
        default="production_v0.2",
        help=(
            "Rewrite prompt family: production_v0.2, legacy_series_v1, "
            "calibrated_contrast_v1, calibrated_contrast_v2, calibrated_contrast_v3, "
            "calibrated_length_v1, calibrated_register_v1, calibrated_dove_rebalance_v1, "
            "calibrated_dove_rebalance_v2, calibrated_symmetric_rebalance_v1, "
            "calibrated_symmetric_rebalance_v2, calibrated_symmetric_rebalance_v3, "
            "calibrated_symmetric_rebalance_v4, calibrated_symmetric_rebalance_v5, "
            "calibrated_symmetric_rebalance_v6, calibrated_symmetric_rebalance_v7, "
            "calibrated_symmetric_rebalance_v8, calibrated_symmetric_rebalance_v9, "
            "or calibrated_symmetric_rebalance_v10."
        ),
    )
    gen.set_defaults(func=cmd_generate_counterfactuals)

    agents = sub.add_parser("run-agents", help="Prepare a dry-run task manifest for recursive synthetic forecasters.")
    agents.add_argument("--minutes-dir", help="Defaults to data/raw/copom_minutes if available, otherwise legacy texto/atas.")
    agents.add_argument("--output", default="data/interim/agents/agent_task_manifest.jsonl")
    agents.add_argument("--forecaster-types", default="anchored,unanchored", help="Comma-separated: anchored,unanchored.")
    agents.add_argument("--num-agents", type=int, default=1)
    agents.add_argument("--initial-expectation", type=float, default=3.80)
    agents.add_argument("--random-seed", type=int, default=123)
    agents.add_argument("--max-meetings", type=int, help="Limit the number of meetings for smoke planning.")
    agents.set_defaults(func=cmd_run_agents)

    prior_prepare = sub.add_parser(
        "prepare-original-priors-db",
        help="Register forecast-only original-path prior tasks directly in SQLite.",
    )
    prior_prepare.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    prior_prepare.add_argument("--run-id", required=True)
    prior_prepare.add_argument("--first-meeting", type=int, default=203)
    prior_prepare.add_argument("--last-meeting", type=int, default=281)
    prior_prepare.add_argument("--meeting-numbers", help="Optional comma-separated meeting subset.")
    prior_prepare.add_argument("--forecaster-types", default="anchored,unanchored")
    prior_prepare.add_argument("--initial-expectation", type=float, default=3.80)
    prior_prepare.add_argument("--random-seed", type=int, default=20260712)
    prior_prepare.add_argument("--model", default="gpt-5.6-terra")
    prior_prepare.add_argument("--reasoning-effort", default="high")
    prior_prepare.add_argument("--temperature", type=float, default=1.0)
    prior_prepare.set_defaults(func=cmd_prepare_original_priors_db)

    prior_execute = sub.add_parser(
        "execute-original-priors-db",
        help="Execute sequential original-path priors and append every response directly to SQLite.",
    )
    prior_execute.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    prior_execute.add_argument("--run-id", required=True)
    prior_execute.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    prior_execute.add_argument("--allow-real-llm", action="store_true")
    prior_execute.add_argument("--timeout-seconds", type=int, default=300)
    prior_execute.add_argument("--max-workers", type=int, default=2)
    prior_execute.set_defaults(func=cmd_execute_original_priors_db)

    common_prepare = sub.add_parser(
        "prepare-common-prior-agents-db",
        help="Register independent common-prior treatment tasks directly in SQLite.",
    )
    common_prepare.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    common_prepare.add_argument("--run-id", required=True)
    common_prepare.add_argument("--source-run-id", required=True)
    common_prepare.add_argument("--prior-run-id", required=True)
    common_prepare.add_argument("--first-meeting", type=int, default=203)
    common_prepare.add_argument("--last-meeting", type=int, default=281)
    common_prepare.add_argument("--meeting-numbers", help="Optional comma-separated meeting subset.")
    common_prepare.add_argument("--iterations", required=True, help="Comma-separated source draw numbers.")
    common_prepare.add_argument("--roles", default="hawk,dove")
    common_prepare.add_argument("--forecaster-types", default="anchored,unanchored")
    common_prepare.add_argument("--random-seed", type=int, default=20260712)
    common_prepare.add_argument("--model", default="gpt-5.6-terra")
    common_prepare.add_argument("--reasoning-effort", default="high")
    common_prepare.add_argument("--temperature", type=float, default=1.0)
    common_prepare.set_defaults(func=cmd_prepare_common_prior_agents_db)

    common_execute = sub.add_parser(
        "execute-common-prior-agents-db",
        help="Execute independent treatment tasks and append every response directly to SQLite.",
    )
    common_execute.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    common_execute.add_argument("--run-id", required=True)
    common_execute.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    common_execute.add_argument("--allow-real-llm", action="store_true")
    common_execute.add_argument("--timeout-seconds", type=int, default=300)
    common_execute.add_argument("--limit", type=int)
    common_execute.add_argument("--iterations", help="Optional comma-separated draw filter.")
    common_execute.add_argument("--max-workers", type=int, default=4)
    common_execute.add_argument("--no-resume", action="store_true")
    common_execute.set_defaults(func=cmd_execute_common_prior_agents_db)

    common_audit = sub.add_parser(
        "audit-common-prior-db",
        help="Audit prompt, source, pairing, prior, revision, and runner invariants in SQLite.",
    )
    common_audit.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    common_audit.add_argument("--run-id", required=True)
    common_audit.add_argument("--prior-run-id")
    common_audit.add_argument("--iterations", help="Audit only this comma-separated draw gate.")
    common_audit.set_defaults(func=cmd_audit_common_prior_db)
    original_audit = sub.add_parser("audit-original-priors-db", help="Audit the original chronological trajectories.")
    original_audit.add_argument("--database", required=True)
    original_audit.add_argument("--run-id", required=True)
    original_audit.set_defaults(func=cmd_audit_original_priors_db)

    arm_prior_prepare = sub.add_parser(
        "prepare-arm-specific-priors-db",
        help=(
            "Register recursive trajectories in which every textual arm carries "
            "its own lagged forecast prior."
        ),
    )
    arm_prior_prepare.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    arm_prior_prepare.add_argument("--run-id", required=True)
    arm_prior_prepare.add_argument("--source-run-id", required=True)
    arm_prior_prepare.add_argument("--first-meeting", type=int, default=203)
    arm_prior_prepare.add_argument("--last-meeting", type=int, default=281)
    arm_prior_prepare.add_argument("--meeting-numbers", help="Optional comma-separated meeting subset.")
    arm_prior_prepare.add_argument("--iterations", required=True, help="Comma-separated source draw numbers.")
    arm_prior_prepare.add_argument(
        "--roles",
        default="dove_high,dove_low,original,neutral,hawk_low,hawk_high",
    )
    arm_prior_prepare.add_argument("--forecaster-types", default="anchored,unanchored")
    arm_prior_prepare.add_argument("--initial-expectation", type=float, default=3.80)
    arm_prior_prepare.add_argument("--random-seed", type=int, default=20260714)
    arm_prior_prepare.add_argument("--model", default="gpt-5.6-terra")
    arm_prior_prepare.add_argument("--reasoning-effort", default="high")
    arm_prior_prepare.add_argument("--temperature", type=float, default=1.0)
    arm_prior_prepare.set_defaults(func=cmd_prepare_arm_specific_priors_db)

    arm_prior_execute = sub.add_parser(
        "execute-arm-specific-priors-db",
        help="Execute arm-specific recursive paths and append every result directly to SQLite.",
    )
    arm_prior_execute.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    arm_prior_execute.add_argument("--run-id", required=True)
    arm_prior_execute.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    arm_prior_execute.add_argument("--allow-real-llm", action="store_true")
    arm_prior_execute.add_argument("--timeout-seconds", type=int, default=300)
    arm_prior_execute.add_argument("--max-workers", type=int, default=2)
    arm_prior_execute.set_defaults(func=cmd_execute_arm_specific_priors_db)

    arm_prior_audit = sub.add_parser(
        "audit-arm-specific-priors-db",
        help="Audit recursive continuity, pairing, source hashes, and prompt provenance.",
    )
    arm_prior_audit.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    arm_prior_audit.add_argument("--run-id", required=True)
    arm_prior_audit.set_defaults(func=cmd_audit_arm_specific_priors_db)

    repl = sub.add_parser("prepare-replication", help="Create an isolated replication run directory and rewrite manifest.")
    repl.add_argument("--run-id", required=True)
    repl.add_argument("--run-directory", required=True)
    repl.add_argument("--meeting-numbers")
    repl.add_argument("--first-meeting", type=int, default=203)
    repl.add_argument("--last-meeting", type=int, default=281)
    repl.add_argument("--minutes-dir", help="Defaults to data/raw/copom_minutes if available, otherwise legacy texto/atas.")
    repl.add_argument("--stances", default="hawk,dove")
    repl.add_argument("--iterations", type=int, default=5)
    repl.add_argument("--parts", type=int, default=5)
    repl.add_argument("--max-meetings", type=int, help="Limit meetings for a pilot replication.")
    repl.add_argument("--model", default="gpt-5.6-terra")
    repl.add_argument("--reasoning-effort", default="high")
    repl.add_argument("--temperature", type=float, default=1.0)
    repl.add_argument(
        "--prompt-version",
        default="production_v0.2",
        help=(
            "Rewrite prompt family: production_v0.2, legacy_series_v1, "
            "calibrated_contrast_v1, calibrated_contrast_v2, calibrated_contrast_v3, "
            "calibrated_length_v1, calibrated_register_v1, calibrated_dove_rebalance_v1, "
            "calibrated_dove_rebalance_v2, calibrated_symmetric_rebalance_v1, "
            "calibrated_symmetric_rebalance_v2, calibrated_symmetric_rebalance_v3, "
            "calibrated_symmetric_rebalance_v4, calibrated_symmetric_rebalance_v5, "
            "calibrated_symmetric_rebalance_v6, calibrated_symmetric_rebalance_v7, "
            "calibrated_symmetric_rebalance_v8, calibrated_symmetric_rebalance_v9, "
            "or calibrated_symmetric_rebalance_v10."
        ),
    )
    repl.set_defaults(func=cmd_prepare_replication)

    cf_agents = sub.add_parser("prepare-counterfactual-agents", help="Build agent manifest over assembled counterfactual texts.")
    cf_agents.add_argument("--corpus", required=True)
    cf_agents.add_argument("--output", required=True)
    cf_agents.add_argument("--text-dir", required=True)
    cf_agents.add_argument("--scenarios", default="hawk,dove")
    cf_agents.add_argument("--forecaster-types", default="anchored,unanchored")
    cf_agents.add_argument("--num-agents", type=int, default=1)
    cf_agents.add_argument("--initial-expectation", type=float, default=3.80)
    cf_agents.add_argument("--random-seed", type=int, default=123)
    cf_agents.set_defaults(func=cmd_prepare_counterfactual_agents)

    assemble_cf_agents = sub.add_parser(
        "assemble-counterfactual-agents",
        help="Collapse completed counterfactual agent JSONL results into long and wide CSVs.",
    )
    assemble_cf_agents.add_argument("--results", required=True)
    assemble_cf_agents.add_argument("--long-output", required=True)
    assemble_cf_agents.add_argument("--wide-output", required=True)
    assemble_cf_agents.set_defaults(func=cmd_assemble_counterfactual_agents)

    estimate_repl = sub.add_parser("estimate-replication", help="Estimate Prompt-IV on replicated counterfactual draws.")
    estimate_repl.add_argument("--baseline-dataset", default="outputs/work/calibration_data.dta")
    estimate_repl.add_argument("--tone-scores", required=True)
    estimate_repl.add_argument("--agent-wide", required=True)
    estimate_repl.add_argument("--output", required=True)
    estimate_repl.add_argument("--draw-output", required=True)
    estimate_repl.set_defaults(func=cmd_estimate_replication)

    exec_cf = sub.add_parser("execute-counterfactuals", help="Consume a rewrite manifest with resume/idempotent JSONL output.")
    exec_cf.add_argument("--manifest", default="data/interim/counterfactuals/rewrite_task_manifest.jsonl")
    exec_cf.add_argument("--output", default="data/interim/counterfactuals/rewrite_results.jsonl")
    exec_cf.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    exec_cf.add_argument("--allow-real-llm", action="store_true", help="Required with --runner codex to permit real model calls.")
    exec_cf.add_argument("--model", default="gpt-5.6-terra")
    exec_cf.add_argument("--reasoning-effort", default="high")
    exec_cf.add_argument("--temperature", type=float, default=1.0)
    exec_cf.add_argument("--timeout-seconds", type=int, default=300)
    exec_cf.add_argument("--limit", type=int, help="Maximum pending tasks to consume.")
    exec_cf.add_argument("--max-retries", type=int, default=2, help="Numeric-preservation retries per task.")
    exec_cf.add_argument("--max-workers", type=int, default=1, help="Parallel rewrite workers for independent block tasks.")
    exec_cf.add_argument("--no-resume", action="store_true", help="Do not skip task IDs already present in the output JSONL.")
    exec_cf.set_defaults(func=cmd_execute_counterfactuals)

    exec_cf_db = sub.add_parser(
        "execute-counterfactuals-db",
        help="Consume rewrite tasks from SQLite and write rewrite results directly to SQLite.",
    )
    exec_cf_db.add_argument("--database", default="data/interim/prompt_iv.sqlite")
    exec_cf_db.add_argument("--run-id", required=True)
    exec_cf_db.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    exec_cf_db.add_argument("--allow-real-llm", action="store_true", help="Required with --runner codex to permit real model calls.")
    exec_cf_db.add_argument("--model", default="gpt-5.6-terra")
    exec_cf_db.add_argument("--reasoning-effort", default="high")
    exec_cf_db.add_argument("--temperature", type=float, default=1.0)
    exec_cf_db.add_argument("--timeout-seconds", type=int, default=300)
    exec_cf_db.add_argument("--limit", type=int, help="Maximum pending tasks to consume.")
    exec_cf_db.add_argument("--max-retries", type=int, default=2, help="Numeric-preservation retries per task.")
    exec_cf_db.add_argument("--max-workers", type=int, default=1, help="Parallel rewrite workers for independent block tasks.")
    exec_cf_db.add_argument("--no-resume", action="store_true", help="Do not skip task IDs already present in the database.")
    exec_cf_db.set_defaults(func=cmd_execute_counterfactuals_db)

    assemble_cf = sub.add_parser("assemble-counterfactuals", help="Assemble completed rewrite block results into a meeting-level CSV corpus.")
    assemble_cf.add_argument("--manifest", required=True)
    assemble_cf.add_argument("--results", default="data/interim/counterfactuals/rewrite_results.jsonl")
    assemble_cf.add_argument("--output", default="data/interim/counterfactuals/counterfactual_corpus.csv")
    assemble_cf.add_argument("--required-stances", default="hawk,dove")
    assemble_cf.add_argument("--allow-incomplete", action="store_true")
    assemble_cf.set_defaults(func=cmd_assemble_counterfactuals)

    eval_cf = sub.add_parser("evaluate-counterfactuals", help="Evaluate real counterfactual rewrite outputs for numeric and tone quality.")
    eval_cf.add_argument("--manifest", required=True)
    eval_cf.add_argument("--results", default="data/interim/counterfactuals/rewrite_results.jsonl")
    eval_cf.add_argument("--output", default="outputs/logs/counterfactual_quality_report.json")
    eval_cf.add_argument("--details-output", default="outputs/logs/counterfactual_quality_details.csv")
    eval_cf.set_defaults(func=cmd_evaluate_counterfactuals)

    sem_manifest = sub.add_parser("prepare-semantic-tone", help="Prepare blinded text-level semantic tone classification manifest.")
    sem_manifest.add_argument("--corpus", default="data/interim/counterfactuals/counterfactual_corpus.csv")
    sem_manifest.add_argument("--output", default="data/interim/dose_response/semantic_tone_manifest.jsonl")
    sem_manifest.add_argument("--seed", type=int, default=123)
    sem_manifest.set_defaults(func=cmd_prepare_semantic_tone)

    sem_exec = sub.add_parser("execute-semantic-tone", help="Execute blinded semantic hawkish/dovish tone classification.")
    sem_exec.add_argument("--manifest", default="data/interim/dose_response/semantic_tone_manifest.jsonl")
    sem_exec.add_argument("--output", default="data/interim/dose_response/semantic_tone_results.jsonl")
    sem_exec.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    sem_exec.add_argument("--allow-real-llm", action="store_true", help="Required with --runner codex to permit real model calls.")
    sem_exec.add_argument("--model", default="gpt-5.6-terra")
    sem_exec.add_argument("--reasoning-effort", default="high")
    sem_exec.add_argument("--temperature", type=float, default=1.0)
    sem_exec.add_argument("--timeout-seconds", type=int, default=300)
    sem_exec.add_argument("--limit", type=int)
    sem_exec.add_argument("--no-resume", action="store_true")
    sem_exec.set_defaults(func=cmd_execute_semantic_tone)

    pair_manifest = sub.add_parser("prepare-pairwise-tone", help="Prepare blinded pairwise hawkishness classification manifest.")
    pair_manifest.add_argument("--corpus", default="data/interim/counterfactuals/counterfactual_corpus.csv")
    pair_manifest.add_argument("--output", default="data/interim/dose_response/pairwise_tone_manifest.jsonl")
    pair_manifest.add_argument("--seed", type=int, default=456)
    pair_manifest.set_defaults(func=cmd_prepare_pairwise_tone)

    pair_exec = sub.add_parser("execute-pairwise-tone", help="Execute blinded pairwise hawkishness classification.")
    pair_exec.add_argument("--manifest", default="data/interim/dose_response/pairwise_tone_manifest.jsonl")
    pair_exec.add_argument("--output", default="data/interim/dose_response/pairwise_tone_results.jsonl")
    pair_exec.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    pair_exec.add_argument("--allow-real-llm", action="store_true", help="Required with --runner codex to permit real model calls.")
    pair_exec.add_argument("--model", default="gpt-5.6-terra")
    pair_exec.add_argument("--reasoning-effort", default="high")
    pair_exec.add_argument("--temperature", type=float, default=1.0)
    pair_exec.add_argument("--timeout-seconds", type=int, default=300)
    pair_exec.add_argument("--limit", type=int)
    pair_exec.add_argument("--no-resume", action="store_true")
    pair_exec.set_defaults(func=cmd_execute_pairwise_tone)

    dose_data = sub.add_parser("build-dose-dataset", help="Build realized dose dataset from lexicon, semantic, and pairwise tone scores.")
    dose_data.add_argument("--corpus", default="data/interim/counterfactuals/counterfactual_corpus.csv")
    dose_data.add_argument("--semantic-results", default="data/interim/dose_response/semantic_tone_results.jsonl")
    dose_data.add_argument("--pairwise-results", default="data/interim/dose_response/pairwise_tone_results.jsonl")
    dose_data.add_argument("--output", default="data/interim/dose_response/realized_dose_dataset.csv")
    dose_data.set_defaults(func=cmd_build_dose_dataset)

    dose_est = sub.add_parser("estimate-dose-response", help="Estimate first-stage and optional outcome dose-response models.")
    dose_est.add_argument("--dose-data", default="data/interim/dose_response/realized_dose_dataset.csv")
    dose_est.add_argument("--output", default="outputs/tables/dose_response_report.json")
    dose_est.add_argument("--outcome-column", help="Optional outcome column in the dose dataset.")
    dose_est.set_defaults(func=cmd_estimate_dose_response)

    exec_agents = sub.add_parser("execute-agents", help="Consume a synthetic-forecaster manifest with resume/idempotent JSONL output.")
    exec_agents.add_argument("--manifest", default="data/interim/agents/agent_task_manifest.jsonl")
    exec_agents.add_argument("--output", default="data/interim/agents/agent_results.jsonl")
    exec_agents.add_argument("--runner", choices=["dry-run", "codex"], default="dry-run")
    exec_agents.add_argument("--allow-real-llm", action="store_true", help="Required with --runner codex to permit real model calls.")
    exec_agents.add_argument("--model", default="gpt-5.6-terra")
    exec_agents.add_argument("--reasoning-effort", default="high")
    exec_agents.add_argument("--temperature", type=float, default=1.0)
    exec_agents.add_argument("--timeout-seconds", type=int, default=300)
    exec_agents.add_argument("--limit", type=int, help="Maximum pending tasks to consume.")
    exec_agents.add_argument("--max-workers", type=int, default=1, help="Parallel workers across independent agent trajectories.")
    exec_agents.add_argument("--no-resume", action="store_true", help="Do not skip task IDs already present in the output JSONL.")
    exec_agents.set_defaults(func=cmd_execute_agents)

    sync_db = sub.add_parser(
        "sync-database",
        help="Build or refresh the project SQLite database from minutes, manifests, results, corpus, and tone CSVs.",
    )
    sync_db.add_argument("--output", default="data/interim/prompt_iv.sqlite")
    sync_db.add_argument("--minutes-dir", help="Defaults to the organized raw Copom minutes folder.")
    sync_db.add_argument(
        "--run-dir",
        action="append",
        default=[],
        help="Replication run directory to ingest. Repeatable; defaults to every data/interim/replications/* directory.",
    )
    sync_db.add_argument("--minutes-only", action="store_true", help="Bootstrap only original texts and hashes.")
    sync_db.add_argument("--replace", action="store_true", help="Unsupported for prospective collection.")
    sync_db.set_defaults(func=cmd_sync_database)

    score_minutes = sub.add_parser("score-minutes", help="Score all Copom minute text files.")
    score_minutes.add_argument("--minutes-dir", help="Defaults to data/raw/copom_minutes.")
    score_minutes.add_argument("--output", help="Defaults to data/interim/tone/minutes_tone_scores.csv.")
    score_minutes.set_defaults(func=cmd_score_minutes)

    score_cf = sub.add_parser("score-counterfactuals", help="Score original/hawk/dove text columns in a counterfactual CSV.")
    score_cf.add_argument("--input", default="data/interim/counterfactuals/corpus_counterfactual_atas_gpt55.csv")
    score_cf.add_argument("--output", default="data/interim/tone/counterfactual_tone_scores.csv")
    score_cf.add_argument("--keep-text", action="store_true")
    score_cf.set_defaults(func=cmd_score_counterfactuals)

    summarize = sub.add_parser("summarize-data", help="Summarize copied CSV/DTA/XLS(X) files.")
    summarize.add_argument("--folder", default="data")
    summarize.add_argument("--output", default="docs/data_summary.csv")
    summarize.set_defaults(func=cmd_summarize_data)

    estimate = sub.add_parser("estimate-baseline", help="Estimate the Terra 5.6 profile calibration and observational Focus AR benchmark.")
    estimate.add_argument("--dataset", default="outputs/work/calibration_data.dta")
    estimate.add_argument("--output", default="outputs/tables/baseline_estimates.json")
    estimate.set_defaults(func=cmd_estimate_baseline)

    pipeline = sub.add_parser("run-pipeline", help="Run the refactored safe pipeline without real LLM collection.")
    pipeline.add_argument("--max-meetings", type=int, default=2)
    pipeline.add_argument("--rewrite-limit", type=int, default=2)
    pipeline.add_argument("--agent-limit", type=int, default=2)
    pipeline.add_argument("--output", default="outputs/logs/safe_pipeline_report.json")
    pipeline.set_defaults(func=cmd_run_pipeline)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.spec:
        os.environ["REPLICATION_SPEC_PATH"] = str(Path(args.spec).resolve(strict=True))
    if args.command == "score-text":
        return args.func(args)
    spec = load_spec(source_root=args.root, require_frozen=True)
    args.root = spec["source_root"]
    if hasattr(args, "model") and (args.model, args.reasoning_effort) != (spec["model"], spec["reasoning_effort"]):
        raise ValueError("Requested model/effort do not match the specification")
    if hasattr(args, "run_id") and args.run_id not in spec["run_ids"].values():
        raise ValueError("Run ID is outside the selected cohort")
    # CLI collection and audits use the mutable warehouse. Frozen analysis
    # databases are read by the separate read-only analytical consumers.
    artifact_fields = ("output", "details_output", "long_output", "wide_output", "text_dir",
        "database", "manifest", "results", "corpus", "input", "semantic_results",
        "pairwise_results", "dose_data", "tone_scores", "agent_wide", "baseline_dataset",
        "dataset", "run_directory")
    for field in artifact_fields:
        value = getattr(args, field, None)
        if value:
            setattr(args, field, str(validate_artifact_path(value, spec, allow_frozen=False)))
    for value in getattr(args, "run_dir", []):
        validate_artifact_path(value, spec)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
