from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from prompt_iv.agents.common_prior import (
    audit_common_prior_run,
    audit_original_prior_run,
    execute_common_prior_run,
    execute_original_prior_run,
    prepare_common_prior_run,
    prepare_original_prior_run,
)
from prompt_iv.llm.runner import CodexCliRunner


def _csv_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _csv_text(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded common-prior collection smoke test.")
    parser.add_argument("--database", default=str(ROOT / "data" / "interim" / "prompt_iv.sqlite"))
    parser.add_argument("--source-run-id", default="v10_referee_d5_25draws_20260620")
    parser.add_argument("--prior-run-id", default="common_prior_original_smoke_20260712")
    parser.add_argument("--run-id", default="common_prior_treatment_smoke_20260712")
    parser.add_argument("--meetings", default="203,204")
    parser.add_argument("--iterations", default="1,6")
    parser.add_argument("--roles", default="original,hawk,dove")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--allow-real-llm", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Path(args.database).resolve()
    meetings = _csv_ints(args.meetings)
    iterations = _csv_ints(args.iterations)
    roles = _csv_text(args.roles)
    runner = CodexCliRunner(
        allow_real_llm=args.allow_real_llm,
        log_path=None,
        timeout_seconds=args.timeout_seconds,
    )

    report: dict[str, object] = {
        "scope": {
            "database": str(database),
            "source_run_id": args.source_run_id,
            "meetings": meetings,
            "iterations": iterations,
            "roles": roles,
            "forecaster_types": ["anchored", "unanchored"],
            "maximum_real_calls": len(meetings) * 2 + len(meetings) * len(iterations) * len(roles) * 2,
        }
    }
    report["prepare_priors"] = prepare_original_prior_run(
        database,
        args.prior_run_id,
        meeting_numbers=meetings,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    report["execute_priors"] = execute_original_prior_run(
        database,
        args.prior_run_id,
        runner=runner,
        max_workers=2,
    )
    prior_audit = audit_original_prior_run(database, args.prior_run_id)
    report["audit_priors"] = prior_audit
    if not prior_audit["ready"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    report["prepare_treatments"] = prepare_common_prior_run(
        database,
        args.run_id,
        source_run_id=args.source_run_id,
        prior_run_id=args.prior_run_id,
        meeting_numbers=meetings,
        iterations=iterations,
        roles=roles,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    report["execute_treatments"] = execute_common_prior_run(
        database,
        args.run_id,
        runner=runner,
        max_workers=4,
    )
    treatment_audit = audit_common_prior_run(database, args.run_id)
    report["audit_treatments"] = treatment_audit
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if treatment_audit["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
