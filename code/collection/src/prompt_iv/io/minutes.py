from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MEETING_RE = re.compile(r"ata(\d+)\.txt$", re.IGNORECASE)


@dataclass(frozen=True)
class MinuteFile:
    meeting_number: int
    path: Path


def extract_meeting_number(path: str | Path) -> int:
    name = Path(path).name
    match = MEETING_RE.search(name)
    if not match:
        digits = re.sub(r"\D", "", name)
        if not digits:
            raise ValueError(f"Could not extract meeting number from {name!r}")
        return int(digits)
    return int(match.group(1))


def list_minutes(folder: str | Path) -> list[MinuteFile]:
    folder = Path(folder)
    files = []
    for path in folder.glob("*.txt"):
        files.append(MinuteFile(extract_meeting_number(path), path))
    return sorted(files, key=lambda item: item.meeting_number)


def read_text(path: str | Path, encoding: str = "utf-8") -> str:
    return Path(path).read_text(encoding=encoding)


def segment_text(text: str, parts: int = 5) -> list[str]:
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    if not paragraphs:
        return []
    if len(paragraphs) <= parts:
        return ["\n\n".join(paragraphs)]

    base, remainder = divmod(len(paragraphs), parts)
    blocks: list[str] = []
    cursor = 0
    for i in range(parts):
        size = base + (1 if i < remainder else 0)
        chunk = paragraphs[cursor : cursor + size]
        blocks.append("\n\n".join(chunk))
        cursor += size
    return blocks
