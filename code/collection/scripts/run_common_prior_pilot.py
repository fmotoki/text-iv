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
    audit_common_prior_gate,
    audit_original_prior_run,
    execute_common_prior_run,
    execute_original_prior_run,
    prepare_common_prior_run,
    prepare_original_prior_run,
)
from prompt_iv.llm.runner import CodexCliRunner


MEETINGS = tuple(range(203, 274))
FULL_DRAWS = tuple(range(1, 26))
GATE_DRAWS = tuple(range(1, 6))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or resume the 1,420-call common-prior gate.")
    parser.add_argument("--database", default=str(ROOT / "data" / "interim" / "prompt_iv.sqlite"))
    parser.add_argument("--source-run-id", default="v10_referee_d5_25draws_20260620")
    parser.add_argument("--prior-run-id", default="common_prior_original_v1_20260712")
    parser.add_argument("--run-id", default="common_prior_main_25draws_v1_20260712")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--allow-real-llm", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Path(args.database).resolve()
    runner = CodexCliRunner(
        allow_real_llm=args.allow_real_llm,
        log_path=None,
        timeout_seconds=args.timeout_seconds,
    )
    report: dict[str, object] = {
        "scope": {
            "database": str(database),
            "source_run_id": args.source_run_id,
            "meetings": [MEETINGS[0], MEETINGS[-1]],
            "registered_draws": [FULL_DRAWS[0], FULL_DRAWS[-1]],
            "executed_gate_draws": list(GATE_DRAWS),
            "roles": ["hawk", "dove"],
            "forecaster_types": ["anchored", "unanchored"],
            "prior_calls": len(MEETINGS) * 2,
            "gate_calls": len(MEETINGS) * len(GATE_DRAWS) * 2 * 2,
        }
    }

    print("Preparing and resuming the 142-call original-prior trajectories...", flush=True)
    report["prepare_priors"] = prepare_original_prior_run(
        database,
        args.prior_run_id,
        meeting_numbers=MEETINGS,
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
        return 2

    print("Registering the frozen 25-draw plan and executing draws 1-5...", flush=True)
    report["prepare_gate"] = prepare_common_prior_run(
        database,
        args.run_id,
        source_run_id=args.source_run_id,
        prior_run_id=args.prior_run_id,
        meeting_numbers=MEETINGS,
        iterations=FULL_DRAWS,
        roles=("hawk", "dove"),
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        requested_temperature=args.temperature,
    )
    report["execute_gate"] = execute_common_prior_run(
        database,
        args.run_id,
        runner=runner,
        iterations=GATE_DRAWS,
        max_workers=4,
    )
    gate_audit = audit_common_prior_gate(database, args.run_id, iterations=GATE_DRAWS)
    report["audit_gate"] = gate_audit
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if gate_audit["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
