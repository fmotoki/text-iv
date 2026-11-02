"""Read allowance telemetry and model availability without starting a model turn."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prompt_iv.paths import load_spec, validate_artifact_path

parser = argparse.ArgumentParser()
parser.add_argument("--codex", required=True)
parser.add_argument("--spec", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--service-tier", choices=("default", "fast"))
args = parser.parse_args()
spec = load_spec(args.spec, require_frozen=True)
# Validate both destinations before opening either file.
args.output = validate_artifact_path(args.output, spec)
stderr_path = validate_artifact_path(args.output.with_suffix(".stderr.log"), spec)
if args.output.exists():
    raise FileExistsError(args.output)

# JSON-RPC requires a matching response before the initialized notification.
# subprocess has no protocol reader, so the small response loop stays inline.
with stderr_path.open("x", encoding="utf-8") as error_log:
    server_command = [args.codex]
    if args.service_tier:
        server_command.extend(["-c", "service_tier=" + json.dumps(args.service_tier)])
    server_command.extend(["app-server", "--listen", "stdio://"])
    process = subprocess.Popen(
        server_command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log,
        text=True, encoding="utf-8", errors="strict",
    )
    # The subprocess API requires a callback to enforce the protocol deadline.
    deadline = threading.Timer(45, process.kill)
    deadline.start()
    responses = {}
    try:
        for identifier, method, parameters in (
            (1, "initialize", {"clientInfo": {"name": "replication_usage_audit", "version": "1.0"}}),
            (2, "account/rateLimits/read", {}),
            (3, "model/list", {}),
            (4, "config/read", {"includeLayers": False}),
            (5, "account/read", {"refreshToken": False}),
        ):
            process.stdin.write(json.dumps({"id": identifier, "method": method, "params": parameters}) + "\n")
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError("App-server ended before the requested response")
                event = json.loads(line)
                if event.get("id") == identifier:
                    if "error" in event:
                        raise RuntimeError(json.dumps(event["error"]))
                    responses[method] = event["result"]
                    break
            if identifier == 1:
                process.stdin.write('{"method":"initialized"}\n')
                process.stdin.flush()
        limits = responses["account/rateLimits/read"]
        models = responses["model/list"]
        configuration = responses["config/read"].get("config", {})
        account = responses["account/read"].get("account") or {}
        selected = [item for item in models.get("data", [])
                    if item.get("id") == "gpt-5.6-terra" or item.get("model") == "gpt-5.6-terra"]
        payload = {
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_turns_started": 0,
            "authentication_type": account.get("type"),
            "api_key_environment_present": {name: bool(os.environ.get(name)) for name in ("CODEX_API_KEY", "OPENAI_API_KEY")},
            "rateLimits": limits.get("rateLimits"),
            "rateLimitsByLimitId": limits.get("rateLimitsByLimitId"),
            "requested_model": "gpt-5.6-terra",
            "selected_model_listing": selected,
            "model_listing_has_next_page": models.get("nextCursor") is not None,
            "requested_service_tier_override": args.service_tier,
            "speed_configuration": {key: value for key, value in configuration.items()
                                    if key in ("service_tier", "serviceTier")},
            "fast_mode_feature": configuration.get("features", {}).get("fast_mode"),
        }
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(json.dumps(payload, ensure_ascii=True))
    finally:
        deadline.cancel()
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
