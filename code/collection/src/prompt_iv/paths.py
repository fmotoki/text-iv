from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
import os


# These checks enforce the research run boundary, which generic path libraries
# cannot infer from the package structure or the selected cohort.
def load_spec(spec_path: str | Path | None = None, source_root: str | Path | None = None,
              require_frozen: bool = False) -> dict:
    selected = spec_path or os.environ.get("REPLICATION_SPEC_PATH")
    if not selected:
        raise ValueError("Set REPLICATION_SPEC_PATH or pass --spec. No legacy artifact defaults are allowed.")
    spec_file = Path(selected).resolve(strict=True)
    spec = json.loads(spec_file.read_text(encoding="utf-8-sig"))
    required = ("source_root", "artifact_root", "run_root", "release_id", "design_type", "model",
                "reasoning_effort", "sample", "run_ids", "paths", "fingerprint")
    missing = [key for key in required if key not in spec]
    if missing:
        raise ValueError(f"Missing specification fields: {missing}")
    source = Path(spec["source_root"]).resolve(strict=True)
    if source_root is not None and source != Path(source_root).resolve(strict=True):
        raise ValueError("--root must equal specification SOURCE_ROOT")
    run = Path(spec["run_root"])
    if not run.is_absolute():
        raise ValueError("RUN_ROOT must be absolute")
    run = run.resolve()
    if run == source or run in source.parents:
        raise ValueError("RUN_ROOT cannot be SOURCE_ROOT or its ancestor")
    design = spec["design_type"]
    if design not in {"production", "qa_fixture", "qa_smoke"}:
        raise ValueError("Unknown design_type")
    artifact = Path(spec["artifact_root"])
    if not artifact.is_absolute():
        raise ValueError("artifact_root must be absolute")
    artifact = artifact.resolve()
    if design == "production" and artifact != (source.parent / "replication_artifacts").resolve():
        raise ValueError("Production artifact_root must be the selected sibling replication_artifacts directory")
    if design == "production" and run != (artifact / "runs" / "terra56" / spec["release_id"]).resolve():
        raise ValueError("Production RUN_ROOT must use artifact_root/runs/terra56/<release_id>")
    if design == "production" and (artifact == source or source in artifact.parents):
        raise ValueError("Production data/results cannot live inside the source Git repository")
    for protected in ("outputs", "results", "reference", "logs", "archives",
                      "data/interim", "data/generated", "data/analysis", "data/metadata"):
        tree = (source / protected).resolve()
        if run == tree or tree in run.parents:
            raise ValueError(f"RUN_ROOT is inside protected current files: {tree}")
    if spec["model"] != "gpt-5.6-terra" or spec["reasoning_effort"] != "high":
        raise ValueError("The prospective cohort requires exact gpt-5.6-terra/high")
    meetings = [int(value) for value in spec["sample"]["meeting_numbers"]]
    if not meetings or meetings != list(range(203, meetings[-1] + 1)):
        raise ValueError("Selected trajectories must be a consecutive prefix starting at 203")
    if design == "production" and meetings != list(range(203, 282)):
        raise ValueError("Production meeting membership must equal 203:281")
    if meetings[-1] > 281 or spec["sample"]["first_meeting"] != meetings[0] or spec["sample"]["last_meeting"] != meetings[-1]:
        raise ValueError("Sample endpoints disagree with exact meeting membership")
    run_ids = list(spec["run_ids"].values())
    if len(run_ids) != len(set(run_ids)) or any(not str(value).startswith("terra56") for value in run_ids):
        raise ValueError("Run identities must be unique and terra56-prefixed")
    if require_frozen and (len(str(spec["fingerprint"])) != 64 or any(c not in "0123456789abcdef" for c in str(spec["fingerprint"]))):
        raise ValueError("Freeze the effective source/input fingerprint before preparation or live execution")
    effective_path = validate_artifact_path(run / "effective_spec.json", spec, allow_frozen=True)
    if effective_path.exists():
        if json.loads(effective_path.read_text(encoding="utf-8-sig")) != spec:
            raise ValueError("Selected specification differs from RUN_ROOT/effective_spec.json")
    elif require_frozen:
        raise ValueError("Runtime producers require an identical, exclusively frozen RUN_ROOT/effective_spec.json")
    if require_frozen:
        if not isinstance(spec.get("source_hashes"), dict) or not spec["source_hashes"] or not isinstance(spec.get("input_hashes"), dict) or not spec["input_hashes"]:
            raise ValueError("Frozen runtime specification requires explicit source_hashes and input_hashes")
        for relative, expected_hash in spec["source_hashes"].items():
            source_file = Path(relative)
            if source_file.is_absolute():
                raise ValueError("source_hashes keys must be relative to SOURCE_ROOT")
            source_file = (source / source_file).resolve(strict=True)
            if source not in source_file.parents or hashlib.sha256(source_file.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"Source identity changed since specification freeze: {relative}")
        for absolute, expected_hash in spec["input_hashes"].items():
            input_file = Path(absolute)
            if not input_file.is_absolute() or hashlib.sha256(input_file.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"Input identity changed since specification freeze: {absolute}")
    dates_file = Path(spec["sample"]["dates_csv"])
    if not dates_file.is_absolute():
        dates_file = source / dates_file
    from prompt_iv.agents.targets import inferred_year_for_meeting
    with dates_file.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    canonical = {}
    for row in rows:
        meeting = int(row["meeting_number"])
        if meeting in canonical:
            raise ValueError(f"Duplicate canonical date for meeting {meeting}")
        canonical[meeting] = row["meeting_date"]
    for meeting in meetings:
        if meeting not in canonical or int(canonical[meeting][:4]) != inferred_year_for_meeting(meeting):
            raise ValueError(f"Canonical year/date mismatch for meeting {meeting}")
    spec["_spec_path"] = str(spec_file)
    spec["_spec_sha256"] = hashlib.sha256(spec_file.read_bytes()).hexdigest()
    spec["source_root"] = str(source)
    spec["run_root"] = str(run)
    for key, value in spec["paths"].items():
        if isinstance(value, str):
            path = Path(value)
            if not path.is_absolute():
                path = run / path
            spec["paths"][key] = str(validate_artifact_path(path, spec, allow_frozen=True))
    return spec


