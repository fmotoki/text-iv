"""Offline research invariants on disposable, explicitly labelled fixtures."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import hashlib
import json
import os
import tempfile
import subprocess
import unittest

from prompt_iv.agents.common_prior import (
    prepare_original_prior_run, execute_original_prior_run,
    prepare_common_prior_run, execute_common_prior_run,
    prepare_arm_specific_prior_run, execute_arm_specific_prior_run,
)
from prompt_iv.agents.targets import inferred_year_for_meeting, inflation_target_for_meeting
from prompt_iv.generation.corpus import assemble_counterfactual_corpus
from prompt_iv.generation.execution import execute_rewrite_manifest
from prompt_iv.generation.tasks import build_rewrite_tasks
from prompt_iv.io.jsonl import read_jsonl
from prompt_iv.llm.runner import CodexCliRunner, DryRunRunner, LLMResponse
from prompt_iv.paths import load_spec, validate_artifact_path
from prompt_iv.cli import main as cli_main
from prompt_iv.pipelines.replication import prepare_replication_run
from prompt_iv.storage.sqlite import connect_database, sync_minutes, sync_run


SOURCE_ROOT = Path(__file__).resolve().parents[3]


class TerraCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="terra56-collection-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name)
        self.run = self.fixture / "run"
        self.spec_path = self.fixture / "fixture_spec.json"
        self.spec = json.loads((SOURCE_ROOT / "code/replication_spec.json").read_text(encoding="utf-8-sig"))
        self.spec.update(design_type="qa_fixture", artifact_root=str(self.fixture),
                         run_root=str(self.run), fingerprint="a" * 64)
        self.spec["sample"].update(first_meeting=203, last_meeting=204, meeting_numbers=[203, 204])
        self.spec["draws"] = {"main": 1, "dose": 1}
        self.spec["execution"] = {"allow_remote_calls": True, "full_production_authorized": False}
        source_file = "code/collection/src/prompt_iv/agents/targets.py"
        self.spec["source_hashes"] = {source_file: hashlib.sha256((SOURCE_ROOT / source_file).read_bytes()).hexdigest()}
        date_file = self.spec["sample"]["dates_csv"]
        self.spec["input_hashes"] = {date_file: hashlib.sha256(Path(date_file).read_bytes()).hexdigest()}
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        self.run.mkdir()
        (self.run / "effective_spec.json").write_text(json.dumps(self.spec), encoding="utf-8")
        environment = patch.dict(os.environ, REPLICATION_SPEC_PATH=str(self.spec_path))
        environment.start()
        self.addCleanup(environment.stop)
        self.effective = load_spec(require_frozen=True)
        self.database = Path(self.effective["paths"]["database"])
        self.minutes = self.fixture / "minutes"
        self.minutes.mkdir()
        for meeting in (202, 203, 204, 281, 282):
            # The established segmenter uses one block for at most five lines.
            text = "\n\n".join(f"Paragrafo {index}. A inflacao foi 3,0 por cento." for index in range(1, 11))
            (self.minutes / f"ata{meeting}.txt").write_text(text, encoding="utf-8")

    def test_exact_selection_and_year_boundary(self):
        tasks = build_rewrite_tasks(self.minutes, meeting_numbers=[203, 281],
                                    iterations=1, parts=5, prompt_version="calibrated_symmetric_rebalance_v10")
        self.assertEqual({task.meeting_number for task in tasks}, {203, 281})
        self.assertEqual(len(tasks), 20)
        self.assertEqual([inferred_year_for_meeting(m) for m in (275, 276, 277, 281)],
                         [2025, 2026, 2026, 2026])
        self.assertEqual(inflation_target_for_meeting(281), 3.0)
        with self.assertRaises(ValueError):
            inferred_year_for_meeting(282)
        with self.assertRaises(ValueError):
            build_rewrite_tasks(self.minutes, meeting_numbers=[203, 205])

    def test_path_and_frozen_database_protection(self):
        with self.assertRaises(ValueError):
            validate_artifact_path(SOURCE_ROOT / "results/forbidden.csv")
        with self.assertRaises(ValueError):
            validate_artifact_path("outputs/legacy.csv")
        with self.assertRaises(ValueError):
            connect_database(self.effective["paths"]["frozen_database"])
        self.spec["run_root"] = str(SOURCE_ROOT)
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_spec()

    def test_cli_direct_writer_rejects_frozen_output(self):
        frozen = Path(self.effective["paths"]["frozen_database"])
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_bytes(b"frozen-target-sentinel")
        with self.assertRaises(ValueError):
            cli_main(["--root", str(SOURCE_ROOT), "summarize-data", "--output", str(frozen)])
        self.assertEqual(frozen.read_bytes(), b"frozen-target-sentinel")

    def test_cli_calibration_summary_uses_current_report_contract(self):
        # The mocked estimator isolates the CLI's report contract from estimation.
        report = {"n_meetings": 79, "mixture": {"r2": 0.2}, "llm_fit": {"r2": 0.3},
                  "focus_ar": {"beta_focus": 0.1, "rho_focus": 0.8},
                  "design_status": "qa_fixture"}
        with patch("prompt_iv.cli.estimate_baseline", return_value=report) as estimator:
            status = cli_main(["--root", str(SOURCE_ROOT), "estimate-baseline",
                "--dataset", str(self.run / "outputs/work/calibration_data.dta"),
                "--output", str(self.run / "outputs/tables/calibration.json")])
        self.assertEqual(status, 0)
        estimator.assert_called_once()

    def test_symlink_escape(self):
        self.run.mkdir(exist_ok=True)
        target = self.fixture / "outside"
        target.mkdir()
        try:
            (self.run / "escape").symlink_to(target, target_is_directory=True)
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 1314:
                raise
            # Windows junctions do not require the symbolic-link privilege.
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"),
                            "/c", "mklink", "/J", str(self.run / "escape"), str(target)],
                           capture_output=True, text=True, check=True)
        with self.assertRaises(ValueError):
            validate_artifact_path(self.run / "escape/result.csv")

    def test_database_fingerprint_is_immutable(self):
        connection = connect_database(self.database)
        connection.close()
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.spec["fingerprint"] = "b" * 64
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        with self.assertRaises(ValueError):
            connect_database(self.database)
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).hexdigest(), before)

    def test_rewrite_resume_and_dry_run_transition(self):
        run_id = self.spec["run_ids"]["main_text"]
        directory = Path(self.effective["paths"]["collections"]) / run_id
        prepared = prepare_replication_run(SOURCE_ROOT, run_id, self.minutes,
            iterations=1, parts=5, meeting_numbers=[203, 204], run_directory=directory,
            prompt_version="calibrated_symmetric_rebalance_v10")
        manifest = Path(prepared["config"]["rewrite_manifest"])
        output = directory / "rewrite_results.jsonl"
        execute_rewrite_manifest(manifest, output, DryRunRunner(), limit=1)
        tasks = build_rewrite_tasks(self.minutes, meeting_numbers=[203, 204],
            iterations=1, parts=5, prompt_version="calibrated_symmetric_rebalance_v10")
        texts = {task.task_id: task.original_text for task in tasks}
        runner = Mock()
        runner.name = "qa_fixture"
        # Mock's side_effect API requires a request callback.
        runner.generate.side_effect = lambda request: LLMResponse(
            text=texts[request.metadata["task_id"]], runner="qa_fixture", model=request.model,
            input_hash=request.input_hash, created_at="fixture", dry_run=False,
            metadata={"returncode": 0, "fixture": True})
        result = execute_rewrite_manifest(manifest, output, runner)
        self.assertEqual(result["attempted"], 20)
        self.assertEqual(runner.generate.call_count, 20)
        self.assertEqual(execute_rewrite_manifest(manifest, output, runner)["attempted"], 0)
        corpus = assemble_counterfactual_corpus(output, manifest_path=manifest)
        self.assertEqual(len(corpus), 2)
        with self.assertRaises(ValueError):
            execute_rewrite_manifest(manifest, output, runner, temperature=0.5)
        with self.assertRaises(ValueError):
            execute_rewrite_manifest(manifest, output, runner, model="other-model")
        with self.assertRaises(ValueError):
            execute_rewrite_manifest(manifest, output, runner, reasoning_effort="low")
        rows = read_jsonl(manifest)
        rows[0]["user_prompt_hash"] = "changed"
        manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            execute_rewrite_manifest(manifest, output, runner)
        rows[0]["user_prompt_hash"] = tasks[0].manifest_record()["user_prompt_hash"]
        manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        (self.minutes / "ata203.txt").write_text("Changed source text.", encoding="utf-8")
        with self.assertRaises(ValueError):
            execute_rewrite_manifest(manifest, output, runner)
        self.assertEqual(runner.generate.call_count, 20)

    def test_original_chronology_pair_priors_and_recursive_state(self):
        connection = connect_database(self.database)
        with connection:
            sync_minutes(connection, SOURCE_ROOT, self.minutes)
            connection.execute("INSERT INTO runs(run_id,run_dir,metadata_json,ingested_at) VALUES('historical_sentinel','legacy','{}','fixture')")
            connection.execute("INSERT INTO runs(run_id,run_dir,metadata_json,ingested_at) VALUES(?,?,?,?)",
                (self.spec["run_ids"]["dose_text"], "qa_fixture", "{}", "fixture"))
            for meeting in (203, 204):
                text = (self.minutes / f"ata{meeting}.txt").read_text(encoding="utf-8")
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                for role in self.spec["roles"]["dose"]:
                    connection.execute("INSERT INTO corpus_texts(run_id,meeting_number,iteration,role,source_path,complete,text,text_hash,char_count,word_count) VALUES(?,?,?,?,?,1,?,?,?,?)",
                        (self.spec["run_ids"]["dose_text"], meeting, 1, role, str(self.minutes / f"ata{meeting}.txt"), text, text_hash, len(text), len(text.split())))
        connection.close()
        prior = self.spec["run_ids"]["original_priors"]
        with self.assertRaises(ValueError):
            prepare_original_prior_run(self.database, prior, meeting_numbers=[204])
        with self.assertRaises(ValueError):
            prepare_original_prior_run(self.database, prior, meeting_numbers=[203, 204], initial_expectation=4.0)
        prepare_original_prior_run(self.database, prior, meeting_numbers=[203, 204])
        runner = Mock()
        runner.name = "qa_fixture"
        # The fixture LLM protocol supplies deterministic responses without a provider.
        runner.generate.side_effect = lambda request: LLMResponse(
            text=json.dumps({"nova_expectativa_inflacao": 4.1 if request.metadata["meeting_number"] == 203 else 4.2}),
            runner="qa_fixture", model=request.model, input_hash=request.input_hash,
            created_at="fixture", dry_run=False, metadata={"returncode": 0, "response_schema_applied": True})
        execute_original_prior_run(self.database, prior, runner=runner)
        count = runner.generate.call_count
        self.assertEqual(count, 4)
        self.assertEqual(execute_original_prior_run(self.database, prior, runner=runner)["attempted"], 0)
        common = self.spec["run_ids"]["common_prior_dose"]
        prepare_common_prior_run(self.database, common, source_run_id=self.spec["run_ids"]["dose_text"],
            prior_run_id=prior, meeting_numbers=[203, 204], iterations=[1], roles=self.spec["roles"]["dose"])
        execute_common_prior_run(self.database, common, runner=runner)
        recursive = self.spec["run_ids"]["recursive_dose"]
        prepare_arm_specific_prior_run(self.database, recursive, source_run_id=self.spec["run_ids"]["dose_text"],
            meeting_numbers=[203, 204], iterations=[1])
        execute_arm_specific_prior_run(self.database, recursive, runner=runner)
        connection = connect_database(self.database)
        try:
            for run_id in (prior, common, recursive):
                rows = connection.execute("SELECT t.meeting_number,r.previous_expectation FROM agent_tasks t JOIN agent_results_latest r USING(run_id,task_id) WHERE t.run_id=? ORDER BY t.meeting_number", (run_id,)).fetchall()
                self.assertTrue(rows)
                self.assertTrue(all(abs(value - (3.8 if meeting == 203 else 4.1)) < 1e-12 for meeting, value in rows))
            self.assertEqual(connection.execute("SELECT metadata_json FROM runs WHERE run_id='historical_sentinel'").fetchone()[0], "{}")
        finally:
            connection.close()

    def test_timeout_reserves_attempt_across_runner_restart(self):
        from prompt_iv.llm.runner import LLMRequest
        log = self.run / "budget/requests.jsonl"
        first = CodexCliRunner(allow_real_llm=True, log_path=log, max_attempts=1)
        first._available_cache = (True, "fixture-codex")
        request = LLMRequest(system_prompt="fixture", user_prompt="fixture")
        with patch("prompt_iv.llm.runner.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 1)) as invoke:
            with self.assertRaises(subprocess.TimeoutExpired):
                first.generate(request)
            second = CodexCliRunner(allow_real_llm=True, log_path=log, max_attempts=1)
            second._available_cache = first._available_cache
            with self.assertRaisesRegex(ValueError, "persistent live-attempt cap"):
                second.generate(request)
            self.assertEqual(invoke.call_count, 1)
        self.assertEqual(len(list(log.with_suffix(".attempts").glob("*.json"))), 1)
        self.assertFalse(log.exists())

    def test_configuration_warning_is_not_a_tool_event(self):
        from prompt_iv.llm.runner import LLMRequest
        runner = CodexCliRunner(allow_real_llm=True)
        runner._available_cache = (True, "fixture-codex")
        events = [{"type": "item.completed", "item": {"type": "error", "message": "Codex is ignoring an unrecognized configuration setting."}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 12}}]
        # The subprocess callback supplies saved events without a paid request.
        def fixture_process(arguments, **kwargs):
            Path(arguments[arguments.index("-o") + 1]).write_text("fixture answer", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(event) for event in events).encode(), stderr=b"")
        with patch("prompt_iv.llm.runner.subprocess.run", side_effect=fixture_process):
            result = runner.generate(LLMRequest(system_prompt="fixture", user_prompt="fixture"))
        self.assertEqual(result.metadata["returncode"], 0)
        self.assertTrue(result.metadata["tool_event_audit_passed"])

    def test_codex_usage_and_tool_event_rejection_without_remote_calls(self):
        runner = CodexCliRunner(allow_real_llm=True)
        runner._available_cache = (True, "fixture-codex")
        runner._version = "fixture-version"
        events = [{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 25, "output_tokens": 12}},
                  {"type": "item.completed", "item": {"type": "command_execution", "command": "forbidden"}}]
        # subprocess.run's mock callback creates only its owned last-message fixture.
        def fixture_process(arguments, **kwargs):
            Path(arguments[arguments.index("-o") + 1]).write_text("fixture answer", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(event) for event in events).encode("utf-8"), stderr=b"")
        from prompt_iv.llm.runner import LLMRequest
        with patch("prompt_iv.llm.runner.subprocess.run", side_effect=fixture_process):
            with patch.dict(os.environ, PROMPT_IV_CODEX_SERVICE_TIER="fast"):
                result = runner.generate(LLMRequest(system_prompt="fixture", user_prompt="fixture"))
        self.assertEqual(result.metadata["returncode"], 1)
        self.assertFalse(result.metadata["tool_event_audit_passed"])
        self.assertEqual(result.metadata["usage_events"][0]["usage"]["cached_input_tokens"], 25)
        self.assertFalse(result.metadata["backend_model_independently_verified"])
        self.assertEqual(result.metadata["requested_service_tier"], "default")
        self.assertIn('service_tier="default"', result.metadata["command_configuration"])


if __name__ == "__main__":
    unittest.main()
