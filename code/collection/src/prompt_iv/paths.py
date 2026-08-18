from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    minutes_dir: Path
    paper_dir: Path
    logs_dir: Path
    raw_data_dir: Path
    interim_data_dir: Path
    processed_data_dir: Path


def discover_root(start: Path | None = None) -> Path:
    """Find the self-contained replication root."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "README.md").exists() and (candidate / "data").is_dir() and (candidate / "code").is_dir():
            return candidate
    return current


def load_paths(root: Path | None = None) -> ProjectPaths:
    root = discover_root(root)
    return ProjectPaths(
        root=root,
        minutes_dir=root / "data" / "raw" / "copom_minutes",
        paper_dir=root,
        logs_dir=root / "outputs" / "logs",
        raw_data_dir=root / "data" / "raw",
        interim_data_dir=root / "data" / "interim",
        processed_data_dir=root / "outputs" / "work",
    )
