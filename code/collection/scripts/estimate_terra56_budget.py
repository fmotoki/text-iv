"""Project the planned workload and optionally run four QA request families.

Generated-text lengths and future reasoning are unknown. Offline sizing uses
original minute lengths. Observed CLI usage supplies per-family overhead and
output-token examples. This script never runs the production collector.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--spec", required=True, type=Path)
parser.add_argument("--production-spec", type=Path, default=SOURCE_ROOT / "code/replication_spec.json")
parser.add_argument("--tokenizer-library", type=Path)
parser.add_argument("--codex")
parser.add_argument("--allow-remote-calls", action="store_true")
args = parser.parse_args()
sys.path.insert(0, str(SOURCE_ROOT / "code/collection/src"))
if args.tokenizer_library:
    sys.path.insert(0, str(args.tokenizer_library.resolve(strict=True)))

from prompt_iv.paths import load_spec, validate_artifact_path
from prompt_iv.generation.tasks import build_rewrite_tasks
from prompt_iv.generation.execution import _build_request_from_manifest, _execute_one_rewrite_task
from prompt_iv.agents.simulation import build_agent_request, parse_agent_decision
from prompt_iv.agents.targets import inflation_target_for_meeting
from prompt_iv.io.minutes import segment_text
from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.llm.runner import CodexCliRunner, LLMResponse

os.environ["REPLICATION_SPEC_PATH"] = str(args.spec.resolve(strict=True))
spec = load_spec(source_root=SOURCE_ROOT, require_frozen=True)
if spec["design_type"] != "qa_smoke" or spec["sample"]["meeting_numbers"] != [203]:
    raise ValueError("Budget live QA requires its own one-meeting-203 qa_smoke specification")
if spec.get("service_tier") != "default":
    raise ValueError("This measurement uses the user-selected explicit Standard tier")
root = Path(spec["run_root"])
out = validate_artifact_path(root / "outputs/budget", spec)
out.mkdir(parents=True, exist_ok=True)
summary_path = out / "budget_summary.json"
if summary_path.exists():
    saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if saved_summary["spec_sha256"] != spec["_spec_sha256"] or saved_summary["fingerprint"] != spec["fingerprint"]:
        raise ValueError("Completed QA belongs to another configuration")
    print(json.dumps({"already_completed": True, "additional_requests": 0, "summary": str(summary_path)}), flush=True)
    raise SystemExit(0)
os.environ["TIKTOKEN_CACHE_DIR"] = str(validate_artifact_path(root / "data/interim/tokenizer_cache", spec))
import tiktoken

encoder = tiktoken.encoding_for_model(spec["model"])
production = json.loads(args.production_spec.read_text(encoding="utf-8-sig"))
if production["sample"]["meeting_numbers"] != list(range(203, 282)):
    raise ValueError("The full-run sizing target must remain the exact 79-meeting design")
if (production["model"], production["reasoning_effort"], production["service_tier"]) != (
        spec["model"], spec["reasoning_effort"], spec["service_tier"]):
    raise ValueError("Sizing and QA model, reasoning and speed must agree")

# Import the unchanged dedicated scoring prompts without invoking its CLI.
module_path = SOURCE_ROOT / "code/collection/scripts/run_v10_referee_step3_blinded_validation.py"
module_spec = importlib.util.spec_from_file_location("terra56_budget_scorer", module_path)
scorer = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = scorer
module_spec.loader.exec_module(scorer)

main_tasks = build_rewrite_tasks(SOURCE_ROOT / "data/raw/copom_minutes",
    meeting_numbers=production["sample"]["meeting_numbers"], iterations=1, parts=production["blocks"],
    stances=production["roles"]["main"], prompt_version=production["prompt_versions"]["rewrite"])
dose_tasks = build_rewrite_tasks(SOURCE_ROOT / "data/raw/copom_minutes",
    meeting_numbers=production["sample"]["meeting_numbers"], iterations=1, parts=production["blocks"],
    stances=production["roles"]["dose_generated"], prompt_version=production["prompt_versions"]["rewrite"])
texts = {task.meeting_number: Path(task.source_path).read_text(encoding="utf-8") for task in main_tasks}
if sorted(texts) != list(range(203, 282)):
    raise ValueError("The sizing minute set is incomplete")
planned = {}
for name, tasks, draws in (("rewrite_main", main_tasks, production["draws"]["main"]),
                           ("rewrite_dose", dose_tasks, production["draws"]["dose"])):
    planned[name] = {"requests": len(tasks) * draws,
        "visible_tokens": draws * sum(len(encoder.encode(task.system_prompt + "\n\n" + task.user_prompt)) for task in tasks),
        "sample_family": "rewrite"}
forecast_tokens = 0
semantic_tokens = 0
pairwise_tokens = 0
for meeting, text in sorted(texts.items()):
    for profile in production["profiles"]:
        # The prior is a sizing placeholder, not a forecast or empirical record.
        request = build_agent_request(text, profile, 3.80, inflation_target_for_meeting(meeting),
            20260712, meeting, model=spec["model"], reasoning_effort=spec["reasoning_effort"],
            prompt_version=spec["prompt_versions"]["common_prior"])
        forecast_tokens += len(encoder.encode(request.system_prompt + "\n\n" + request.user_prompt))
    record = {"anonymous_id": "QA_SIZE_" + str(meeting), "text": text, "task_id": "size_sem_" + str(meeting),
              "prompt_version": spec["prompt_versions"]["semantic"],
              "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    request = scorer.semantic_request(record, spec["model"], spec["reasoning_effort"], 1.0)
    semantic_tokens += len(encoder.encode(request.system_prompt + "\n\n" + request.user_prompt))
    pair_record = {"anonymous_pair_id": "QA_SIZE_PAIR_" + str(meeting), "text_a": text, "text_b": text,
                   "task_id": "size_pair_" + str(meeting), "prompt_version": spec["prompt_versions"]["pairwise"]}
    request = scorer.pairwise_request(pair_record, spec["model"], spec["reasoning_effort"], 1.0)
    pairwise_tokens += len(encoder.encode(request.system_prompt + "\n\n" + request.user_prompt))
for name, multiplier in (("original_forecast", 1), ("main_forecast", production["draws"]["main"] * 2),
                         ("dose_common_forecast", production["draws"]["dose"] * 6),
                         ("dose_recursive_forecast", production["draws"]["dose"] * 6)):
    planned[name] = {"requests": 79 * 2 * multiplier, "visible_tokens": forecast_tokens * multiplier,
                     "sample_family": "forecast"}
planned["semantic"] = {"requests": 79 * production["draws"]["dose"] * 6,
    "visible_tokens": semantic_tokens * production["draws"]["dose"] * 6, "sample_family": "semantic"}
planned["pairwise"] = {"requests": 79 * production["draws"]["dose"] * 5,
    "visible_tokens": pairwise_tokens * production["draws"]["dose"] * 5, "sample_family": "pairwise"}
if sum(row["requests"] for row in planned.values()) != 51508:
    raise ValueError("Full-run first-attempt dimensions changed")
projection = {"spec_sha256": spec["_spec_sha256"], "fingerprint": spec["fingerprint"],
    "tokenizer": encoder.name, "tiktoken_version": tiktoken.__version__, "planned": planned,
    "sizing_proxy": "original minute text for future generated-text length; prior 3.80 for sizing only"}
projection_path = out / "visible_token_projection.json"
if projection_path.exists():
    if json.loads(projection_path.read_text(encoding="utf-8")) != projection:
        raise ValueError("A changed sizing configuration needs a new QA root")
else:
    with projection_path.open("x", encoding="utf-8") as handle:
        json.dump(projection, handle, ensure_ascii=False, indent=2)
print("Offline first-attempt sizing complete. 51,508 planned requests.", flush=True)
if not args.allow_remote_calls:
    raise SystemExit(0)
if not args.codex or not spec.get("execution", {}).get("allow_remote_calls"):
    raise ValueError("Live QA requires an explicit executable, switch and QA authorization")
if any(os.environ.get(name) for name in ("CODEX_API_KEY", "OPENAI_API_KEY")):
    raise ValueError("The subscription measurement requires ChatGPT sign-in without API-key environment overrides")

# This is a single-operator QA program, not a parallel collection launcher.
# Telemetry starts no model turns. Keep the parent agent idle during this interval.
telemetry = SOURCE_ROOT / "code/collection/scripts/read_codex_usage.py"
before = out / "weekly_before.json"
after = out / "weekly_after.json"
if not before.exists():
    subprocess.run([sys.executable, str(telemetry), "--spec", str(args.spec), "--codex", args.codex,
                    "--service-tier", "default", "--output", str(before)], check=True,
                   stdout=subprocess.DEVNULL)
account_before = json.loads(before.read_text(encoding="utf-8"))
if account_before.get("authentication_type") != "chatgpt":
    raise ValueError("This is a Pro subscription measurement. Codex must report ChatGPT authentication.")
request_log = out / "requests.jsonl"
runner = CodexCliRunner(command=args.codex, allow_real_llm=True, log_path=request_log, timeout_seconds=300, max_attempts=6)
attempt_dir = request_log.with_suffix(".attempts")
visible_by_hash = {}
selected = next(task for task in main_tasks if task.meeting_number == 203 and task.stance == "hawk" and task.block_index == 1)
task = selected.manifest_record()
initial_request, source_block = _build_request_from_manifest(task, spec["model"], spec["reasoning_effort"], 1.0)
visible_by_hash[initial_request.input_hash] = len(encoder.encode(initial_request.system_prompt + "\n\n" + initial_request.user_prompt))
rewrite_cache = out / "rewrite.json"
if rewrite_cache.exists():
    saved = json.loads(rewrite_cache.read_text(encoding="utf-8"))
    if saved["spec_sha256"] != spec["_spec_sha256"] or saved["manifest_hash"] != task["input_hash"]:
        raise ValueError("Rewrite QA cache configuration changed")
    rewrite = saved["record"]
else:
    attempts = len(list(attempt_dir.glob("attempt_*.json")))
    if attempts > 2:
        raise ValueError("The six-attempt QA cap cannot cover the remaining required families")
    rewrite, stats = _execute_one_rewrite_task(task, runner, spec["model"], spec["reasoning_effort"], 1.0, True, min(2, 2-attempts))
    if rewrite["status"] != "completed" or not rewrite["numeric_ok"] or rewrite["runner_dry_run"]:
        raise RuntimeError("Rewrite QA failed. Retain request evidence and repair only the affected cell.")
    with rewrite_cache.open("x", encoding="utf-8") as handle:
        json.dump({"spec_sha256": spec["_spec_sha256"], "manifest_hash": task["input_hash"],
                   "record": rewrite, "statistics": stats}, handle, ensure_ascii=False, indent=2)
if (rewrite["status"] != "completed" or rewrite["runner_dry_run"] or
    rewrite["model"] != spec["model"] or rewrite["reasoning_effort"] != spec["reasoning_effort"] or
    rewrite["manifest_input_hash"] != task["input_hash"] or
    hashlib.sha256(rewrite["generated_text"].encode("utf-8")).hexdigest() != rewrite["output_text_hash"] or
    not check_numeric_multiset(source_block, rewrite["generated_text"])[0]):
    raise ValueError("Saved rewrite completion/source/numeric identity failed")
blocks = segment_text(texts[203], parts=5)
qa_variant = "\n\n".join([rewrite["generated_text"], *blocks[1:]])
normalized_original = "\n\n".join(blocks)
print("QA rewrite completed with numeric preservation.", flush=True)

# These three requests cover distinct public request constructors and validators.
# The partial rewrite is explicitly QA data and never a production dose cell.
for family in ("forecast", "semantic", "pairwise"):
    if family == "forecast":
        request = build_agent_request(texts[203], "anchored", 3.80, inflation_target_for_meeting(203),
            20260712, 203, model=spec["model"], reasoning_effort=spec["reasoning_effort"],
            prompt_version=spec["prompt_versions"]["common_prior"])
    elif family == "semantic":
        record = {"anonymous_id": "QA_SEM_203", "text": qa_variant, "task_id": "qa_sem_203",
                  "prompt_version": spec["prompt_versions"]["semantic"],
                  "text_hash": hashlib.sha256(qa_variant.encode("utf-8")).hexdigest()}
        request = scorer.semantic_request(record, spec["model"], spec["reasoning_effort"], 1.0)
    else:
        record = {"anonymous_pair_id": "QA_PAIR_203", "text_a": normalized_original, "text_b": qa_variant,
                  "task_id": "qa_pair_203", "prompt_version": spec["prompt_versions"]["pairwise"]}
        request = scorer.pairwise_request(record, spec["model"], spec["reasoning_effort"], 1.0)
    visible_by_hash[request.input_hash] = len(encoder.encode(request.system_prompt + "\n\n" + request.user_prompt))
    cache = out / (family + ".json")
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved["spec_sha256"] != spec["_spec_sha256"] or saved["request_hash"] != request.input_hash:
            raise ValueError("QA cache request/configuration mismatch")
        response = LLMResponse(**saved["response"])
    else:
        attempts = len(list(attempt_dir.glob("attempt_*.json")))
        if attempts >= 6:
            raise ValueError("The six-attempt QA cap has been reached")
        response = runner.generate(request)
    if (response.dry_run or (response.metadata or {}).get("returncode") != 0 or response.model != spec["model"] or
        response.input_hash != request.input_hash or
        (response.metadata or {}).get("requested_reasoning_effort") != spec["reasoning_effort"] or
        (response.metadata or {}).get("requested_service_tier") != spec["service_tier"]):
        raise RuntimeError("Live QA runner/model validation failed for " + family)
    if family == "forecast":
        parse_agent_decision(response.text, spec["prompt_versions"]["common_prior"])
    elif family == "semantic":
        parsed = scorer.extract_json_object(response.text)
        scorer.clean_stance_score(parsed.get("stance_score"))
        scorer.clean_label(parsed.get("label"))
        scorer.clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence")
    else:
        parsed = scorer.extract_json_object(response.text)
        scorer.clean_pairwise(parsed.get("more_hawkish"))
        scorer.clean_score(parsed.get("confidence"), 0.0, 1.0, "confidence")
    if not cache.exists():
        with cache.open("x", encoding="utf-8") as handle:
            json.dump({"spec_sha256": spec["_spec_sha256"], "request_hash": request.input_hash,
                       "response": asdict(response)}, handle, ensure_ascii=False, indent=2)
    print("QA " + family + " JSON/schema passed.", flush=True)
if not after.exists():
    subprocess.run([sys.executable, str(telemetry), "--spec", str(args.spec), "--codex", args.codex,
                    "--service-tier", "default", "--output", str(after)], check=True,
                   stdout=subprocess.DEVNULL)

logged = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines() if line.strip()]
reserved_attempts = len(list(attempt_dir.glob("attempt_*.json")))
if reserved_attempts != len(logged):
    raise RuntimeError("An attempted call has no final usage record; retain reservations and investigate before retrying")
task_families = {"counterfactual_rewrite": "rewrite", "synthetic_forecaster": "forecast",
                 "step3_semantic_tone_classification": "semantic", "step3_pairwise_tone_classification": "pairwise"}
observations = []
samples = {}
for line in logged:
    family = task_families[line["request"]["metadata"]["task"]]
    events = line["response"]["metadata"].get("usage_events", [])
    completed = [event["usage"] for event in events if event.get("type") == "turn.completed"]
    if len(completed) != 1 or not all(key in completed[0] for key in ("input_tokens", "cached_input_tokens", "output_tokens")):
        raise ValueError("Missing or ambiguous authoritative CLI usage; do not substitute zero")
    usage = completed[0]
    if not 0 <= usage["cached_input_tokens"] <= usage["input_tokens"]:
        raise ValueError("CLI cached/input token definitions need inspection")
    accepted = line["response"]["metadata"].get("returncode") == 0
    observations.append({"family": family, "request_hash": line["request"]["input_hash"],
                         "accepted": accepted, "spec_sha256": line["response"]["metadata"].get("spec_sha256"), **usage})
    if accepted and family not in samples and line["request"]["input_hash"] in visible_by_hash:
        samples[family] = {**usage, "visible_tokens": visible_by_hash[line["request"]["input_hash"]]}
if set(samples) != {"rewrite", "forecast", "semantic", "pairwise"} or len(logged) > 6:
    raise ValueError("The smaller smoke did not complete all four request families within its cap")
projected = {}
for name, row in planned.items():
    sample = samples[row["sample_family"]]
    overhead = sample["input_tokens"] - sample["visible_tokens"]
    if overhead < 0:
        raise ValueError("Visible-token mapping exceeds actual usage; investigate before extrapolation")
    input_tokens = row["visible_tokens"] + row["requests"] * overhead
    projected[name] = {"requests": row["requests"], "input_tokens": input_tokens,
        "output_tokens": row["requests"] * sample["output_tokens"],
        "cached_input_tokens_at_observed_prefix": min(input_tokens, row["requests"] * sample["cached_input_tokens"])}
summary = {"spec_sha256": spec["_spec_sha256"], "fingerprint": spec["fingerprint"],
    "model": spec["model"], "reasoning_effort": spec["reasoning_effort"], "service_tier": spec["service_tier"],
    "actual_requests": len(logged), "reserved_attempts": reserved_attempts, "full_first_attempt_requests": 51508,
    "rewrite_retry_ceiling_additional_requests": 59250, "observations": observations,
    "sample_usage": samples, "projected_first_attempts": projected,
    "weekly_before": json.loads(before.read_text(encoding="utf-8")),
    "weekly_after": json.loads(after.read_text(encoding="utf-8")),
    "limitations": ["Four request-family examples are not a statistical sampling design.",
        "Future generated text, reasoning length, cache hits and retries remain unknown.",
        "The weekly meter is rounded. Token counts and API dollars do not specify the Pro weekly allowance.",
        "No production text/forecast/statistical output was generated. The full 92-request smoke is deferred."]}
if summary_path.exists():
    raise FileExistsError("A completed budget result is immutable. Use its saved record.")
with summary_path.open("x", encoding="utf-8") as handle:
    json.dump(summary, handle, ensure_ascii=False, indent=2)
print(json.dumps({"completed": True, "actual_requests": len(logged), "summary": str(summary_path)}), flush=True)
