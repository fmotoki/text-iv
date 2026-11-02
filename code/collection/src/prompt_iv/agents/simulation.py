from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import random

from prompt_iv.agents.prompts import (
    COMMON_PRIOR_PROMPT_VERSION,
    DEFAULT_FORECASTER_PROMPT_VERSION,
    build_forecaster_prompt,
    forecaster_response_schema,
    normalize_forecaster_prompt_version,
)
from prompt_iv.agents.targets import inflation_target_for_meeting
from prompt_iv.io.minutes import list_minutes
from prompt_iv.llm.runner import LLMRequest
from prompt_iv.paths import validate_artifact_path


FORECASTER_TYPES = {"anchored", "unanchored"}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentPlanTask:
    task_id: str
    agent_id: int
    agent_seed: int
    sequence_index: int
    meeting_number: int
    source_path: str
    forecaster_type: str
    inflation_target: float
    initial_expectation: float
    previous_expectation_required: bool

    def manifest_record(self) -> dict:
        prompt_family = build_forecaster_prompt(self.forecaster_type, previous_inflation=0.0, target=0.0)
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "agent_seed": self.agent_seed,
            "sequence_index": self.sequence_index,
            "meeting_number": self.meeting_number,
            "source_path": self.source_path,
            "forecaster_type": self.forecaster_type,
            "inflation_target": self.inflation_target,
            "initial_expectation": self.initial_expectation,
            "previous_expectation_required": self.previous_expectation_required,
            "prompt_family_hash": _sha256_text(prompt_family),
        }


@dataclass(frozen=True)
class AgentDecision:
    perceived_tone: float | None
    new_expectation: float


def build_agent_request(
    text: str,
    forecaster_type: str,
    previous_expectation: float,
    inflation_target: float,
    seed: int,
    meeting_number: int,
    model: str = "gpt-5.6-terra",
    reasoning_effort: str = "high",
    temperature: float = 1.0,
    prompt_version: str = DEFAULT_FORECASTER_PROMPT_VERSION,
) -> LLMRequest:
    if forecaster_type not in FORECASTER_TYPES:
        raise ValueError(f"Unknown forecaster_type {forecaster_type!r}")
    version = normalize_forecaster_prompt_version(prompt_version)
    if version == COMMON_PRIOR_PROMPT_VERSION:
        supplied_text = text
        user_prompt = f"TEXTO FORNECIDO:\n\n{supplied_text}"
    else:
        supplied_text = text[:15000]
        user_prompt = f"NOVA ATA DO COPOM:\n\n{supplied_text}"
    return LLMRequest(
        system_prompt=build_forecaster_prompt(
            forecaster_type,
            previous_expectation,
            inflation_target,
            prompt_version=version,
        ),
        user_prompt=user_prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        seed=seed,
        response_schema=forecaster_response_schema(version),
        metadata={
            "task": "synthetic_forecaster",
            "prompt_version": version,
            "forecaster_type": forecaster_type,
            "meeting_number": meeting_number,
            "previous_expectation": previous_expectation,
            "inflation_target": inflation_target,
            "source_text_hash": _sha256_text(text),
            "source_text_char_count": len(text),
            "supplied_text_char_count": len(supplied_text),
            "text_truncated": len(supplied_text) != len(text),
        },
    )


def build_agent_plan(
    minutes_dir: str | Path,
    forecaster_types: Iterable[str] = ("anchored", "unanchored"),
    num_agents: int = 1,
    initial_expectation: float = 3.80,
    random_seed: int = 123,
    max_meetings: int | None = None,
    meeting_numbers: Iterable[int] | None = None,
) -> list[AgentPlanTask]:
    if num_agents < 1:
        raise ValueError("num_agents must be at least 1")

    normalized_types = [item.strip().lower() for item in forecaster_types if item.strip()]
    if not normalized_types:
        raise ValueError("at least one forecaster type is required")
    unknown = sorted(set(normalized_types) - FORECASTER_TYPES)
    if unknown:
        raise ValueError(f"unknown forecaster type(s): {unknown}")

    minutes = list_minutes(minutes_dir)
    if meeting_numbers is not None:
        selected = [int(value) for value in meeting_numbers]
        if not selected or selected != list(range(203, selected[-1] + 1)):
            raise ValueError("Recursive agent plans must start at 203 without gaps")
        minutes = [minute for minute in minutes if minute.meeting_number in selected]
        if [minute.meeting_number for minute in minutes] != selected:
            raise ValueError("Selected original minutes are incomplete")
    if max_meetings is not None:
        minutes = minutes[:max_meetings]

    rng = random.Random(random_seed)
    tasks: list[AgentPlanTask] = []
    for forecaster_type in normalized_types:
        for agent_id in range(1, num_agents + 1):
            agent_seed = rng.randint(0, 1_000_000_000)
            for sequence_index, minute in enumerate(minutes, start=1):
                task_id = (
                    f"{forecaster_type}"
                    f"-agent-{agent_id:03d}"
                    f"-seq-{sequence_index:03d}"
                    f"-meeting-{minute.meeting_number:03d}"
                )
                tasks.append(
                    AgentPlanTask(
                        task_id=task_id,
                        agent_id=agent_id,
                        agent_seed=agent_seed,
                        sequence_index=sequence_index,
                        meeting_number=minute.meeting_number,
                        source_path=str(minute.path),
                        forecaster_type=forecaster_type,
                        inflation_target=inflation_target_for_meeting(minute.meeting_number),
                        initial_expectation=initial_expectation,
                        previous_expectation_required=sequence_index > 1,
                    )
                )
    return tasks


def parse_agent_decision(
    raw_text: str,
    prompt_version: str = DEFAULT_FORECASTER_PROMPT_VERSION,
) -> AgentDecision:
    content = (raw_text or "").replace("```json", "").replace("```", "").strip()
    data = json.loads(content)
    version = normalize_forecaster_prompt_version(prompt_version)
    expectation = float(data["nova_expectativa_inflacao"])
    if not 0 <= expectation <= 30:
        raise ValueError("nova_expectativa_inflacao must be between 0 and 30")
    if version != DEFAULT_FORECASTER_PROMPT_VERSION:
        unexpected = set(data) - {"nova_expectativa_inflacao"}
        if unexpected:
            raise ValueError(f"Unexpected common-prior response fields: {sorted(unexpected)}")
        return AgentDecision(perceived_tone=None, new_expectation=expectation)

    tone = float(data["percepcao_tom_score"])
    if not 1 <= tone <= 7:
        raise ValueError("percepcao_tom_score must be between 1 and 7")
    return AgentDecision(perceived_tone=tone, new_expectation=expectation)


def write_agent_plan_manifest(tasks: Iterable[AgentPlanTask], output_path: str | Path) -> int:
    path = validate_artifact_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task.manifest_record(), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def summarize_agent_plan(tasks: Iterable[AgentPlanTask]) -> dict:
    task_list = list(tasks)
    meetings = sorted({task.meeting_number for task in task_list})
    return {
        "tasks": len(task_list),
        "agents": len({(task.forecaster_type, task.agent_id) for task in task_list}),
        "forecaster_types": sorted({task.forecaster_type for task in task_list}),
        "meetings": len(meetings),
        "first_meeting": meetings[0] if meetings else None,
        "last_meeting": meetings[-1] if meetings else None,
        "recursive": any(task.previous_expectation_required for task in task_list),
    }
