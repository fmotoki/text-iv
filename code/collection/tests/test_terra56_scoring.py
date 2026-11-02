"""Focused offline scoring, source-cohort and persistence fixtures."""
import copy
from contextlib import closing
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
import numpy as np
from unittest.mock import patch

sys.dont_write_bytecode=True
SOURCE_ROOT=Path(__file__).resolve().parents[3]
COLLECTION=SOURCE_ROOT/"code/collection"
sys.path.insert(0,str(COLLECTION/"src"))
from prompt_iv.llm.runner import LLMResponse
from prompt_iv.io.jsonl import read_jsonl,write_jsonl
from prompt_iv.paths import load_spec,specification_metadata
from prompt_iv.storage.sqlite import connect_database

# Import existing scripts through the documented importlib loader protocol.
modules={}
for name,filename in (("blind","run_v10_referee_step3_blinded_validation.py"),("postprocess","postprocess_v10_referee_step3_db.py"),("embedding","compute_referee_embeddings_db.py")):
    module_spec=importlib.util.spec_from_file_location(name,COLLECTION/"scripts"/filename)
    module=importlib.util.module_from_spec(module_spec)
    sys.modules[name]=module
    module_spec.loader.exec_module(module)
    modules[name]=module
blind=modules["blind"]
embedding=modules["embedding"]
RSCRIPT=Path("C:/Users/csg690/AppData/Local/Programs/R/R-4.5.3/bin/Rscript.exe")


