"""Verify candidate freezing on a small, explicitly synthetic warehouse."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest

import pandas as pd

SOURCE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE_ROOT / "code/01_data_preparation"))
sys.path.insert(0, str(SOURCE_ROOT / "code/collection/scripts"))
from build_replication_database import build_database, copy_query
from prompt_iv.agents.common_prior import (
    prepare_original_prior_run, execute_original_prior_run,
    prepare_common_prior_run, execute_common_prior_run,
    prepare_arm_specific_prior_run, execute_arm_specific_prior_run,
)
from prompt_iv.generation.corpus import assemble_counterfactual_corpus
from prompt_iv.generation.execution import execute_rewrite_manifest
from prompt_iv.generation.tasks import build_rewrite_tasks
from prompt_iv.io.jsonl import read_jsonl
from prompt_iv.llm.runner import LLMResponse
from prompt_iv.paths import load_spec
from prompt_iv.pipelines.replication import prepare_replication_run
from prompt_iv.pipelines.tone import score_text_columns
from prompt_iv.storage.sqlite import connect_database, register_sqlite_run, sync_project_database
from run_v10_referee_step3_blinded_validation import (
    build_semantic_manifest, build_pairwise_manifest, execute_semantic, execute_pairwise,
    build_realized_dataset,
)


class CandidateExporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="terra56-export-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        self.run = self.fixture / "run"
        self.run.mkdir()
        spec = json.loads((SOURCE_ROOT / "code/replication_spec.json").read_text(encoding="utf-8-sig"))
        spec.update(design_type="qa_fixture", artifact_root=str(self.fixture), run_root=str(self.run), fingerprint="a" * 64)
        spec["sample"].update(first_meeting=203, last_meeting=204, meeting_numbers=[203, 204])
        spec["draws"] = {"main": 1, "dose": 1}
        spec["execution"] = {"allow_remote_calls": False, "full_production_authorized": False}
        code_file = "code/01_data_preparation/build_replication_database.py"
        spec["source_hashes"] = {code_file: hashlib.sha256((SOURCE_ROOT / code_file).read_bytes()).hexdigest()}
        dates = spec["sample"]["dates_csv"]
        spec["input_hashes"] = {dates: hashlib.sha256(Path(dates).read_bytes()).hexdigest()}
        self.spec_path = self.fixture / "spec.json"
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")
        (self.run / "effective_spec.json").write_text(json.dumps(spec), encoding="utf-8")
        environment = patch.dict(os.environ, REPLICATION_SPEC_PATH=str(self.spec_path))
        environment.start()
        self.addCleanup(environment.stop)
        self.spec = load_spec(require_frozen=True)
        self.database = Path(self.spec["paths"]["database"])
        self.output = Path(self.spec["paths"]["frozen_database"])
        ids = spec["run_ids"]
        minutes = self.fixture / "minutes"
        minutes.mkdir()
        for meeting in (203, 204):
            (minutes / f"ata{meeting}.txt").write_text("\n\n".join(f"Paragrafo {number}. A inflacao foi 3,0 por cento." for number in range(1, 11)), encoding="utf-8")
        runner = Mock()
        runner.name = "qa_fixture"
        runner.generate.side_effect = self.fixture_response
        self.rewrite_text = {}
        directories = []
        for family, roles in (("main", spec["roles"]["main"]), ("dose", spec["roles"]["dose_generated"])):
            run_id = ids[family + "_text"]
            directory = Path(self.spec["paths"]["collections"]) / run_id
            directories.append(directory)
            config = prepare_replication_run(SOURCE_ROOT, run_id, minutes, iterations=1, parts=5,
                stances=roles, meeting_numbers=[203, 204], run_directory=directory,
                prompt_version=spec["prompt_versions"]["rewrite"])["config"]
            tasks = build_rewrite_tasks(minutes, stances=roles, meeting_numbers=[203, 204], iterations=1, parts=5,
                prompt_version=spec["prompt_versions"]["rewrite"])
            self.rewrite_text.update({task.task_id: task.original_text for task in tasks})
            execute_rewrite_manifest(config["rewrite_manifest"], config["rewrite_results"], runner)
            assemble_counterfactual_corpus(config["rewrite_results"], config["counterfactual_corpus"], required_stances=tuple(roles), manifest_path=config["rewrite_manifest"])
            score_text_columns(config["counterfactual_corpus"], [role + "_text" for role in ("original", *roles)], directory / "counterfactual_tone_scores.csv")
        sync_project_database(self.database, SOURCE_ROOT, minutes, run_dirs=directories)
        prepare_original_prior_run(self.database, ids["original_priors"], meeting_numbers=[203, 204])
        execute_original_prior_run(self.database, ids["original_priors"], runner=runner)
        for family in ("main", "dose"):
            prepare_common_prior_run(self.database, ids["common_prior_" + family], source_run_id=ids[family + "_text"],
                prior_run_id=ids["original_priors"], meeting_numbers=[203, 204], iterations=[1], roles=spec["roles"][family])
            execute_common_prior_run(self.database, ids["common_prior_" + family], runner=runner)
        prepare_arm_specific_prior_run(self.database, ids["recursive_dose"], source_run_id=ids["dose_text"], meeting_numbers=[203, 204], iterations=[1])
        execute_arm_specific_prior_run(self.database, ids["recursive_dose"], runner=runner)

        validation = self.run / "data/interim/dose_response" / ids["blinded_scoring"]
        validation.mkdir(parents=True)
        corpus_path = directories[1] / "counterfactual_corpus.csv"
        build_semantic_manifest(corpus_path, validation / "semantic_tone_manifest.jsonl", seed=123)
        build_pairwise_manifest(corpus_path, validation / "pairwise_tone_manifest.jsonl", seed=456)
        execute_semantic(validation / "semantic_tone_manifest.jsonl", validation / "semantic_results.jsonl", runner,
            spec["model"], spec["reasoning_effort"], 1.0, None, resume=True, max_workers=1)
        execute_pairwise(validation / "pairwise_tone_manifest.jsonl", validation / "pairwise_results.jsonl", runner,
            spec["model"], spec["reasoning_effort"], 1.0, None, resume=True, max_workers=1)
        realized = build_realized_dataset(corpus_path, validation / "semantic_results.jsonl", validation / "pairwise_results.jsonl", validation / "realized.csv")
        connection = connect_database(self.database)
        try:
            with connection:
                register_sqlite_run(connection, ids["blinded_scoring"], metadata={"model": spec["model"], "reasoning_effort": spec["reasoning_effort"], "source_run_id": ids["dose_text"]})
            # Standard DataFrame.to_sql creates the importer's tabular contract.
            for family in ("semantic", "pairwise"):
                tasks = read_jsonl(validation / (family + "_tone_manifest.jsonl"))
                scores = read_jsonl(validation / (family + "_results.jsonl"))
                task_fields = ["run_id", "source_run_id", "task_id", "meeting_number", "iteration", "prompt_version", "prompt_hash", "request_input_hash", "model", "reasoning_effort", "spec_fingerprint"]
                task_fields += ["anonymous_id", "text_role", "assigned_dose", "text_hash"] if family == "semantic" else ["anonymous_pair_id", "pair_type", "role_a", "role_b", "text_a_hash", "text_b_hash"]
                task_data = [{field: row[field] for field in task_fields} for row in tasks]
                score_data = []
                for row in scores:
                    retained = {field: value for field, value in row.items() if not isinstance(value, (dict, list))}
                    retained["raw_json"] = json.dumps(row)
                    if family == "semantic":
                        retained["score"] = row["stance_score"]
                    score_data.append(retained)
                pd.DataFrame(task_data).to_sql("blinded_" + family + "_tasks", connection, index=False)
                pd.DataFrame(score_data).to_sql("blinded_" + family + "_scores", connection, index=False)
            realized["run_id"] = ids["dose_text"]
            realized["source_run_id"] = ids["dose_text"]
            realized["scorer_run_id"] = ids["blinded_scoring"]
            realized.to_sql("realized_dose_scores", connection, index=False)
        finally:
            connection.close()
        # The source table above uses ordinary tuples. Read named fixture rows.
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            corpus = [dict(row) for row in connection.execute("SELECT * FROM corpus_texts")]
            vectors, metrics, manifests = [], [], []
            for row in corpus:
                shared = {"run_id": row["run_id"], "meeting_number": row["meeting_number"], "iteration": row["iteration"], "source_role": row["role"], "text_hash": row["text_hash"], "model_id": spec["embedding_model"]["id"], "generated_at": "qa_fixture"}
                vectors.append({**shared, "dimensions": 384, "sentence_total_count": 10, "sentence_sample_count": 10, "subword_count": 50, "embedding_blob": struct.pack("<384f", *([0.1] * 384))})
                if row["role"] != "original":
                    original = next(cell for cell in corpus if (cell["run_id"], cell["meeting_number"], cell["iteration"], cell["role"]) == (row["run_id"], row["meeting_number"], row["iteration"], "original"))
                    metric = {**shared, "original_text_hash": original["text_hash"]}
                    metric.update({field: 1.0 for field in ("document_cosine_similarity", "rewrite_to_original_sentence_similarity", "original_to_rewrite_sentence_similarity", "sentence_similarity_p05", "sentence_share_below_080", "subword_jensen_shannon", "subword_count_ratio", "sentence_count_ratio")})
                    metrics.append(metric)
            for run_id in (ids["main_text"], ids["dose_text"]):
                keys = sorted((row["meeting_number"], row["iteration"], row["role"], row["text_hash"]) for row in corpus if row["run_id"] == run_id)
                manifests.append({"run_id": run_id, "model_id": spec["embedding_model"]["id"], "model_revision": spec["embedding_model"]["revision_status"], "spec_fingerprint": spec["fingerprint"], "corpus_sha256": hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode()).hexdigest(), "fastembed_version": "qa_fixture_no_fit", "tokenizer_id": spec["embedding_model"]["id"], "pooling": spec["embedding_model"]["pooling"], "sentence_sampling": "qa_fixture", "max_sentences_per_document": 12, "max_words_per_segment": 80, "threads": 6, "generated_at": "qa_fixture"})
            pd.DataFrame(vectors).to_sql("referee_text_embeddings", connection, index=False)
            pd.DataFrame(metrics).to_sql("referee_embedding_pair_metrics", connection, index=False)
            pd.DataFrame(manifests).to_sql("referee_embedding_run_manifest", connection, index=False)
            connection.execute("CREATE TABLE external_bcb_sgs(series_code INTEGER,date TEXT,value REAL)")
            connection.execute("CREATE TABLE external_focus_statistics(source_entity TEXT,date TEXT,value REAL)")
            connection.execute("CREATE TABLE external_source_manifest(source_id TEXT)")
            connection.execute("INSERT INTO runs(run_id,run_dir,metadata_json,ingested_at) VALUES('historical_sentinel','old','{}','fixture')")
            connection.execute("CREATE TABLE referee_human_audit_tasks(label TEXT)")
            connection.execute("INSERT INTO referee_human_audit_tasks VALUES('historical_only')")
            connection.commit()
        finally:
            connection.close()

    # Mock's LLM protocol requires a request callback. All responses are labelled
    # synthetic fixture evidence and no provider or embedding model is invoked.
    def fixture_response(self, request):
        task = request.metadata["task"]
        if task == "counterfactual_rewrite":
            text = self.rewrite_text[request.metadata["task_id"]]
        elif task == "step3_semantic_tone_classification":
            text = json.dumps({"stance_score": 0.2, "label": "neutral", "confidence": 0.8, "rationale_short": "fixture"})
        elif task == "step3_pairwise_tone_classification":
            text = json.dumps({"more_hawkish": "tie", "confidence": 0.8, "rationale_short": "fixture"})
        else:
            text = json.dumps({"nova_expectativa_inflacao": 4.1 if request.metadata["meeting_number"] == 203 else 4.2})
        metadata = {"returncode": 0, "response_schema_applied": True, "tool_event_audit_passed": True,
                    "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "system_prompt_sha256": hashlib.sha256(request.system_prompt.encode()).hexdigest(),
                    "user_prompt_sha256": hashlib.sha256(request.user_prompt.encode()).hexdigest(),
                    "usage_events": [{"usage": {"input_tokens": 10, "output_tokens": 5}}], "fixture": True}
        return LLMResponse(text=text, runner="qa_fixture", model=request.model, input_hash=request.input_hash,
                           created_at="qa_fixture", dry_run=False, metadata=metadata)

    def test_candidate_and_overwrite_protection(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        manifest = build_database(self.database, self.output, self.spec_path)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        self.assertEqual(manifest["database_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        frozen = self.output.read_bytes()
        with self.assertRaises(ValueError):
            build_database(self.database, self.output, self.spec_path)
        self.assertEqual(self.output.read_bytes(), frozen)
        connection = sqlite3.connect(self.output)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 7)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs WHERE run_id='historical_sentinel'").fetchone()[0], 0)
            self.assertFalse(connection.execute("SELECT name FROM sqlite_master WHERE name='referee_human_audit_tasks'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM corpus_texts").fetchone()[0], 18)
        finally:
            connection.close()

    def test_wrong_model_keys_hash_and_missing_state_are_rejected(self):
        changes = (
            ("UPDATE agent_results_latest SET model='wrong' WHERE rowid=(SELECT MIN(rowid) FROM agent_results_latest)", "UPDATE agent_results_latest SET model=? WHERE model='wrong'", (self.spec["model"],)),
            ("UPDATE corpus_texts SET role='wrong' WHERE role='hawk'", "UPDATE corpus_texts SET role='hawk' WHERE role='wrong'", ()),
            ("UPDATE corpus_texts SET text='changed' WHERE rowid=(SELECT MIN(rowid) FROM corpus_texts)", None, ()),
        )
        for mutate, restore, params in changes:
            with self.subTest(mutation=mutate):
                connection = sqlite3.connect(self.database)
                old = connection.execute("SELECT text FROM corpus_texts WHERE rowid=(SELECT MIN(rowid) FROM corpus_texts)").fetchone()[0]
                connection.execute(mutate)
                connection.commit()
                connection.close()
                before = hashlib.sha256(self.database.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):
                    build_database(self.database, self.output, self.spec_path)
                self.assertFalse(self.output.exists())
                self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
                connection = sqlite3.connect(self.database)
                connection.execute(restore if restore else "UPDATE corpus_texts SET text=? WHERE rowid=(SELECT MIN(rowid) FROM corpus_texts)", params if restore else (old,))
                connection.commit()
                connection.close()
        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM agent_results_latest WHERE run_id=? AND meeting_number=204", (self.spec["run_ids"]["original_priors"],))
        connection.commit()
        connection.close()
        with self.assertRaises(ValueError):
            build_database(self.database, self.output, self.spec_path)
        self.assertFalse(self.output.exists())

    def test_forecast_raw_sql_values_and_schema_are_required(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT rowid, * FROM agent_results_latest WHERE run_id=? AND meeting_number=204 ORDER BY forecaster_type LIMIT 1", (self.spec["run_ids"]["original_priors"],)).fetchone()
            original = (row["previous_expectation"], row["new_expectation"], row["forecast_revision"], row["response_schema_applied"], row["raw_json"])
            rowid = row["rowid"]
        previous, forecast, revision, schema, raw_json = original
        raw = json.loads(raw_json)
        # Shift the final forecast and revision together. SQL chronology still
        # holds, so only the raw/SQL identity gate can reject this corruption.
        cases = [
            ("sql_forecast", (previous, forecast + 0.25, revision + 0.25, schema, raw_json), "numerical values"),
            ("sql_schema_false", (previous, forecast, revision, 0, raw_json), "schema application"),
            ("raw_schema_false", (previous, forecast, revision, schema, json.dumps({**raw, "response_schema_applied": False})), "schema application"),
        ]
        for field in ("previous_expectation", "new_expectation", "forecast_revision"):
            cases.append(("raw_" + field, (previous, forecast, revision, schema, json.dumps({**raw, field: raw[field] + 0.25})), "numerical values"))
        cases.append(("raw_nonfinite", (previous, forecast, revision, schema, json.dumps({**raw, "new_expectation": float("inf")})), "numerical values"))
        cases.append(("sql_nonfinite", (previous, float("inf"), revision, schema, raw_json), "numerical values"))
        for name, values, message in cases:
            with self.subTest(mutation=name):
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("UPDATE agent_results_latest SET previous_expectation=?, new_expectation=?, forecast_revision=?, response_schema_applied=?, raw_json=? WHERE rowid=?", (*values, rowid))
                    connection.commit()
                before = hashlib.sha256(self.database.read_bytes()).hexdigest()
                try:
                    with self.assertRaisesRegex(ValueError, message):
                        build_database(self.database, self.output, self.spec_path)
                    self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
                    for suffix in ("", ".manifest.json", "-journal", "-wal", "-shm"):
                        self.assertFalse(Path(str(self.output) + suffix).exists())
                finally:
                    with closing(sqlite3.connect(self.database)) as connection:
                        connection.execute("UPDATE agent_results_latest SET previous_expectation=?, new_expectation=?, forecast_revision=?, response_schema_applied=?, raw_json=? WHERE rowid=?", (*original, rowid))
                        connection.commit()
        audits = list(Path(self.spec["paths"]["logs"]).glob("candidate_export_*.json"))
        self.assertEqual(len(audits), len(cases))
        for audit_path in audits:
            audit = json.loads(audit_path.read_text())
            self.assertEqual((audit["status"], audit["error_type"]), ("FAIL", "ValueError"))

    def test_partial_copy_failure_retains_audit_and_removes_only_candidate(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            old_run = connection.execute("SELECT * FROM runs WHERE run_id='historical_sentinel'").fetchall()
            old_audit = connection.execute("SELECT * FROM referee_human_audit_tasks").fetchall()
        neighbor = self.output.parent / "historical_frozen.sqlite"
        neighbor.parent.mkdir(parents=True, exist_ok=True)
        neighbors = (neighbor, Path(str(neighbor) + "-wal"), Path(str(neighbor) + ".manifest.json"))
        for path in neighbors:
            with path.open("xb") as handle:
                handle.write(b"historical fixture sentinel")
        neighbor_bytes = {path: path.read_bytes() for path in neighbors}
        connections = []
        copied_tables = []
        sqlite_connect = sqlite3.connect

        # Mock's side_effect callback records the real handles so closure can be
        # checked directly, including the read-only preflight connection.
        def record_connection(*args, **kwargs):
            connection = sqlite_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        # Mock requires a callback to fail after an actual successful copy.
        # The synthetic sidecars belong only to this disposable candidate.
        def fail_during_copy(target, table, query, params=()):
            if copied_tables:
                self.assertGreater(target.execute("SELECT COUNT(*) FROM main.metadata").fetchone()[0], 0)
                for suffix in ("-journal", "-wal", "-shm"):
                    with Path(str(self.output) + suffix).open("xb") as handle:
                        handle.write(b"owned partial candidate fixture")
                raise RuntimeError("fixture failure after one successful table copy")
            copy_query(target, table, query, params)
            copied_tables.append(table)

        with patch("build_replication_database.sqlite3.connect", side_effect=record_connection), \
                patch("build_replication_database.copy_query", side_effect=fail_during_copy) as copy:
            with self.assertRaisesRegex(RuntimeError, "after one successful table copy"):
                build_database(self.database, self.output, self.spec_path)
        self.assertEqual(copied_tables, ["metadata"])
        self.assertEqual(copy.call_count, 2)
        self.assertEqual(len(connections), 2)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            self.assertEqual(old_run, connection.execute("SELECT * FROM runs WHERE run_id='historical_sentinel'").fetchall())
            self.assertEqual(old_audit, connection.execute("SELECT * FROM referee_human_audit_tasks").fetchall())
        self.assertEqual(neighbor_bytes, {path: path.read_bytes() for path in neighbors})
        for suffix in ("", ".manifest.json", "-journal", "-wal", "-shm"):
            self.assertFalse(Path(str(self.output) + suffix).exists())
        audits = list(Path(self.spec["paths"]["logs"]).glob("candidate_export_*.json"))
        self.assertEqual(len(audits), 1)
        audit = json.loads(audits[0].read_text())
        self.assertEqual((audit["status"], audit["error_type"]), ("FAIL", "RuntimeError"))
        self.assertIn("after one successful table copy", audit["error"])


if __name__ == "__main__":
    unittest.main()
