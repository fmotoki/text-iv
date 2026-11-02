from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from threading import Lock
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path


@dataclass(frozen=True)
class LLMRequest:
    system_prompt: str
    user_prompt: str
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "high"
    temperature: float = 1.0
    seed: int | None = None
    response_schema: dict | None = None
    metadata: dict | None = None

    @property
    def input_hash(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMResponse:
    text: str
    runner: str
    model: str
    input_hash: str
    created_at: str
    dry_run: bool
    metadata: dict | None = None


class LLMRunner(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse:
        ...


def log_response(log_path: str | Path | None, request: LLMRequest, response: LLMResponse) -> None:
    if not log_path:
        return
    path = validate_artifact_path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "request": {
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "temperature": request.temperature,
            "seed": request.seed,
            "response_schema": request.response_schema,
            "metadata": request.metadata,
            "input_hash": request.input_hash,
        },
        "response": asdict(response),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class DryRunRunner:
    name = "dry-run"

    def __init__(self, log_path: str | Path | None = None) -> None:
        self.log_path = log_path

    def generate(self, request: LLMRequest) -> LLMResponse:
        preview = request.user_prompt.strip().splitlines()[0][:120] if request.user_prompt.strip() else ""
        response = LLMResponse(
            text=f"[DRY RUN] No LLM call executed. Input hash: {request.input_hash}. Preview: {preview}",
            runner=self.name,
            model=request.model,
            input_hash=request.input_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
            dry_run=True,
            metadata={
                "reason": "real LLM calls require explicit authorization",
                "temperature_applied": False,
                "seed_applied": False,
                "response_schema_applied": False,
            },
        )
        log_response(self.log_path, request, response)
        return response


class CodexCliRunner:
    name = "codex-cli"

    def __init__(
        self,
        command: str = "codex",
        allow_real_llm: bool = False,
        log_path: str | Path | None = None,
        timeout_seconds: int = 300,
        max_attempts: int | None = None,
    ) -> None:
        self.command = os.getenv("PROMPT_IV_CODEX_COMMAND", command).strip().strip('"')
        self.allow_real_llm = allow_real_llm
        self.log_path = log_path
        self.timeout_seconds = timeout_seconds
        if max_attempts is not None and (max_attempts < 1 or log_path is None):
            raise ValueError("A bounded runner needs a positive attempt cap and a log path")
        self.max_attempts = max_attempts
        self._available_cache: tuple[bool, str] | None = None
        self._available_lock = Lock()
        self._version: str | None = None

    def _candidate_commands(self) -> list[str]:
        candidates: list[str] = []

        explicit = os.getenv("PROMPT_IV_CODEX_COMMAND")
        command_name = Path(self.command).name.lower()
        is_default_alias = command_name in {"codex", "codex.exe"} and Path(self.command).parent == Path(".")

        if explicit:
            candidates.append(self.command)
        elif is_default_alias:
            local_appdata = os.getenv("LOCALAPPDATA")
            if local_appdata:
                candidates.append(str(Path(local_appdata) / "OpenAI" / "Codex" / "bin" / "codex.exe"))
            if platform.system().lower() == "windows":
                candidates.append(str(Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.exe"))
            candidates.append(self.command)
        else:
            candidates.append(self.command)

        for name in (self.command, "codex", "codex.exe"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(resolved)

        seen: set[str] = set()
        unique: list[str] = []
        for item in candidates:
            key = os.path.normcase(os.path.abspath(item)) if any(sep in item for sep in ("\\", "/")) else item.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def check_available(self) -> tuple[bool, str]:
        if self._available_cache is not None and self._available_cache[0]:
            return self._available_cache

        with self._available_lock:
            if self._available_cache is not None and self._available_cache[0]:
                return self._available_cache

            result = self._check_available_uncached()
            if result[0]:
                self._available_cache = result
            return result

    def _check_available_uncached(self) -> tuple[bool, str]:
        errors: list[str] = []
        for candidate in self._candidate_commands():
            try:
                result = subprocess.run(
                    [candidate, "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except FileNotFoundError:
                errors.append(f"{candidate}: not found")
                continue
            except Exception as exc:
                errors.append(f"{candidate}: {type(exc).__name__}: {exc}")
                continue
            if result.returncode == 0:
                version = subprocess.run([candidate, "--version"], capture_output=True,
                                         text=True, timeout=10, check=False)
                if version.returncode != 0:
                    errors.append(f"{candidate}: --version failed")
                    continue
                self._version = version.stdout.strip()
                return True, candidate
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")
            errors.append(f"{candidate}: exit {result.returncode}: {detail[:220]}")

        return False, "No runnable Codex CLI found. Tried: " + " | ".join(errors[-6:])

    def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.allow_real_llm:
            response = LLMResponse(
                text="[BLOCKED] Codex CLI runner requires allow_real_llm=True.",
                runner=self.name,
                model=request.model,
                input_hash=request.input_hash,
                created_at=datetime.now(timezone.utc).isoformat(),
                dry_run=True,
                metadata={"blocked": "real LLM calls require explicit authorization"},
            )
            log_response(self.log_path, request, response)
            return response

        spec = load_spec(require_frozen=True)
        if not spec.get("execution", {}).get("allow_remote_calls", False):
            raise ValueError("The selected specification does not authorize remote calls")
        if spec["design_type"] == "production" and not spec.get("execution", {}).get("full_production_authorized", False):
            raise ValueError("Full production execution is disabled in the selected specification")
        if (request.model, request.reasoning_effort) != (spec["model"], spec["reasoning_effort"]):
            raise ValueError("Codex request must use exact gpt-5.6-terra/high")
        ok, detail = self.check_available()
        if not ok:
            response = LLMResponse(
                text=f"[BLOCKED] Codex CLI unavailable: {detail}",
                runner=self.name,
                model=request.model,
                input_hash=request.input_hash,
                created_at=datetime.now(timezone.utc).isoformat(),
                dry_run=True,
                metadata={"blocked": detail},
            )
            log_response(self.log_path, request, response)
            return response

        prompt = ("Use no tools, web search, retrieval, files, or external information. "
                  "Answer only from the supplied task text.\n\n"
                  + request.system_prompt.strip() + "\n\n" + request.user_prompt.strip())
        sandbox = "read-only"
        # Speed changes subscription consumption and belongs to the run identity.
        # An inherited shell variable cannot override the selected specification.
        service_tier = str(spec.get("service_tier", "")).strip()
        if service_tier not in {"default", "fast"}:
            raise ValueError("Pin service_tier to default (Standard) or fast in the specification")
        command_args = [
            detail,
            "-a",
            "never",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "--model",
            request.model,
            "-c",
            f'model_reasoning_effort="{request.reasoning_effort}"',
        ]
        if service_tier:
            command_args.extend(["-c", f"service_tier={json.dumps(service_tier)}"])
        command_args.extend(["--ephemeral", "--ignore-rules", "--color", "never", "--json"])

        with tempfile.TemporaryDirectory(prefix="prompt-iv-codex-") as temp_dir:
            temp_path = Path(temp_dir)
            last_message_path = temp_path / "last-message.txt"
            if request.response_schema is not None:
                schema_path = temp_path / "response-schema.json"
                schema_path.write_text(
                    json.dumps(request.response_schema, ensure_ascii=False, sort_keys=True),
                    encoding="utf-8",
                )
                command_args.extend(["--output-schema", str(schema_path)])
            command_args.extend(["-C", str(temp_path), "-o", str(last_message_path), "-"])
            # Codex exposes no per-script quota cap. Reserve each paid attempt
            # before launch, so timeouts and restarts cannot reuse its slot.
            if self.max_attempts is not None:
                attempt_dir = validate_artifact_path(Path(self.log_path).with_suffix(".attempts"), spec)
                attempt_dir.mkdir(parents=True, exist_ok=True)
                for attempt in range(1, self.max_attempts + 1):
                    try:
                        with (attempt_dir / f"attempt_{attempt:03d}.json").open("x", encoding="utf-8") as handle:
                            json.dump({"request_hash": request.input_hash, "model": request.model,
                                       "reasoning_effort": request.reasoning_effort, "service_tier": service_tier,
                                       "spec_sha256": spec["_spec_sha256"],
                                       "reserved_at": datetime.now(timezone.utc).isoformat()}, handle)
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ValueError("The persistent live-attempt cap has been reached")
            result = subprocess.run(
                command_args,
                capture_output=True,
                input=prompt.encode("utf-8"),
                timeout=self.timeout_seconds,
                check=False,
            )
            if result.returncode == 0 and last_message_path.exists():
                text = last_message_path.read_text(encoding="utf-8").strip()
                if not text:
                    text = result.stdout.decode("utf-8", errors="replace").strip()
            else:
                text = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        # Codex JSONL is the only available source of usage and rate-limit
        # evidence. Absent fields stay absent rather than becoming zero usage.
        events = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        usage_events = [event for event in events if isinstance(event.get("usage"), dict)]
        forbidden_items = {"command_execution", "web_search", "mcp_tool_call",
                           "collab_tool_call", "file_change", "tool_call"}
        tool_events = [event for event in events
                       if event.get("type") in forbidden_items
                       or (isinstance(event.get("item"), dict)
                           # Codex also emits non-executing startup warnings as
                           # error items. Exit status still governs failure.
                           and event["item"].get("type") not in {"reasoning", "agent_message", "error"})]
        invalid_reason = ("prohibited_tool_event" if tool_events else
                          "missing_json_event_evidence" if not events and result.returncode == 0 else None)
        rate_limit_events = [event for event in events if any("ratelimit" in str(key).replace("_", "").lower() for key in event)]
        returned_models = []
        for event in events:
            for payload in (event, event.get("response", {}), event.get("metadata", {})):
                if isinstance(payload, dict) and payload.get("model"):
                    returned_models.append(str(payload["model"]))
        if any(model != request.model for model in returned_models):
            invalid_reason = "returned_model_mismatch"
        response = LLMResponse(
            text=text,
            runner=self.name,
            model=request.model,
            input_hash=request.input_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
            dry_run=False,
            metadata={
                "returncode": result.returncode if not invalid_reason else 1,
                "codex_returncode": result.returncode,
                "status": "completed" if result.returncode == 0 and not invalid_reason else "failed",
                "invalid_reason": invalid_reason,
                "codex_version": self._version,
                "requested_model": request.model,
                "requested_reasoning_effort": request.reasoning_effort,
                "requested_service_tier": service_tier,
                "response_model_is_request_echo": True,
                "returned_model_evidence": returned_models,
                "backend_model_independently_verified": bool(returned_models),
                "command_configuration": command_args,
                "system_prompt_sha256": hashlib.sha256(request.system_prompt.encode("utf-8")).hexdigest(),
                "user_prompt_sha256": hashlib.sha256(request.user_prompt.encode("utf-8")).hexdigest(),
                "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "invocation_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
                "codex_json_events": events,
                "tool_event_audit_passed": not bool(tool_events),
                "usage_events": usage_events,
                "rate_limit_events": rate_limit_events,
                "usage_available": bool(usage_events),
                "rate_limits_available": bool(rate_limit_events),
                **specification_metadata(spec),
                "temperature_requested": request.temperature,
                "temperature_applied": False,
                "seed_requested": request.seed,
                "seed_applied": False,
                "response_schema_applied": request.response_schema is not None,
                "sampling_control": "codex_cli_default",
                "codex_command": detail,
                "service_tier": service_tier or None,
                "sandbox": sandbox,
            },
        )
        log_response(self.log_path, request, response)
        return response