# Resolve before opening: Path.resolve follows existing symlinks and junctions,
# including a linked ancestor of a not-yet-created output file.
def validate_artifact_path(path: str | Path, spec: dict | None = None,
                           allow_frozen: bool = False) -> Path:
    spec = spec if spec is not None else load_spec(require_frozen=True)
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"Artifact paths must be explicit and absolute: {path}")
    run = Path(spec["run_root"]).resolve()
    candidate = candidate.resolve()
    if candidate == run or run not in candidate.parents:
        raise ValueError(f"Artifact escapes selected RUN_ROOT: {path}")
    frozen = spec["paths"].get("frozen_database")
    if not allow_frozen and frozen and candidate == Path(frozen).resolve():
        raise ValueError("Frozen candidate database is read-only to collection tools")
    return candidate


# The registration primitives use this shared identity to reject cohort reuse.
def specification_metadata(spec: dict | None = None) -> dict:
    spec = spec if spec is not None else load_spec(require_frozen=True)
    return {"spec_sha256": spec["_spec_sha256"], "fingerprint": spec["fingerprint"],
            "release_id": spec["release_id"], "design_type": spec["design_type"]}


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    minutes_dir: Path
    paper_dir: Path
    logs_dir: Path
    raw_data_dir: Path
    interim_data_dir: Path
    processed_data_dir: Path
    run_root: Path
    specification: dict


def discover_root(start: Path | None = None) -> Path:
    """Return the explicit source root from the selected specification."""
    return Path(load_spec(source_root=start)["source_root"])


def load_paths(root: Path | None = None) -> ProjectPaths:
    spec = load_spec(source_root=root, require_frozen=True)
    source = Path(spec["source_root"])
    run = Path(spec["run_root"])
    return ProjectPaths(root=source, minutes_dir=source / "data/raw/copom_minutes",
        paper_dir=source, logs_dir=Path(spec["paths"]["logs"]),
        raw_data_dir=source / "data/raw", interim_data_dir=run / "data/interim",
        processed_data_dir=Path(spec["paths"]["work_database"]).parent,
        run_root=run, specification=spec)
