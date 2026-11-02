from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "collection" / "src"
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
from prompt_iv.paths import load_spec, validate_artifact_path




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or resume the selected common-prior draw gate.")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--prior-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--allow-real-llm", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.environ["REPLICATION_SPEC_PATH"] = str(Path(args.spec).resolve(strict=True))
    spec = load_spec(source_root=ROOT, require_frozen=True)
    MEETINGS = tuple(spec["sample"]["meeting_numbers"])
    FULL_DRAWS = tuple(range(1, spec["draws"]["main"] + 1))
    GATE_DRAWS = tuple(range(1, min(5, spec["draws"]["main"]) + 1))
    database = validate_artifact_path(args.database, spec)
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

    print(f"Preparing and resuming {len(MEETINGS) * 2} original-prior tasks...", flush=True)
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
