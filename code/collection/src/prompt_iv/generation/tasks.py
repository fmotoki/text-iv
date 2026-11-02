from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import re

from prompt_iv.generation.numeric import extract_numeric_tokens
from prompt_iv.generation.prompts import (
    CALIBRATED_CONTRAST_PROMPT_VERSION,
    CALIBRATED_CONTRAST_V2_PROMPT_VERSION,
    CALIBRATED_CONTRAST_V3_PROMPT_VERSION,
    DEFAULT_PROMPT_VERSION,
    LEGACY_SERIES_PROMPT_VERSION,
    build_rewrite_prompt_versioned,
    normalize_prompt_version,
)
from prompt_iv.io.minutes import list_minutes, read_text, segment_text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or "", flags=re.UNICODE))


@dataclass(frozen=True)
class RewriteTask:
    task_id: str
    meeting_number: int
    source_path: str
    iteration: int
    block_index: int
    total_blocks: int
    stance: str
    original_text: str
    system_prompt: str
    user_prompt: str
    prompt_version: str = DEFAULT_PROMPT_VERSION

    @property
    def input_hash(self) -> str:
        payload = {
            "task_id": self.task_id,
            "meeting_number": self.meeting_number,
            "iteration": self.iteration,
            "block_index": self.block_index,
            "total_blocks": self.total_blocks,
            "stance": self.stance,
            "prompt_version": self.prompt_version,
            "original_text_hash": _sha256_text(self.original_text),
            "system_prompt_hash": _sha256_text(self.system_prompt),
            "user_prompt_hash": _sha256_text(self.user_prompt),
        }
        return _sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))

    def manifest_record(self) -> dict:
        numeric_tokens = extract_numeric_tokens(self.original_text)
        return {
            "task_id": self.task_id,
            "meeting_number": self.meeting_number,
            "source_path": self.source_path,
            "iteration": self.iteration,
            "block_index": self.block_index,
            "total_blocks": self.total_blocks,
            "stance": self.stance,
            "prompt_version": self.prompt_version,
            "input_hash": self.input_hash,
            "original_text_hash": _sha256_text(self.original_text),
            "system_prompt_hash": _sha256_text(self.system_prompt),
            "user_prompt_hash": _sha256_text(self.user_prompt),
            "char_count": len(self.original_text),
            "word_count": word_count(self.original_text),
            "numeric_token_count": sum(numeric_tokens.values()),
            "numeric_unique_count": len(numeric_tokens),
        }


def build_rewrite_user_prompt(
    original_text: str,
    stance: str,
    missing: Iterable[str] = (),
    extra: Iterable[str] = (),
    prompt_version: str | None = None,
) -> str:
    version = normalize_prompt_version(prompt_version)
    if version in {
        LEGACY_SERIES_PROMPT_VERSION,
        CALIBRATED_CONTRAST_PROMPT_VERSION,
        CALIBRATED_CONTRAST_V3_PROMPT_VERSION,
    }:
        prompt = f"Texto Original:\n{original_text}\n\nReescreva no tom {stance.upper()}:"
    elif version == CALIBRATED_CONTRAST_V2_PROMPT_VERSION:
        prompt = (
            f"Texto Original:\n{original_text}\n\n"
            "Reescreva o texto acima seguindo a instrucao de estilo do sistema."
        )
    else:
        prompt = (
            f"Texto Original:\n{original_text}\n\n"
            "Reescreva o texto acima seguindo a instrucao de estilo do sistema."
        )
    missing_items = sorted(set(missing))
    extra_items = sorted(set(extra))
    if missing_items or extra_items:
        prompt += (
            "\n\nATENCAO (CHECK AUTOMATICO): a tentativa anterior violou as regras numericas.\n"
            f"- Tokens que devem aparecer exatamente: {missing_items}\n"
            f"- Tokens que nao podem aparecer: {extra_items}\n"
            "Reescreva novamente preservando os demais fatos e a estrutura."
        )
    return prompt


def build_rewrite_tasks(
    minutes_dir: str | Path,
    stances: Iterable[str] = ("hawk", "dove"),
    iterations: int = 1,
    parts: int = 5,
    max_meetings: int | None = None,
    meeting_numbers: Iterable[int] | None = None,
    prompt_version: str | None = None,
) -> list[RewriteTask]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if parts < 1:
        raise ValueError("parts must be at least 1")

    normalized_stances = [stance.strip().lower() for stance in stances if stance.strip()]
    if not normalized_stances:
        raise ValueError("at least one stance is required")
    if len(normalized_stances) != len(set(normalized_stances)):
        raise ValueError("Duplicate rewrite stances are not allowed")
    normalized_prompt_version = normalize_prompt_version(prompt_version)

    minute_files = list_minutes(minutes_dir)
    if meeting_numbers is not None:
        selected_list = [int(number) for number in meeting_numbers]
        selected = set(selected_list)
        if len(selected) != len(selected_list):
            raise ValueError("Duplicate meeting selectors are not allowed")
        minute_files = [minute for minute in minute_files if minute.meeting_number in selected]
        if {minute.meeting_number for minute in minute_files} != selected:
            raise ValueError("A selected original minute is missing")
    if max_meetings is not None:
        minute_files = minute_files[:max_meetings]
    if len({minute.meeting_number for minute in minute_files}) != len(minute_files):
        raise ValueError("Multiple source files identify the same selected meeting")

    tasks: list[RewriteTask] = []
    for minute in minute_files:
        full_text = read_text(minute.path)
        blocks = segment_text(full_text, parts=parts)
        total_blocks = len(blocks)
        if total_blocks != parts:
            raise ValueError(f"Meeting {minute.meeting_number} did not produce the intended {parts} blocks")
        for iteration in range(1, iterations + 1):
            for block_index, block in enumerate(blocks, start=1):
                for stance in normalized_stances:
                    system_prompt = build_rewrite_prompt_versioned(stance, normalized_prompt_version)
                    user_prompt = build_rewrite_user_prompt(block, stance, prompt_version=normalized_prompt_version)
                    task_id = (
                        f"meeting-{minute.meeting_number:03d}"
                        f"-iter-{iteration:02d}"
                        f"-block-{block_index:02d}"
                        f"-{stance}"
                    )
                    tasks.append(
                        RewriteTask(
                            task_id=task_id,
                            meeting_number=minute.meeting_number,
                            source_path=str(minute.path),
                            iteration=iteration,
                            block_index=block_index,
                            total_blocks=total_blocks,
                            stance=stance,
                            original_text=block,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            prompt_version=normalized_prompt_version,
                        )
                    )
    return tasks


def write_rewrite_task_manifest(tasks: Iterable[RewriteTask], output_path: str | Path) -> int:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task.manifest_record(), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def summarize_rewrite_tasks(tasks: Iterable[RewriteTask]) -> dict:
    task_list = list(tasks)
    meetings = sorted({task.meeting_number for task in task_list})
    stances = sorted({task.stance for task in task_list})
    prompt_versions = sorted({task.prompt_version for task in task_list})
    return {
        "tasks": len(task_list),
        "meetings": len(meetings),
        "first_meeting": meetings[0] if meetings else None,
        "last_meeting": meetings[-1] if meetings else None,
        "stances": stances,
        "prompt_versions": prompt_versions,
        "iterations": sorted({task.iteration for task in task_list}),
        "blocks": sorted({task.total_blocks for task in task_list}),
    }
