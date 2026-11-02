"""Build the frozen analysis database used by the replication package.

This author-side preparation step extracts only the rows and columns needed by
the published analysis from the full production event database. Replicators do
not need the full production database or provider credentials: the resulting
SQLite file is distributed as an analysis input.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
import re
import sqlite3
import struct
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / "code/collection/src"))
from prompt_iv.agents.simulation import build_agent_request
from prompt_iv.agents.prompts import build_forecaster_prompt
from prompt_iv.agents.targets import inflation_target_for_meeting
from prompt_iv.generation.prompts import build_rewrite_prompt_versioned
from prompt_iv.generation.tasks import RewriteTask, build_rewrite_user_prompt
from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.io.minutes import segment_text
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path
sys.path.insert(0, str(SOURCE_ROOT / "code/collection/scripts"))
from run_v10_referee_step3_blinded_validation import (
    SEMANTIC_SYSTEM_PROMPT, PAIRWISE_SYSTEM_PROMPT, semantic_request, pairwise_request,
)

FOCUS_ENTITIES = (
    "ExpectativasMercadoInflacao12Meses",
    "ExpectativasMercadoTop5Inflacao12Meses",
    "ExpectativasMercadoSelic",
)


def placeholders(values: tuple) -> str:
    return ",".join("?" for _ in values)


def copy_query(
    target: sqlite3.Connection,
    table: str,
    query: str,
    params: tuple = (),
) -> None:
    source_columns = target.execute(f"PRAGMA srcdb.table_info('{table}')").fetchall()
    if not source_columns:
        raise RuntimeError(f"Source table is missing: {table}")
    # Qualifying the destination schema is essential: an unqualified DROP can
    # resolve to an attached database when the table is absent from main.
    target.execute(f'DROP TABLE IF EXISTS main."{table}"')
    target.execute(f'CREATE TABLE main."{table}" AS {query}', params)


def portable_string(value: str, project_root: Path, run_root: Path, user_home: Path) -> str:
    for prefix, replacement in ((project_root, "${SOURCE_ROOT}"), (run_root, "${RUN_ROOT}"), (user_home, "${USER_HOME}")):
        variants = (str(prefix), prefix.as_posix())
        for variant in variants:
            if value.lower().startswith(variant.lower()):
                suffix = value[len(variant) :].lstrip("\\/").replace("\\", "/")
                return "/".join(part for part in (replacement, suffix) if part)
    return value


def portable_json(raw: str, project_root: Path, run_root: Path, user_home: Path) -> str:
    value = json.loads(raw)

    def walk(item: object, key: str | None = None) -> object:
        # Standard json hooks cannot transform strings nested in both lists and
        # dictionaries. This retained recursion only sanitizes path prefixes.
        if key == "codex_command":
            return "codex-cli"
        if isinstance(item, dict):
            return {name: walk(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, str):
            return portable_string(item, project_root, run_root, user_home)
        return item

    return json.dumps(walk(value), ensure_ascii=True, sort_keys=True)


def sanitize_portability_fields(connection: sqlite3.Connection, project_root: Path, run_root: Path) -> None:
    user_home = Path.home().resolve()

    for table, column in (
        ("minutes", "source_path"),
        ("runs", "run_dir"),
        ("rewrite_tasks", "source_path"),
        ("agent_tasks", "source_path"),
        ("corpus_texts", "source_path"),
        ("tone_scores", "source_path"),
    ):
        rows = connection.execute(
            f'SELECT rowid, "{column}" FROM main."{table}" WHERE "{column}" IS NOT NULL'
        ).fetchall()
        connection.executemany(
            f'UPDATE main."{table}" SET "{column}" = ? WHERE rowid = ?',
            (
                (portable_string(value, project_root, run_root, user_home), rowid)
                for rowid, value in rows
            ),
        )

    tables = (
        row[0]
        for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table'"
        )
    )
    for table in tables:
        columns = {
            row[1] for row in connection.execute(f'PRAGMA main.table_info("{table}")')
        }
        if "raw_json" not in columns:
            continue
        rows = connection.execute(
            f'SELECT rowid, raw_json FROM main."{table}" WHERE raw_json IS NOT NULL'
        ).fetchall()
        connection.executemany(
            f'UPDATE main."{table}" SET raw_json = ? WHERE rowid = ?',
            (
                (portable_json(raw, project_root, run_root, user_home), rowid)
                for rowid, raw in rows
            ),
        )


def build_database(source: Path, output: Path, spec_path: Path | None = None) -> dict:
    spec = load_spec(spec_path, source_root=SOURCE_ROOT, require_frozen=True)
    identity = specification_metadata(spec)
    source = validate_artifact_path(source, spec).resolve(strict=True)
    output = validate_artifact_path(output, spec, allow_frozen=True)
    if source != Path(spec["paths"]["database"]) or output != Path(spec["paths"]["frozen_database"]):
        raise ValueError("Use the prospective and candidate database paths from the selected specification")
    if source == output or output.exists():
        raise ValueError("Source/output must differ and an existing frozen output is immutable")
    manifest_path = validate_artifact_path(str(output) + ".manifest.json", spec, allow_frozen=True)
    if manifest_path.exists() or any(Path(str(output) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
        raise ValueError("Candidate target or sidecars already exist")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    audit_path = validate_artifact_path(Path(spec["paths"]["logs"]) / f"candidate_export_{stamp}.json", spec)
    audit = {"status": "STARTED", "created_at_utc": stamp, "source": str(source), "output": str(output), **identity}
    source_before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm")) if path.exists()}
    wal = Path(str(source) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Close/checkpoint the collection warehouse before export. A live WAL is not a frozen input.")
    # A closed warehouse with no WAL payload can be read immutably. This also
    # prevents SQLite from creating source-side shared-memory files.
    reader = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
    reader.row_factory = sqlite3.Row
    connection = None
    created = False
    sidecar_created = False
    try:
        reader.execute("BEGIN")
        if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source database integrity check failed")
        saved = reader.execute("SELECT value FROM metadata WHERE key='replication_specification'").fetchone()
        if not saved or json.loads(saved[0]) != identity:
            raise ValueError("Source warehouse specification metadata does not match")
        ids = spec["run_ids"]
        source_runs = (ids["main_text"], ids["dose_text"])
        agent_runs = (ids["original_priors"], ids["common_prior_main"], ids["common_prior_dose"], ids["recursive_dose"])
        all_runs = source_runs + agent_runs + (ids["blinded_scoring"],)
        meetings = tuple(spec["sample"]["meeting_numbers"])
        profiles = tuple(spec["profiles"])
        blocks = int(spec["blocks"])
        main_roles = tuple(spec["roles"]["main"])
        dose_roles = tuple(spec["roles"]["dose"])
        expected_counts = {}
        observed_counts = {}

        # Validate registrations, source texts and exact logical text/tone cells.
        registrations = reader.execute(f"SELECT * FROM runs WHERE run_id IN ({placeholders(all_runs)})", all_runs).fetchall()
        if {row["run_id"] for row in registrations} != set(all_runs) or len(registrations) != 7:
            raise ValueError("All seven selected cohorts must be registered")
        for row in registrations:
            metadata = json.loads(row["metadata_json"])
            config = metadata.get("replication_config", metadata)
            if any(metadata.get(key) != value for key, value in identity.items()):
                raise ValueError(f"Registration identity differs for {row['run_id']}")
            if (config.get("model"), config.get("reasoning_effort")) != (spec["model"], spec["reasoning_effort"]):
                raise ValueError("Registration model/effort differs from the selected cohort")
        minute_rows = reader.execute(f"SELECT * FROM minutes WHERE meeting_number IN ({placeholders(meetings)})", meetings).fetchall()
        minutes_by_number = {int(row["meeting_number"]): dict(row) for row in minute_rows}
        if set(minutes_by_number) != set(meetings) or len(minute_rows) != len(meetings):
            raise ValueError("Original minute keys are incomplete or duplicated")
        original_blocks = {}
        for meeting, row in minutes_by_number.items():
            if hashlib.sha256(row["raw_text"].encode("utf-8")).hexdigest() != row["text_hash"]:
                raise ValueError("Original minute content/hash mismatch")
            original_blocks[meeting] = segment_text(row["raw_text"], parts=blocks)
            if len(original_blocks[meeting]) != blocks:
                raise ValueError("Original minute segmentation does not match the selected design")
        corpus = {}
        expected_corpus = set()
        for run_id, draws, roles in ((ids["main_text"], spec["draws"]["main"], ("original",) + main_roles),
                                     (ids["dose_text"], spec["draws"]["dose"], dose_roles)):
            keys = {(run_id, meeting, draw, role) for meeting, draw, role in itertools.product(meetings, range(1, draws + 1), roles)}
            expected_corpus |= keys
            expected_counts[f"corpus:{run_id}"] = len(keys)
        rows = reader.execute(f"SELECT * FROM corpus_texts WHERE run_id IN ({placeholders(source_runs)})", source_runs).fetchall()
        for row in rows:
            key = (row["run_id"], int(row["meeting_number"]), int(row["iteration"]), row["role"])
            if key in corpus or row["complete"] != 1 or hashlib.sha256(row["text"].encode("utf-8")).hexdigest() != row["text_hash"]:
                raise ValueError("Corpus cells must be unique, complete and hash-compatible")
            if row["role"] == "original" and row["text_hash"] != minutes_by_number[int(row["meeting_number"])]["text_hash"]:
                raise ValueError("Original corpus text differs across source families or draws")
            corpus[key] = dict(row)
        if set(corpus) != expected_corpus:
            raise ValueError("Corpus keys do not equal both selected Cartesian products")
        tone_rows = reader.execute(f"SELECT * FROM tone_scores WHERE run_id IN ({placeholders(source_runs)})", source_runs).fetchall()
        tone_keys = {(row["run_id"], int(row["meeting_number"]), int(row["iteration"]), row["role"]) for row in tone_rows}
        if len(tone_rows) != len(tone_keys) or tone_keys != {(run, meeting, draw, role + "_text") for run, meeting, draw, role in expected_corpus}:
            raise ValueError("Tone keys do not match the selected source texts")
        if any(row["complete"] != 1 or not math.isfinite(float(row["score"])) or int(row["word_count"]) < 1 for row in tone_rows):
            raise ValueError("Tone scores are incomplete or nonfinite")

        # Validate rewrite blocks using the unchanged task/prompt primitives.
        for run_id, draws, roles in ((ids["main_text"], spec["draws"]["main"], main_roles),
                                     (ids["dose_text"], spec["draws"]["dose"], tuple(spec["roles"]["dose_generated"]))):
            tasks = reader.execute("SELECT * FROM rewrite_tasks WHERE run_id=?", (run_id,)).fetchall()
            results = reader.execute("SELECT * FROM rewrite_results_latest WHERE run_id=?", (run_id,)).fetchall()
            task_map = {row["task_id"]: row for row in tasks}
            result_map = {row["task_id"]: row for row in results}
            expected = set(itertools.product(meetings, range(1, draws + 1), range(1, blocks + 1), roles))
            found = {(int(row["meeting_number"]), int(row["iteration"]), int(row["block_index"]), row["stance"]) for row in tasks}
            if len(tasks) != len(task_map) or len(tasks) != len(found) or found != expected or set(result_map) != set(task_map) or len(results) != len(result_map):
                raise ValueError(f"Rewrite task/latest exact key support is invalid for {run_id}")
            expected_counts[f"rewrites:{run_id}"] = len(expected)
            for task_id, task in task_map.items():
                row = result_map[task_id]
                raw = json.loads(row["raw_json"])
                response = raw.get("response_metadata", {})
                if any(raw.get(field) != row[field] for field in ("task_id", "status", "model", "reasoning_effort")) or (spec["design_type"] == "production" and raw.get("runner") != "codex-cli"):
                    raise ValueError("Rewrite SQL and raw runner identities disagree")
                block = original_blocks[int(task["meeting_number"])][int(task["block_index"]) - 1]
                system = build_rewrite_prompt_versioned(task["stance"], spec["prompt_versions"]["rewrite"])
                user = build_rewrite_user_prompt(block, task["stance"], prompt_version=spec["prompt_versions"]["rewrite"])
                constructed = RewriteTask(task_id=task_id, meeting_number=int(task["meeting_number"]), source_path=task["source_path"],
                    iteration=int(task["iteration"]), block_index=int(task["block_index"]), total_blocks=blocks,
                    stance=task["stance"], original_text=block, system_prompt=system, user_prompt=user,
                    prompt_version=spec["prompt_versions"]["rewrite"]).manifest_record()
                if task["prompt_version"] != spec["prompt_versions"]["rewrite"] or int(task["total_blocks"]) != blocks or any(task[field] != constructed[field] for field in ("input_hash", "original_text_hash", "system_prompt_hash", "user_prompt_hash")):
                    raise ValueError("Rewrite source/prompt/task manifest identity changed")
                if row["status"] != "completed" or row["numeric_ok"] != 1 or raw.get("runner_dry_run") or (row["model"], row["reasoning_effort"]) != (spec["model"], spec["reasoning_effort"]):
                    raise ValueError("Rewrite latest result is not real completed Terra/high evidence")
                if row["manifest_input_hash"] != task["input_hash"] or any(raw.get(key) != value for key, value in identity.items()):
                    raise ValueError("Rewrite result source/specification identity mismatch")
                text = row["generated_text"]
                if not text or not check_numeric_multiset(block, text)[0] or hashlib.sha256(text.encode("utf-8")).hexdigest() != row["output_text_hash"]:
                    raise ValueError("Rewrite generated text/hash/numeric preservation mismatch")
                if response.get("returncode") != 0 or response.get("tool_event_audit_passed") is not True or response.get("response_sha256") != row["output_text_hash"]:
                    raise ValueError("Rewrite runner provenance or tool-event audit failed")
                if len(str(row["request_input_hash"])) != 64 or response.get("system_prompt_sha256") != task["system_prompt_hash"]:
                    raise ValueError("Rewrite request/prompt provenance is absent")
                if int(row["retry_attempts_used"] or 0) == 0 and response.get("user_prompt_sha256") != task["user_prompt_hash"]:
                    raise ValueError("Rewrite first-attempt request prompt differs from its manifest")
            event_ids = {row[0] for row in reader.execute("SELECT DISTINCT task_id FROM rewrite_result_events WHERE run_id=?", (run_id,))}
            if not event_ids.issubset(task_map) or not set(result_map).issubset(event_ids):
                raise ValueError("Rewrite event history does not support its selected latest cells")

        # Validate original, paired and recursive forecast trajectories.
        original_priors = {}
        for run_id, draws, roles, design, source_run, prompt_version in (
            (ids["original_priors"], (0,), ("original",), "original_path", None, spec["prompt_versions"]["original_priors"]),
            (ids["common_prior_main"], range(1, spec["draws"]["main"] + 1), main_roles, "common_prior", ids["main_text"], spec["prompt_versions"]["common_prior"]),
            (ids["common_prior_dose"], range(1, spec["draws"]["dose"] + 1), dose_roles, "common_prior", ids["dose_text"], spec["prompt_versions"]["common_prior"]),
            (ids["recursive_dose"], range(1, spec["draws"]["dose"] + 1), dose_roles, "arm_specific_prior_path", ids["dose_text"], spec["prompt_versions"]["recursive_dose"]),
        ):
            tasks = reader.execute("SELECT * FROM agent_tasks WHERE run_id=?", (run_id,)).fetchall()
            results = reader.execute("SELECT * FROM agent_results_latest WHERE run_id=?", (run_id,)).fetchall()
            task_map = {row["task_id"]: json.loads(row["raw_json"]) for row in tasks}
            for stored in tasks:
                task = task_map[stored["task_id"]]
                if any(stored[field] != task.get(field) for field in ("meeting_number", "corpus_iteration", "scenario", "forecaster_type", "source_text_hash", "source_run_id", "source_role", "prompt_version", "design", "sequence_index", "fixed_prior")):
                    raise ValueError("Forecast SQL task columns disagree with their immutable raw task")
            result_map = {row["task_id"]: row for row in results}
            expected = set(itertools.product(meetings, draws, roles, profiles))
            found = {(int(row["meeting_number"]), int(row["corpus_iteration"]), row["scenario"], row["forecaster_type"]) for row in tasks}
            if found != expected or len(tasks) != len(found) or len(task_map) != len(tasks) or set(result_map) != set(task_map) or len(results) != len(result_map):
                raise ValueError(f"Forecast exact key support is invalid for {run_id}")
            expected_counts[f"forecasts:{run_id}"] = len(expected)
            current_by_path = {}
            for task in sorted(task_map.values(), key=lambda item: (int(item["meeting_number"]), int(item["corpus_iteration"]), item["scenario"], item["forecaster_type"])):
                row = result_map[task["task_id"]]
                raw = json.loads(row["raw_json"])
                response = raw.get("response_metadata", {})
                if any(raw.get(field) != row[field] for field in ("task_id", "status", "model", "reasoning_effort")) or (spec["design_type"] == "production" and raw.get("runner") != "codex-cli"):
                    raise ValueError("Forecast SQL and raw runner identities disagree")
                if row["response_schema_applied"] != 1 or raw.get("response_schema_applied") is not True:
                    raise ValueError("Forecast SQL and raw schema application must both be true")
                # Exported numerical fields must retain the response record's
                # finite values, independently of the trajectory identities.
                for field in ("previous_expectation", "new_expectation", "forecast_revision"):
                    raw_value, sql_value = raw.get(field), row[field]
                    if (not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool)
                            or not isinstance(sql_value, (int, float))
                            or not math.isfinite(raw_value) or not math.isfinite(sql_value)
                            or not math.isclose(raw_value, sql_value, rel_tol=0, abs_tol=1e-12)):
                        raise ValueError("Forecast SQL and raw numerical values disagree or are nonfinite")
                meeting, draw, role, profile = int(task["meeting_number"]), int(task["corpus_iteration"]), task["scenario"], task["forecaster_type"]
                source_cell = minutes_by_number[meeting] if source_run is None else corpus[(source_run, meeting, draw, role)]
                text = source_cell["raw_text"] if source_run is None else source_cell["text"]
                expected_hash = source_cell["text_hash"]
                if (task.get("source_run_id"), task.get("source_role"), task.get("design"), task.get("prompt_version")) != (source_run, role, design, prompt_version):
                    raise ValueError("Forecast source/design/prompt link mismatch")
                if task.get("source_text_hash") != expected_hash or raw.get("source_text_hash") != expected_hash or row["source_text_hash"] != expected_hash:
                    raise ValueError("Forecast source-text hash differs from its selected original/corpus")
                if task.get("requested_model") != spec["model"] or task.get("requested_reasoning_effort") != spec["reasoning_effort"] or int(task["agent_id"]) != 1:
                    raise ValueError("Forecast task model/profile construction mismatch")
                if int(task["sequence_index"]) != meetings.index(meeting) + 1 or float(task["inflation_target"]) != inflation_target_for_meeting(meeting):
                    raise ValueError("Forecast sequence/year-target identity mismatch")
                if design == "common_prior":
                    previous = original_priors[(meeting, profile)]
                    if task.get("prior_source") != ids["original_priors"] or not math.isclose(float(task["fixed_prior"]), previous, rel_tol=0, abs_tol=1e-12):
                        raise ValueError("Paired forecasts must use original.previous_expectation")
                else:
                    path = (draw, role, profile)
                    previous = current_by_path.get(path, float(spec["initial_expectation"]))
                    if float(task["initial_expectation"]) != float(spec["initial_expectation"]):
                        raise ValueError("Original/recursive initial expectation mismatch")
                if row["status"] != "completed" or raw.get("runner_dry_run") or (row["model"], row["reasoning_effort"]) != (spec["model"], spec["reasoning_effort"]):
                    raise ValueError("Forecast latest result is not real completed Terra/high evidence")
                if any(raw.get(key) != value for key, value in identity.items()):
                    raise ValueError("Forecast result specification identity mismatch")
                forecast = float(row["new_expectation"])
                revision = float(row["forecast_revision"])
                if not all(math.isfinite(value) for value in (previous, forecast, revision)) or not math.isclose(float(row["previous_expectation"]), previous, rel_tol=0, abs_tol=1e-12) or not math.isclose(revision, forecast - previous, rel_tol=0, abs_tol=1e-12):
                    raise ValueError("Forecast chronology or revision identity mismatch")
                request = build_agent_request(text=text, forecaster_type=profile, previous_expectation=previous,
                    inflation_target=float(task["inflation_target"]), seed=int(task["agent_seed"]), meeting_number=meeting,
                    model=spec["model"], reasoning_effort=spec["reasoning_effort"], temperature=float(task["requested_temperature"]), prompt_version=prompt_version)
                family = build_forecaster_prompt(profile, previous_inflation=0.0, target=0.0, prompt_version=prompt_version)
                if task["prompt_family_hash"] != hashlib.sha256(family.encode("utf-8")).hexdigest() or raw.get("request_input_hash") != request.input_hash:
                    raise ValueError("Forecast prompt/source/prior request hash mismatch")
                if response.get("returncode") != 0 or response.get("tool_event_audit_passed") is not True or response.get("response_sha256") != raw.get("output_text_hash"):
                    raise ValueError("Forecast runner/response hash provenance failed")
                if design == "original_path":
                    original_priors[(meeting, profile)] = previous
                if design != "common_prior":
                    current_by_path[(draw, role, profile)] = forecast
            event_ids = {row[0] for row in reader.execute("SELECT DISTINCT task_id FROM agent_result_events WHERE run_id=?", (run_id,))}
            if not event_ids.issubset(task_map) or not set(result_map).issubset(event_ids):
                raise ValueError("Forecast event history does not support its selected latest cells")

        # Validate blinded scoring, realized-dose and embedding provenance.
        semantic = {}
        for family, key_fields, prompt, version in (
            ("semantic", ("meeting_number", "iteration", "text_role"), SEMANTIC_SYSTEM_PROMPT, spec["prompt_versions"]["semantic"]),
            ("pairwise", ("meeting_number", "iteration", "pair_type"), PAIRWISE_SYSTEM_PROMPT, spec["prompt_versions"]["pairwise"]),
        ):
            tasks = reader.execute(f"SELECT * FROM blinded_{family}_tasks WHERE run_id=?", (ids["blinded_scoring"],)).fetchall()
            scores = reader.execute(f"SELECT * FROM blinded_{family}_scores WHERE run_id=?", (ids["blinded_scoring"],)).fetchall()
            expected = set(itertools.product(meetings, range(1, spec["draws"]["dose"] + 1), dose_roles if family == "semantic" else tuple(spec["pairwise_pairs"])))
            found = {tuple(row[field] for field in key_fields) for row in tasks}
            score_keys = {tuple(row[field] for field in key_fields) for row in scores}
            task_map = {row["task_id"]: dict(row) for row in tasks}
            score_map = {row["task_id"]: dict(row) for row in scores}
            if found != expected or score_keys != expected or len(tasks) != len(found) or len(scores) != len(score_keys) or set(task_map) != set(score_map):
                raise ValueError(f"Blinded {family} exact Cartesian keys are invalid")
            expected_counts[f"blinded_{family}"] = len(expected)
            for task_id, task in task_map.items():
                score = score_map[task_id]
                for row in (task, score):
                    if row["source_run_id"] != ids["dose_text"] or row["spec_fingerprint"] != spec["fingerprint"] or (row["model"], row["reasoning_effort"]) != (spec["model"], spec["reasoning_effort"]) or row["prompt_version"] != version or row["prompt_hash"] != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
                        raise ValueError("Scorer model/source/prompt/specification identity mismatch")
                if any(task[field] != score[field] for field in ("task_id", *key_fields, "prompt_hash", "request_input_hash")):
                    raise ValueError("Scorer task/result identity mismatch")
                meeting, draw = int(task["meeting_number"]), int(task["iteration"])
                if family == "semantic":
                    cell = corpus[(ids["dose_text"], meeting, draw, task["text_role"])]
                    if task["text_hash"] != cell["text_hash"] or score["text_hash"] != cell["text_hash"]:
                        raise ValueError("Semantic score source hash mismatch")
                    task["text"] = cell["text"]
                    request = semantic_request(task, spec["model"], spec["reasoning_effort"], 1.0)
                    value = float(score["score"])
                    if not math.isfinite(value) or not -1 <= value <= 1:
                        raise ValueError("Semantic score must be finite and in [-1,1]")
                    semantic[(meeting, draw, task["text_role"])] = value
                else:
                    if {task["role_a"], task["role_b"]} != set(spec["pairwise_pairs"][task["pair_type"]]):
                        raise ValueError("Blinded pair A/B roles differ from their named comparison")
                    for side in ("a", "b"):
                        cell = corpus[(ids["dose_text"], meeting, draw, task[f"role_{side}"])]
                        if task[f"text_{side}_hash"] != cell["text_hash"] or score[f"text_{side}_hash"] != cell["text_hash"] or score[f"role_{side}"] != task[f"role_{side}"]:
                            raise ValueError("Pairwise source-text hash or A/B identity mismatch")
                        task[f"text_{side}"] = cell["text"]
                    request = pairwise_request(task, spec["model"], spec["reasoning_effort"], 1.0)
                    if score["more_hawkish"] not in ("A", "B", "tie"):
                        raise ValueError("Invalid blinded pairwise response")
                raw = json.loads(score["raw_json"])
                response = raw.get("response_metadata", {})
                if any(raw.get(field) != score[field] for field in ("task_id", "status", "model", "reasoning_effort", "request_input_hash")) or (spec["design_type"] == "production" and raw.get("runner") != "codex-cli"):
                    raise ValueError("Scorer SQL and raw runner identities disagree")
                if family == "semantic" and not math.isclose(float(raw["stance_score"]), float(score["score"]), rel_tol=0, abs_tol=1e-12):
                    raise ValueError("Semantic SQL score differs from its raw response record")
                if score["status"] != "completed" or score["runner_dry_run"] or score["request_input_hash"] != request.input_hash or response.get("returncode") != 0 or response.get("tool_event_audit_passed") is not True:
                    raise ValueError("Score is not a real, request-compatible completion")
                if not re.fullmatch(r"[0-9a-f]{64}", str(score["raw_response_hash"])) or score["raw_response_hash"] != raw.get("raw_response_hash") or response.get("response_sha256") != score["raw_response_hash"]:
                    raise ValueError("Scorer response hash provenance mismatch")
        realized = reader.execute("SELECT * FROM realized_dose_scores WHERE run_id=?", (ids["dose_text"],)).fetchall()
        realized_keys = {(int(row["meeting_number"]), int(row["iteration"]), row["text_role"]) for row in realized}
        if realized_keys != set(semantic) or len(realized) != len(realized_keys):
            raise ValueError("Realized-dose exact source support is incomplete or duplicated")
        for row in realized:
            key = (int(row["meeting_number"]), int(row["iteration"]), row["text_role"])
            cell = corpus[(ids["dose_text"], *key)]
            if row["source_run_id"] != ids["dose_text"] or row["scorer_run_id"] != ids["blinded_scoring"] or row["source_text_hash"] != cell["text_hash"] or row["text_hash"] != cell["text_hash"]:
                raise ValueError("Realized dose source/scorer/hash link mismatch")
            if row["status"] != "completed" or row["runner_dry_run"] or (row["model"], row["reasoning_effort"], row["spec_fingerprint"]) != (spec["model"], spec["reasoning_effort"], spec["fingerprint"]):
                raise ValueError("Realized dose completion/model/specification mismatch")
            if not math.isfinite(float(row["semantic_score"])) or not math.isclose(float(row["semantic_score"]), semantic[key], rel_tol=0, abs_tol=1e-12):
                raise ValueError("Realized dose disagrees with its selected semantic score")
        expected_counts["realized_dose_scores"] = len(semantic)

        model_id = spec["embedding_model"]["id"]
        dimensions = int(spec["embedding_model"]["dimensions"])
        for table in ("referee_text_embeddings", "referee_embedding_pair_metrics"):
            rows = reader.execute(f"SELECT * FROM {table} WHERE run_id IN ({placeholders(source_runs)}) AND model_id=?", source_runs + (model_id,)).fetchall()
            found = {(row["run_id"], int(row["meeting_number"]), int(row["iteration"]), row["source_role"]) for row in rows}
            expected = expected_corpus if table == "referee_text_embeddings" else {key for key in expected_corpus if key[3] != "original"}
            if found != expected or len(rows) != len(found):
                raise ValueError("Selected embedding logical hash coverage is incomplete or duplicated")
            for row in rows:
                key = (row["run_id"], int(row["meeting_number"]), int(row["iteration"]), row["source_role"])
                if row["text_hash"] != corpus[key]["text_hash"]:
                    raise ValueError("Embedding text hash disagrees with its selected source")
                if table == "referee_text_embeddings":
                    if int(row["dimensions"]) != dimensions or len(row["embedding_blob"]) != dimensions * 4 or not all(math.isfinite(value) for value in struct.unpack("<" + "f" * dimensions, row["embedding_blob"])):
                        raise ValueError("Embedding dimensions or vector values are invalid")
                else:
                    if row["original_text_hash"] != corpus[(key[0], key[1], key[2], "original")]["text_hash"]:
                        raise ValueError("Embedding comparison uses a different original text")
                    metrics = ("document_cosine_similarity", "rewrite_to_original_sentence_similarity", "original_to_rewrite_sentence_similarity", "sentence_similarity_p05", "sentence_share_below_080", "subword_jensen_shannon", "subword_count_ratio", "sentence_count_ratio")
                    if any(not math.isfinite(float(row[field])) for field in metrics):
                        raise ValueError("Embedding pair metrics contain missing or nonfinite values")
            expected_counts[table] = len(expected)
        manifests = reader.execute(f"SELECT * FROM referee_embedding_run_manifest WHERE run_id IN ({placeholders(source_runs)}) AND model_id=?", source_runs + (model_id,)).fetchall()
        if len(manifests) != 2 or {row["run_id"] for row in manifests} != set(source_runs):
            raise ValueError("Selected embeddings require separate per-run model metadata")
        for row in manifests:
            keys = sorted((meeting, draw, role, cell["text_hash"]) for (run, meeting, draw, role), cell in corpus.items() if run == row["run_id"])
            corpus_hash = hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode("utf-8")).hexdigest()
            if row["spec_fingerprint"] != spec["fingerprint"] or row["corpus_sha256"] != corpus_hash or row["model_revision"] != spec["embedding_model"]["revision_status"]:
                raise ValueError("Per-run embedding model/source/specification metadata mismatch")

        # Copy only selected evidence after every preflight gate has passed.
        selections = {
            "metadata": ("1=1", ()),
            "minutes": (f"meeting_number IN ({placeholders(meetings)})", meetings),
            "runs": (f"run_id IN ({placeholders(all_runs)})", all_runs),
            "external_bcb_sgs": ("1=1", ()),
            "external_focus_statistics": (f"source_entity IN ({placeholders(FOCUS_ENTITIES)})", FOCUS_ENTITIES),
            "external_source_manifest": ("1=1", ()),
        }
        for table in ("rewrite_tasks", "rewrite_result_events", "rewrite_results_latest", "corpus_texts", "tone_scores"):
            selections[table] = (f"run_id IN ({placeholders(source_runs)})", source_runs)
        for table in ("agent_tasks", "agent_result_events", "agent_results_latest"):
            selections[table] = (f"run_id IN ({placeholders(agent_runs)})", agent_runs)
        for table in ("blinded_semantic_tasks", "blinded_semantic_scores", "blinded_pairwise_tasks", "blinded_pairwise_scores"):
            selections[table] = ("run_id=? AND source_run_id=?", (ids["blinded_scoring"], ids["dose_text"]))
        selections["realized_dose_scores"] = ("run_id=? AND scorer_run_id=?", (ids["dose_text"], ids["blinded_scoring"]))
        for table in ("referee_text_embeddings", "referee_embedding_pair_metrics", "referee_embedding_run_manifest"):
            selections[table] = (f"run_id IN ({placeholders(source_runs)}) AND model_id=?", source_runs + (spec["embedding_model"]["id"],))
        current = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm")) if path.exists()}
        if current != source_before:
            raise ValueError("Source files changed during export preflight")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb"):
            pass
        created = True
        connection = sqlite3.connect(output.as_uri() + "?mode=rw", uri=True)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("ATTACH DATABASE ? AS srcdb", (source.as_uri() + "?mode=ro&immutable=1",))
        for table, (condition, params) in selections.items():
            count = reader.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {condition}', params).fetchone()[0]
            copy_query(connection, table, f'SELECT * FROM srcdb."{table}" WHERE {condition}', params)
            if connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0] != count:
                raise ValueError(f"Selected copy count differs for {table}")
            observed_counts[table] = count
        sanitize_portability_fields(connection, Path(spec["source_root"]), Path(spec["run_root"]))
        connection.commit()
        connection.execute("DETACH DATABASE srcdb")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Candidate integrity check failed")
        connection.close()
        connection = None
        reader.rollback()
        reader.close()
        reader = None
        current = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm")) if path.exists()}
        if current != source_before:
            raise ValueError("Source files changed during candidate export")
        manifest = {**identity, "database_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                    "source_database_sha256": source_before[str(source)], "model": spec["model"],
                    "reasoning_effort": spec["reasoning_effort"], "selected_run_ids": dict(ids),
                    "expected_counts": expected_counts, "observed_counts": observed_counts,
                    "exported_at_utc": datetime.now(timezone.utc).isoformat(), "auditstatus": "PASS", "audit_status": "PASS"}
        with manifest_path.open("x", encoding="utf-8") as handle:
            sidecar_created = True
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
        audit.update(status="PASS", manifest=str(manifest_path), **manifest)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("x", encoding="utf-8") as handle:
            json.dump(audit, handle, ensure_ascii=False, indent=2, sort_keys=True)
        return manifest
    except Exception as error:
        if connection is not None:
            connection.close()
            connection = None
        audit.update(status="FAIL", error_type=type(error).__name__, error=str(error))
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("x", encoding="utf-8") as handle:
            json.dump(audit, handle, ensure_ascii=False, indent=2, sort_keys=True)
        # Only files exclusively created by this invocation can be removed.
        if sidecar_created:
            manifest_path.unlink(missing_ok=True)
        if created:
            for path in (output, *(Path(str(output) + suffix) for suffix in ("-journal", "-wal", "-shm"))):
                validate_artifact_path(path, spec, allow_frozen=True).unlink(missing_ok=True)
        raise
    finally:
        if reader is not None:
            reader.close()
        if connection is not None:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, help="Explicit frozen specification, or set REPLICATION_SPEC_PATH.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build_database(args.source, args.output, args.spec)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
