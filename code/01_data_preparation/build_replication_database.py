"""Build the frozen analysis database used by the replication package.

This author-side preparation step extracts only the rows and columns needed by
the published analysis from the full production event database. Replicators do
not need the full production database or provider credentials: the resulting
SQLite file is distributed as an analysis input.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


AGENT_RUNS = (
    "common_prior_main_25draws_v1_20260712",
    "common_prior_dose_6arm_5draws_v1_20260713",
    "arm_specific_prior_dose_6arm_5draws_v1_20260714",
    "common_prior_original_v1_20260712",
)

SOURCE_RUNS = (
    "v10_referee_d5_25draws_20260620",
    "v10_referee_step3_neutral_dose_5draws_20260627",
)

FOCUS_ENTITIES = (
    "ExpectativasMercadoInflacao12Meses",
    "ExpectativasMercadoTop5Inflacao12Meses",
    "ExpectativasMercadoSelic",
)


def placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def copy_query(
    target: sqlite3.Connection,
    table: str,
    query: str,
    params: tuple[str, ...] = (),
) -> None:
    source_columns = target.execute(f"PRAGMA srcdb.table_info('{table}')").fetchall()
    if not source_columns:
        raise RuntimeError(f"Source table is missing: {table}")
    # Qualifying the destination schema is essential: an unqualified DROP can
    # resolve to an attached database when the table is absent from main.
    target.execute(f'DROP TABLE IF EXISTS main."{table}"')
    target.execute(f'CREATE TABLE main."{table}" AS {query}', params)


def portable_string(value: str, project_root: Path, user_home: Path) -> str:
    for prefix, replacement in ((project_root, ""), (user_home, "${USER_HOME}")):
        variants = (str(prefix), prefix.as_posix())
        for variant in variants:
            if value.lower().startswith(variant.lower()):
                suffix = value[len(variant) :].lstrip("\\/").replace("\\", "/")
                return "/".join(part for part in (replacement, suffix) if part)
    return value


def portable_json(raw: str, project_root: Path, user_home: Path) -> str:
    value = json.loads(raw)

    def walk(item: object, key: str | None = None) -> object:
        if key == "codex_command":
            return "codex-cli"
        if isinstance(item, dict):
            return {name: walk(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, str):
            return portable_string(item, project_root, user_home)
        return item

    return json.dumps(walk(value), ensure_ascii=True, sort_keys=True)


def sanitize_portability_fields(connection: sqlite3.Connection, source: Path) -> None:
    project_root = source.parents[2]
    user_home = Path.home().resolve()

    for table, column in (
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
                (portable_string(value, project_root, user_home), rowid)
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
                (portable_json(raw, project_root, user_home), rowid)
                for rowid, raw in rows
            ),
        )


def build_database(source: Path, output: Path) -> None:
    source = source.resolve(strict=True)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if source == output:
        raise ValueError("Source and output databases must differ")
    if output.exists():
        output.unlink()

    connection = sqlite3.connect(output.as_uri() + "?mode=rwc", uri=True)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "ATTACH DATABASE ? AS srcdb",
            (source.as_uri() + "?mode=ro",),
        )

        run_marks = placeholders(AGENT_RUNS)
        source_marks = placeholders(SOURCE_RUNS)
        focus_marks = placeholders(FOCUS_ENTITIES)

        copy_query(
            connection,
            "agent_results_latest",
            f"SELECT * FROM srcdb.agent_results_latest WHERE run_id IN ({run_marks})",
            AGENT_RUNS,
        )
        copy_query(
            connection,
            "agent_tasks",
            f"SELECT * FROM srcdb.agent_tasks WHERE run_id IN ({run_marks})",
            AGENT_RUNS,
        )
        copy_query(
            connection,
            "tone_scores",
            f"SELECT * FROM srcdb.tone_scores WHERE run_id IN ({source_marks})",
            SOURCE_RUNS,
        )
        copy_query(
            connection,
            "corpus_texts",
            "SELECT * FROM srcdb.corpus_texts WHERE run_id = ?",
            (SOURCE_RUNS[1],),
        )
        copy_query(
            connection,
            "rewrite_results_latest",
            "SELECT run_id, task_id, status, stance, retry_attempts_used "
            "FROM srcdb.rewrite_results_latest WHERE run_id = ?",
            (SOURCE_RUNS[0],),
        )
        copy_query(
            connection,
            "realized_dose_scores",
            "SELECT * FROM srcdb.realized_dose_scores WHERE run_id = ?",
            (SOURCE_RUNS[1],),
        )
        copy_query(
            connection,
            "referee_text_embeddings",
            "SELECT * FROM srcdb.referee_text_embeddings "
            "WHERE run_id = ? AND source_role IN ('hawk', 'dove')",
            (SOURCE_RUNS[0],),
        )
        copy_query(
            connection,
            "referee_embedding_pair_metrics",
            "SELECT * FROM srcdb.referee_embedding_pair_metrics",
        )
        copy_query(
            connection,
            "referee_embedding_manifest",
            "SELECT * FROM srcdb.referee_embedding_manifest",
        )
        copy_query(
            connection,
            "external_bcb_sgs",
            "SELECT * FROM srcdb.external_bcb_sgs",
        )
        copy_query(
            connection,
            "external_focus_statistics",
            f"SELECT * FROM srcdb.external_focus_statistics "
            f"WHERE source_entity IN ({focus_marks})",
            FOCUS_ENTITIES,
        )
        copy_query(
            connection,
            "external_source_manifest",
            "SELECT * FROM srcdb.external_source_manifest",
        )
        copy_query(connection, "metadata", "SELECT * FROM srcdb.metadata")
        copy_query(
            connection,
            "runs",
            f"SELECT * FROM srcdb.runs WHERE run_id IN ({run_marks},{source_marks})",
            AGENT_RUNS + SOURCE_RUNS,
        )

        for table in (
            "blinded_semantic_tasks",
            "blinded_semantic_scores",
            "blinded_pairwise_tasks",
            "blinded_pairwise_scores",
            "referee_human_audit_tasks",
            "referee_human_audit_key",
            "referee_human_audit_ratings",
        ):
            copy_query(connection, table, f'SELECT * FROM srcdb."{table}"')

        sanitize_portability_fields(connection, source)

        indexes = (
            "CREATE INDEX idx_agent_results_run_task ON agent_results_latest(run_id, task_id)",
            "CREATE INDEX idx_agent_results_run_meeting ON agent_results_latest(run_id, meeting_number)",
            "CREATE INDEX idx_agent_tasks_run_task ON agent_tasks(run_id, task_id)",
            "CREATE INDEX idx_tone_run_cell ON tone_scores(run_id, meeting_number, iteration, role)",
            "CREATE INDEX idx_corpus_run_cell ON corpus_texts(run_id, meeting_number, iteration, role)",
            "CREATE INDEX idx_embedding_run_cell ON referee_text_embeddings(run_id, meeting_number, iteration, source_role)",
            "CREATE INDEX idx_focus_entity_date ON external_focus_statistics(source_entity, date)",
            "CREATE INDEX idx_sgs_code_date ON external_bcb_sgs(series_code, date)",
        )
        for statement in indexes:
            connection.execute(statement)

        connection.commit()
        connection.execute("DETACH DATABASE srcdb")
        connection.execute("VACUUM")
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build_database(args.source, args.output)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