class ScoringFixture(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="terra56_scores_")
        self.addCleanup(self.temporary.cleanup)
        self.fixture=Path(self.temporary.name)
        self.run=self.fixture/"run"
        self.run.mkdir()
        self.spec=json.loads((SOURCE_ROOT/"code/replication_spec.json").read_text(encoding="utf-8"))
        self.spec.update(design_type="qa_fixture",run_root=str(self.run),fingerprint="b"*64,release_id="terra56_qa_scoring")
        self.spec["sample"].update(first_meeting=203,last_meeting=203,meeting_numbers=[203])
        self.spec["draws"]={"main":1,"dose":1}
        self.spec["pairwise_pairs"]={f"{left}_vs_{right}":[left,right] for left,right in blind.PAIR_SPECS}
        self.spec["source_hashes"]={"code/collection/scripts/run_v10_referee_step3_blinded_validation.py":hashlib.sha256((COLLECTION/"scripts/run_v10_referee_step3_blinded_validation.py").read_bytes()).hexdigest()}
        dates=Path(self.spec["sample"]["dates_csv"])
        self.spec["input_hashes"]={str(dates):hashlib.sha256(dates.read_bytes()).hexdigest()}
        self.specfile=self.fixture/"spec.json"
        self.specfile.write_text(json.dumps(self.spec),encoding="utf-8")
        (self.run/"effective_spec.json").write_bytes(self.specfile.read_bytes())
        self.environment=patch.dict(os.environ,{"REPLICATION_SPEC_PATH":str(self.specfile),"PYTHONDONTWRITEBYTECODE":"1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.effective=load_spec(require_frozen=True)
        self.corpus=Path(self.effective["paths"]["collections"])/self.spec["run_ids"]["dose_text"]/"counterfactual_corpus.csv"
        self.corpus.parent.mkdir(parents=True)
        self.texts={role:f"Texto institucional de politica monetaria. A inflacao permanece em 3,0 por cento. Papel {role}." for role in blind.ROLES}
        row=dict(meeting_number=203,iteration=1,complete=True,source_path="labelled synthetic fixture")
        row.update({blind.ROLE_COLUMNS[role]:text for role,text in self.texts.items()})
        with self.corpus.open("w",encoding="utf-8",newline="") as output:
            writer=csv.DictWriter(output,fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        self.directory=self.run/"data/interim/dose_response"/self.spec["run_ids"]["blinded_scoring"]
        self.directory.mkdir(parents=True)
        self.semantic_manifest=self.directory/"semantic_tone_manifest.jsonl"
        self.pairwise_manifest=self.directory/"pairwise_tone_manifest.jsonl"
        self.semantic_results=self.directory/"semantic_tone_results.jsonl"
        self.pairwise_results=self.directory/"pairwise_tone_results.jsonl"
        blind.build_semantic_manifest(self.corpus,self.semantic_manifest,123)
        blind.build_pairwise_manifest(self.corpus,self.pairwise_manifest,456)

    def test_exact_manifests_and_model_propagation(self):
        semantic=read_jsonl(self.semantic_manifest)
        pairs=read_jsonl(self.pairwise_manifest)
        self.assertEqual(len(semantic),6)
        self.assertEqual(len(pairs),5)
        self.assertEqual({r["text_role"] for r in semantic},set(blind.ROLES))
        self.assertEqual({r["source_run_id"] for r in semantic},{self.spec["run_ids"]["dose_text"]})
        self.assertEqual({r["model"] for r in semantic},{"gpt-5.6-terra"})
        self.assertTrue(all(r["prompt_hash"] and r["request_input_hash"] for r in semantic+pairs))
        with self.assertRaises(ValueError):blind.execute_semantic(self.semantic_manifest,self.semantic_results,None,"gpt-5.5","high",1.0,None,True,1)
        changed=copy.deepcopy(semantic)
        changed[0]["text"]+=" changed"
        write_jsonl(self.semantic_manifest,changed)
        with self.assertRaises(ValueError):blind.execute_semantic(self.semantic_manifest,self.semantic_results,None,"gpt-5.6-terra","high",1.0,None,True,1)

    def test_resume_dry_run_promotion_and_changed_prompt(self):
        class Runner:
            name="fixture"
            dry=False
            calls=0
            # LLMRunner.generate is the required callback. No external model is called.
            def generate(inner,request):
                inner.calls+=1
                return LLMResponse(json.dumps(dict(stance_score=0,label="neutral",confidence=1)),"fixture",request.model,request.input_hash,"fixture",inner.dry,{"returncode":0})
        runner=Runner()
        runner.dry=True
        first=blind.execute_semantic(self.semantic_manifest,self.semantic_results,runner,"gpt-5.6-terra","high",1.0,None,True,1)
        self.assertEqual(first["dry_run"],6)
        runner.dry=False
        second=blind.execute_semantic(self.semantic_manifest,self.semantic_results,runner,"gpt-5.6-terra","high",1.0,None,True,1)
        self.assertEqual(second["completed"],6)
        third=blind.execute_semantic(self.semantic_manifest,self.semantic_results,runner,"gpt-5.6-terra","high",1.0,None,True,1)
        self.assertEqual(third["attempted"],0)
        self.assertEqual(runner.calls,12)
        with patch.object(blind,"SEMANTIC_SYSTEM_PROMPT",blind.SEMANTIC_SYSTEM_PROMPT+" changed"):
            with self.assertRaises(ValueError):blind.execute_semantic(self.semantic_manifest,self.semantic_results,runner,"gpt-5.6-terra","high",1.0,None,True,1)

    def test_failed_runner_and_nonfinite_scores(self):
        class Runner:
            name="fixture"
            def generate(inner,request):
                return LLMResponse(json.dumps(dict(stance_score=0,label="neutral",confidence=1)),"fixture",request.model,request.input_hash,"fixture",False,{"returncode":1,"forbidden_tool":True})
        result=blind.execute_semantic(self.semantic_manifest,self.semantic_results,Runner(),"gpt-5.6-terra","high",1.0,None,True,1)
        self.assertEqual(result["failed"],6)
        with self.assertRaises(ValueError):blind.clean_stance_score(float("nan"))
        with self.assertRaises(ValueError):blind.clean_stance_score(1.1)

    def test_realized_hash_and_missing_counterpart_fail_closed(self):
        semantic=[]
        for task in read_jsonl(self.semantic_manifest):
            semantic.append(dict(task,status="completed",runner_dry_run=False,temperature=1.0,stance_score=0,label="neutral",confidence=1,raw_response_hash="c"*64,created_at="fixture",response_metadata={"returncode":0}))
        pairs=[]
        for task in read_jsonl(self.pairwise_manifest):
            pairs.append(dict(task,status="completed",runner_dry_run=False,temperature=1.0,more_hawkish="tie",more_hawkish_role="tie",confidence=1,raw_response_hash="c"*64,created_at="fixture",response_metadata={"returncode":0}))
        write_jsonl(self.semantic_results,semantic)
        write_jsonl(self.pairwise_results,pairs)
        output=self.directory/"realized_dose_dataset.csv"
        data=blind.build_realized_dataset(self.corpus,self.semantic_results,self.pairwise_results,output)
        self.assertEqual(len(data),6)
        self.assertEqual(set(data.semantic_score),{0})
        self.assertEqual(set(data.run_id),{self.spec["run_ids"]["dose_text"]})
        # Constant scores are valid. Outcome variation is not an acquisition gate.
        write_jsonl(self.pairwise_results,pairs[:-1])
        with self.assertRaises(ValueError):blind.build_realized_dataset(self.corpus,self.semantic_results,self.pairwise_results,output)
        write_jsonl(self.pairwise_results,pairs)
        semantic[0]["text_hash"]="d"*64
        write_jsonl(self.semantic_results,semantic)
        with self.assertRaises(ValueError):blind.build_realized_dataset(self.corpus,self.semantic_results,self.pairwise_results,output)

    @unittest.skipUnless(RSCRIPT.exists(),"Documented Rscript unavailable")
    def test_transactional_import_preserves_old_sentinels(self):
        # Build a labelled complete score fixture without executing a scorer.
        semantic=[dict(task,status="completed",runner_dry_run=False,temperature=1.0,stance_score=0,label="neutral",confidence=1,raw_response_hash="c"*64,created_at="fixture",response_metadata={"returncode":0}) for task in read_jsonl(self.semantic_manifest)]
        pairs=[dict(task,status="completed",runner_dry_run=False,temperature=1.0,more_hawkish="tie",more_hawkish_role="tie",confidence=1,raw_response_hash="c"*64,created_at="fixture",response_metadata={"returncode":0}) for task in read_jsonl(self.pairwise_manifest)]
        write_jsonl(self.semantic_results,semantic)
        write_jsonl(self.pairwise_results,pairs)
        blind.build_realized_dataset(self.corpus,self.semantic_results,self.pairwise_results,self.directory/"realized_dose_dataset.csv")
        database=Path(self.effective["paths"]["database"])
        database.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata VALUES(?,?)",("replication_specification",json.dumps(specification_metadata(),sort_keys=True)))
            connection.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY,run_dir TEXT NOT NULL,first_meeting INTEGER,last_meeting INTEGER,metadata_json TEXT,ingested_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE corpus_texts(run_id TEXT,meeting_number INTEGER,iteration INTEGER,role TEXT,text_hash TEXT,complete INTEGER)")
            for role,text in self.texts.items():connection.execute("INSERT INTO corpus_texts VALUES(?,?,?,?,?,1)",(self.spec["run_ids"]["dose_text"],203,1,role,hashlib.sha256(text.encode()).hexdigest()))
            connection.execute("CREATE TABLE blinded_semantic_scores(run_id TEXT,score REAL)")
            connection.execute("INSERT INTO blinded_semantic_scores VALUES('old_run',0.7)")
            connection.execute("CREATE TABLE realized_dose_scores(run_id TEXT,scorer_run_id TEXT,semantic_score REAL)")
            connection.execute("INSERT INTO realized_dose_scores VALUES('old_run','old_scorer',0.7)")
            connection.commit()
        command=[str(RSCRIPT),"--vanilla",str(COLLECTION/"scripts/migrate_blinded_validation_to_db.R"),"--database",str(database),"--run-id",self.spec["run_ids"]["blinded_scoring"],"--source-run-id",self.spec["run_ids"]["dose_text"],"--validation-dir",str(self.directory)]
        environment=dict(os.environ,http_proxy="http://127.0.0.1:1",https_proxy="http://127.0.0.1:1")
        for repeat in range(2):
            result=subprocess.run(command,env=environment,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT score FROM blinded_semantic_scores WHERE run_id='old_run'").fetchone()[0],0.7)
            self.assertEqual(connection.execute("SELECT scorer_run_id FROM realized_dose_scores WHERE run_id='old_run'").fetchone()[0],"old_scorer")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM blinded_semantic_scores WHERE run_id=?",(self.spec["run_ids"]["blinded_scoring"],)).fetchone()[0],6)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM realized_dose_scores WHERE run_id=?",(self.spec["run_ids"]["dose_text"],)).fetchone()[0],6)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs WHERE run_id=?",(self.spec["run_ids"]["blinded_scoring"],)).fetchone()[0],1)

        # Keep all cache/model/prompt/request fields valid while supplying a
        # different text under a selected corpus role. The R entry point must
        # independently reject either side before replacing any database rows.
        tables=("metadata","runs","corpus_texts","blinded_semantic_tasks","blinded_semantic_scores","blinded_pairwise_tasks","blinded_pairwise_scores","realized_dose_scores")
        with closing(sqlite3.connect(database)) as connection:
            before={table:connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
        original_pair_tasks=read_jsonl(self.pairwise_manifest)
        for arm in ("a","b"):
            with self.subTest(pair_side=arm):
                changed_tasks=copy.deepcopy(original_pair_tasks)
                changed_scores=copy.deepcopy(pairs)
                changed_tasks[0][f"text_{arm}"]="Different synthetic text outside the selected corpus role."
                changed_tasks[0][f"text_{arm}_hash"]=hashlib.sha256(changed_tasks[0][f"text_{arm}"].encode("utf-8")).hexdigest()
                changed_tasks[0]["request_input_hash"]=blind.pairwise_request(changed_tasks[0],self.spec["model"],self.spec["reasoning_effort"],1.0).input_hash
                changed_scores[0].update(changed_tasks[0])
                write_jsonl(self.pairwise_manifest,changed_tasks)
                write_jsonl(self.pairwise_results,changed_scores)
                result=subprocess.run(command,env=environment,capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(f"Pairwise text_{arm}_hash differs from selected SQLite corpus",result.stderr)
                with closing(sqlite3.connect(database)) as connection:
                    after={table:connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
                self.assertEqual(before,after)

    def test_embedding_selected_fetch_and_new_manifest(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE corpus_texts(run_id TEXT,meeting_number INTEGER,iteration INTEGER,role TEXT,text_hash TEXT,text TEXT,complete INTEGER)")
            connection.execute("INSERT INTO corpus_texts VALUES('selected',203,1,'original','hash','text',1)")
            connection.execute("INSERT INTO corpus_texts VALUES('old',203,1,'original','oldhash','oldtext',1)")
            connection.execute("CREATE TABLE referee_embedding_manifest(model_id TEXT PRIMARY KEY,generated_at TEXT)")
            connection.execute("INSERT INTO referee_embedding_manifest VALUES('oldmodel','oldtime')")
            self.assertEqual([row.run_id for row in embedding.fetch_rows(connection,("selected",))],["selected"])
            embedding.prepare_schema(connection)
            self.assertEqual(connection.execute("SELECT * FROM referee_embedding_manifest").fetchall(),[("oldmodel","oldtime")])
            columns={row[1] for row in connection.execute("PRAGMA table_info(referee_embedding_run_manifest)")}
            self.assertTrue({"run_id","model_id","model_revision","spec_fingerprint","corpus_sha256"}.issubset(columns))

    def test_postprocess_assembles_complete_blocks(self):
        module=modules["postprocess"]
        rows=[]
        for stance in module.REQUIRED_STANCES:
            for block in range(1,6):rows.append(dict(meeting_number=203,iteration=1,block_index=block,total_blocks=5,source_path="fixture",stance=stance,status="completed",numeric_ok=True,generated_text=f"Bloco {block} texto {stance}"))
        with patch.object(module,"read_text",return_value="Original fixture text"):
            corpus=module.assemble_corpus(rows,self.run/"assembled.csv")
        self.assertEqual(len(corpus),1)
        self.assertEqual(set(corpus.columns)&set(module.TEXT_COLUMNS),set(module.TEXT_COLUMNS))
        self.assertTrue(all(corpus.iloc[0][f"{role}_text"].count("Bloco")==5 for role in module.REQUIRED_STANCES))

    def test_embedding_recompute_preserves_old_model_rows(self):
        database=Path(self.effective["paths"]["database"])
        with closing(connect_database(database)) as connection:
            for kind,roles in (("main_text",("original","hawk","dove")),("dose_text",blind.ROLES)):
                run_id=self.spec["run_ids"][kind]
                connection.execute("INSERT INTO runs(run_id,run_dir,metadata_json,ingested_at) VALUES(?,?,?,?)",(run_id,str(self.run),json.dumps({"replication_config":{"fingerprint":self.spec["fingerprint"]}}),"fixture"))
                for role in roles:
                    text=f"Texto original de politica monetaria e inflacao. Papel {role}."
                    connection.execute("INSERT INTO corpus_texts(run_id,meeting_number,iteration,role,source_path,complete,text,text_hash,char_count,word_count) VALUES(?,203,1,?,'fixture',1,?,?,?,?)",(run_id,role,text,hashlib.sha256(text.encode()).hexdigest(),len(text),len(text.split())))
            embedding.prepare_schema(connection)
            connection.execute("INSERT INTO referee_text_embeddings VALUES('old_run',203,1,'original','oldhash',?,384,1,1,1,?,'oldtime')",(embedding.MODEL_ID,sqlite3.Binary(b"old")))
            connection.execute("INSERT INTO referee_embedding_run_manifest VALUES('old_run',?,'oldrevision','oldspec','oldcorpus','oldversion','oldtokenizer','oldpool','oldsample',12,80,6,'oldtime')",(embedding.MODEL_ID,))
            connection.commit()
        # Mock only the external model/tokenizer callbacks. Persistence is real SQLite.
        fake_embedder=SimpleNamespace(embed=lambda segments,batch_size:iter(np.ones((len(segments),384),dtype=np.float32)))
        fake_tokenizer=SimpleNamespace(no_truncation=lambda:None,encode=lambda text,add_special_tokens:SimpleNamespace(ids=[1,2,3]))
        external={"fastembed":SimpleNamespace(TextEmbedding=lambda **kwargs:fake_embedder),"tokenizers":SimpleNamespace(Tokenizer=SimpleNamespace(from_pretrained=lambda model:fake_tokenizer))}
        arguments=["embeddings","--database",str(database),"--source-run-ids",self.spec["run_ids"]["main_text"]+","+self.spec["run_ids"]["dose_text"]]
        with patch.dict(sys.modules,external),patch.object(sys,"argv",arguments),patch.object(embedding.importlib.metadata,"version",return_value="fixture_version"):
            embedding.main()
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT text_hash,embedding_blob FROM referee_text_embeddings WHERE run_id='old_run'").fetchone(),("oldhash",b"old"))
            self.assertEqual(connection.execute("SELECT model_revision,spec_fingerprint FROM referee_embedding_run_manifest WHERE run_id='old_run'").fetchone(),("oldrevision","oldspec"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM referee_text_embeddings WHERE run_id<>'old_run'").fetchone()[0],9)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM referee_embedding_pair_metrics WHERE run_id<>'old_run'").fetchone()[0],7)


    def test_alternate_three_role_protocol_remains_separate(self):
        from prompt_iv.dose_response import pipeline
        corpus=Path(self.effective["paths"]["collections"])/self.spec["run_ids"]["main_text"]/"counterfactual_corpus.csv"
        corpus.parent.mkdir(parents=True,exist_ok=True)
        row=dict(meeting_number=203,iteration=1,complete=True)
        row.update({column:"Texto de politica monetaria sobre precos e inflacao. "+role for role,column in pipeline.TEXT_COLUMNS.items()})
        with corpus.open("w",encoding="utf-8",newline="") as output:
            writer=csv.DictWriter(output,fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        directory=self.run/"main_three_role"
        directory.mkdir()
        semantic_manifest=directory/"semantic_tone_manifest.jsonl"
        pairwise_manifest=directory/"pairwise_tone_manifest.jsonl"
        pipeline.build_semantic_manifest(corpus,semantic_manifest)
        pipeline.build_pairwise_manifest(corpus,pairwise_manifest)
        semantic=[]
        for task in read_jsonl(semantic_manifest):semantic.append(dict(task,status="completed",runner_dry_run=False,stance_score=0,label="neutral",confidence=1,raw_response_hash="c"*64,response_metadata={"returncode":0}))
        pairs=[]
        for task in read_jsonl(pairwise_manifest):pairs.append(dict(task,status="completed",runner_dry_run=False,more_hawkish="tie",more_hawkish_role="tie",confidence=1,raw_response_hash="c"*64,response_metadata={"returncode":0}))
        semantic_path=directory/"semantic_tone_results.jsonl"
        pairwise_path=directory/"pairwise_tone_results.jsonl"
        write_jsonl(semantic_path,semantic)
        write_jsonl(pairwise_path,pairs)
        result=pipeline.build_realized_dose_dataset(corpus,semantic_path,pairwise_path,directory/"dataset.csv")
        self.assertEqual(len(result),3)
        self.assertEqual(set(result.scoring_protocol),{"three_role_alternate"})
        with self.assertRaises(ValueError):pipeline.build_semantic_manifest(self.corpus,self.directory/"not_three_role.jsonl")


if __name__=="__main__":
    unittest.main(verbosity=2)
