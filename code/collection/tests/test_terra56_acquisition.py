"""Bounded offline acquisition and strict release-join fixtures. No model calls."""
import copy
from contextlib import closing
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs

SOURCE_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = SOURCE_ROOT / "code/01_data_preparation/download_minutes.py"
sys.dont_write_bytecode=True
module_spec=importlib.util.spec_from_file_location("bcb_acquisition",MODULE_PATH)
bcb=importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(bcb)
# Coauthors can use Rscript on PATH or name their installed interpreter once.
rscript_command=os.environ.get("RSCRIPT_EXECUTABLE","Rscript")
RSCRIPT=Path(shutil.which(rscript_command) or rscript_command)


class AcquisitionContracts(unittest.TestCase):
    # unittest callbacks isolate each destructive fixture from the source package.
    def test_sgs_schema_and_duplicates(self):
        rows=bcb.normalize_sgs([{"data":"01/01/2026","valor":"1"},{"data":"01/01/2026","valor":"1"}],24364,"url","stamp")
        self.assertEqual(len(rows),1)
        for payload in ([{"data":"01/01/2026","valor":"nan"}],[{"data":"01/01/2026","valor":"1"},{"data":"01/01/2026","valor":"2"}],{"error":"bad"}):
            with self.assertRaises(ValueError): bcb.normalize_sgs(payload,24364,"url","stamp")

    def test_actual_schedule_announcement_and_public_date(self):
        actual={"titulo":"BC divulga o IBC-Br de agosto de 2026","Id":1,"DataPublicacao":"2026-10-16T12:00:49Z","Url":"/detalhenoticia/1/nota"}
        events=bcb.normalize_release(actual,"retrieved","a"*64)
        self.assertEqual(len(events),5)
        self.assertEqual(events[0]["reference_month"],"2026-08")
        self.assertEqual(events[0]["event_status"],"actual")
        # Retained BCB note 17159 starts with a space and reports June in August.
        june=dict(actual,titulo=" BC divulga o IBC-Br de junho de 2020",Id=17159,DataPublicacao="2020-08-14T12:02:09Z",Url="/detalhenoticia/17159/nota")
        june_events=bcb.normalize_release(june,"retrieved","a"*64)
        self.assertEqual({e["series_code"] for e in june_events},{24363,24364})
        self.assertEqual(june_events[0]["reference_month"],"2020-06")
        self.assertEqual(june_events[0]["event_status"],"actual")
        self.assertEqual(june_events[0]["actual_release_timestamp"],"2020-08-14T12:02:09Z")
        scheduled=bcb.normalize_release({"Id":1,"descricao":"Referência agosto de 2026","dataEvento":"2026-10-16T12:00:00Z"},"retrieved","a"*64,True)
        self.assertEqual(scheduled[0]["actual_release_timestamp"],"")
        announced=dict(actual,titulo=" BC divulga IBC-Br de agosto de 2026 na sexta-feira (16/10) ")
        self.assertEqual(bcb.normalize_release(announced,"retrieved","a"*64)[0]["event_status"],"announcement")
        old=dict(actual,DataPublicacao="2025-03-15T12:00:00Z",titulo="BC divulga IBC-Br de janeiro de 2025")
        self.assertEqual({e["series_code"] for e in bcb.normalize_release(old,"r","a"*64)},{24363,24364})
        no_year=dict(actual,titulo="BC divulga IBC-Br de agosto")
        self.assertEqual(bcb.normalize_release(no_year,"r","a"*64)[0]["reference_month"],"")
        explicit={"corpo":"<p>IBC-Br referente a agosto de 2026.</p>"}
        self.assertEqual(bcb.normalize_release(no_year,"r","a"*64,detail=explicit)[0]["reference_month"],"2026-08")
        conflicting={"corpo":"Agosto de 2025 e agosto de 2026."}
        self.assertEqual(bcb.normalize_release(no_year,"r","a"*64,detail=conflicting)[0]["reference_month"],"")
        with self.assertRaises(ValueError): bcb.normalize_release(dict(actual,DataPublicacao="2026-10-16T12:00:00"),"r","a"*64)

    def test_pagination_year_checks_and_atomic_failure(self):
        with tempfile.TemporaryDirectory(prefix="terra56_bcb_download_") as temporary:
            dest=Path(temporary)/"new_snapshot"
            old=Path(temporary)/"old.csv"
            old.write_bytes(b"protected")
            calls=[]
            # requests' response interface is the required callback contract here.
            class Response:
                def __init__(self,payload):
                    self.payload=payload
                    self.content=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
                def raise_for_status(self): pass
                def json(self): return self.payload
            class Session:
                def get(self,url,timeout):
                    calls.append(url)
                    parsed=urlparse(url)
                    params=parse_qs(parsed.query)
                    if "package_search" in url:return Response({"success":True,"result":{"count":1,"results":[{"codigo_sgs":"24364","title":"IBC-Br com ajuste sazonal","periodicidade":"mensal","unidade_medida":"Indice"}]}})
                    if "consultarValoresSeries.do" in url:return Response(b"<table><tr><td>7478</td><td>IPCA-15</td><td>Monthly % var.</td><td>M</td></tr></table>")
                    if "bcdata.sgs" in url:return Response([{"data":"01/01/2016","valor":"100"}])
                    if "$metadata" in url:return Response(b"<metadata/>")
                    if "odata/Expectativas" in url:
                        entity=parsed.path.rsplit("/",1)[1]
                        skip=int(params["$skip"][0])
                        # First full page, then a terminal page proves complete pagination.
                        days=["2016-01-01","2016-01-02"] if skip==0 else ["2016-01-03"]
                        rows=[]
                        for day in days:
                            row=dict(Indicador="IPCA",Data=day,Suavizada="N",Media=1,Mediana=1,DesvioPadrao=0,Minimo=1,Maximo=1,baseCalculo=0)
                            if "Top5" in entity:row["tipoCalculo"]="CURTO_PRAZO"
                            if entity.endswith("Selic"):row.update(Indicador="Selic",Reuniao="R1/2016")
                            rows.append(row)
                        return Response({"value":rows})
                    if "paginasite" in url:return Response({"conteudo":"verified fixture"})
                    if "notasporano" in url:return Response({"conteudo":[dict(titulo="BC divulga o IBC-Br de novembro de 2015",Id=1.0,DataPublicacao="2016-01-15T12:00:00Z",Url="/detalhenoticia/1/nota")]})
                    if "notasImprensa" in url:
                        assert parsed.path.endswith("/1"), "BCB detail URLs require integer note IDs"
                        return Response({"conteudo":[{"dataPublicacao":"2016-01-15T12:00:00Z"}]})
                    if "calendario" in url:return Response({"conteudo":[]})
                    if url.endswith(".xlsx"):return Response(b"fixture, not an empirical vintage")
                    raise AssertionError(url)
            with patch.dict(bcb.SGS,{24364:bcb.SGS[24364]},clear=True),patch.object(bcb,"ODATA_PAGE_SIZE",2):
                bcb.acquire_support(Session(),dest,date(2016,12,31))
            saved=json.loads((dest/"source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"],"acquired_current_values_vintages_unverified")
            # The official SGS table verifies a definition absent from the catalogue.
            missing_metadata=Path(temporary)/"missing_metadata"
            with patch.dict(bcb.SGS,{7478:bcb.SGS[7478]},clear=True),patch.object(bcb,"ODATA_PAGE_SIZE",2):
                bcb.acquire_support(Session(),missing_metadata,date(2016,12,31))
            incomplete=json.loads((missing_metadata/"source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(incomplete["status"],"acquired_current_values_vintages_unverified")
            self.assertEqual(incomplete["metadata_unverified_series"],[])
            self.assertTrue(any(r.get("metadata_status")=="verified" and r.get("series_code")==7478 for r in incomplete["requests"]))
            with (dest/"focus.csv").open(encoding="utf-8") as focus_file:
                self.assertEqual(len(list(csv.DictReader(focus_file))),9)
            self.assertTrue(any("%20" in u and "$skip" not in u and "%24skip=2" in u for u in calls))
            self.assertEqual(old.read_bytes(),b"protected")
            with self.assertRaises(FileExistsError):bcb.acquire_support(Session(),dest,date(2016,12,31))
            class BadSession:
                def get(self,url,timeout):return Response({"not":"a SGS response"})
            replay=Path(temporary)/"replayed_snapshot"
            with patch.dict(bcb.SGS,{24364:bcb.SGS[24364]},clear=True),patch.object(bcb,"ODATA_PAGE_SIZE",2):
                bcb.acquire_support(BadSession(),replay,date(2026,11,9),replay_from=dest)
            self.assertEqual((replay/"sgs.csv").read_bytes(),(dest/"sgs.csv").read_bytes())
            self.assertEqual((replay/"focus.csv").read_bytes(),(dest/"focus.csv").read_bytes())
            failed=Path(temporary)/"failed_snapshot"
            with self.assertRaises(ValueError):bcb.acquire_support(BadSession(),failed,date(2016,12,31))
            self.assertFalse((failed/"sgs.csv").exists())
            self.assertTrue((failed/"FAILED.json").exists())
            self.assertEqual(old.read_bytes(),b"protected")


@unittest.skipUnless(RSCRIPT.is_file(),"Set RSCRIPT_EXECUTABLE or place Rscript on PATH; R checks were not run")
class BcbOfflineFixture(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="terra56_bcb_join_")
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.snapshot=self.root/"snapshot"
        self.snapshot.mkdir()
        self.run=self.root/"run"
        self.run.mkdir()
        self.spec=json.loads((SOURCE_ROOT/"code/replication_spec.json").read_text())
        self.spec.update(design_type="qa_fixture",run_root=str(self.run),release_id="terra56_qa_bcb",fingerprint="a"*64)
        self.spec["sample"].update(first_meeting=281,last_meeting=281,meeting_numbers=[281])
        self.spec["raw_snapshot"]["path"]=str(self.snapshot)
        self.spec["activity_controls"]["cutoffs_file"]=str(self.run/"cutoffs.csv")
        self.spec["source_hashes"]={"code/01_data_preparation/build_bcb_inputs.R":hashlib.sha256((SOURCE_ROOT/"code/01_data_preparation/build_bcb_inputs.R").read_bytes()).hexdigest()}
        dates=Path(self.spec["sample"]["dates_csv"])
        self.spec["input_hashes"]={str(dates):hashlib.sha256(dates.read_bytes()).hexdigest()}
        self.specfile=self.root/"qa_spec.json"
        self.cutoff=[dict(meeting_number=281,meeting_cutoff="2026-09-16T12:00:00Z",timezone="UTC",cutoff_precision="second",evidence_url="https://example.test/fixture",convention="Explicit synthetic test cutoff")]
        self.events=[]
        self.vintages=[]
        self.sourcefile=self.snapshot/"fixture_release.txt"
        self.sourcefile.write_text("LABELLED SYNTHETIC RELEASE VINTAGE")
        self.sourcehash=hashlib.sha256(self.sourcefile.read_bytes()).hexdigest()
        for code in (29602,29604,29606,24364):
            self.events.append(dict(series_code=code,reference_month="2026-07",release_id="july",actual_release_date="2026-09-15",actual_release_timestamp="2026-09-15T12:00:00Z",timezone="UTC",timestamp_precision="second",scheduled_release_date="",event_status="actual",evidence_url="https://example.test/fixture",retrieved_at="2026-11-01T00:00:00Z",source_hash=self.sourcehash))
            self.vintages.append(dict(series_code=code,reference_month="2026-07",release_id="july",vintage_timestamp="2026-09-15T12:00:00Z",value_at_release=100,vintage_status="VERIFIED_RELEASE_VINTAGE",source_file="fixture_release.txt",source_hash=self.sourcehash))
        self.vintages.append(dict(self.vintages[-1],reference_month="2026-06",value_at_release=80))

    def run_builder(self):
        # csv.DictWriter and subprocess.run supply ordinary fixture persistence/execution.
        for path,rows,fields in [(Path(self.spec["activity_controls"]["cutoffs_file"]),self.cutoff,list(self.cutoff[0])),(self.snapshot/"ibc_release_calendar.csv",self.events,bcb.RELEASE_COLUMNS),(self.snapshot/"ibc_vintage_observations.csv",self.vintages,list(self.vintages[0]))]:
            with path.open("w",newline="") as output:
                writer=csv.DictWriter(output,fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        self.specfile.write_text(json.dumps(self.spec),encoding="utf-8")
        (self.run/"effective_spec.json").write_bytes(self.specfile.read_bytes())
        environment=dict(os.environ,REPLICATION_SPEC_PATH=str(self.specfile))
        completed=subprocess.run([str(RSCRIPT),"--vanilla",str(SOURCE_ROOT/"code/01_data_preparation/build_bcb_inputs.R")],env=environment,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        with (self.run/"data/analysis/meeting_ibc_availability.csv").open() as source:return list(csv.DictReader(source))

    def test_latest_vintage_and_aggregate_units(self):
        # Current/revised value999 is deliberately absent from the vintage authority.
        (self.snapshot/"ibc_series_enriched.csv").write_text("series_code,reference_month,current_value\n29602,2026-07,999\n")
        audit=self.run_builder()
        self.assertEqual({r["availability_status"] for r in audit},{"READY"})
        self.assertEqual({r["selected_value"] for r in audit},{"100"})
        with (self.run/"data/analysis/meeting_ibc_aggregate_controls.csv").open() as source:aggregate=list(csv.DictReader(source))
        import math
        self.assertAlmostEqual(float(aggregate[0]["ibc_change_pre"]),100*math.log(100/80),10)

    def test_future_and_scheduled_release_are_ineligible(self):
        for status in ("actual","scheduled"):
            with self.subTest(status=status):
                self.events=[dict(r,reference_month="2026-08",release_id="august",actual_release_date="2026-10-16" if status=="actual" else "",actual_release_timestamp="2026-10-16T12:00:00Z" if status=="actual" else "",event_status=status,scheduled_release_date="2026-10-16") for r in self.events]
                audit=self.run_builder()
                self.assertEqual({r["availability_status"] for r in audit},{"BLOCKED_RELEASE_EVIDENCE"})
                self.assertFalse((self.run/"data/analysis/copom_meeting_activity_controls.csv").exists())

    def test_same_day_equality_and_ambiguity(self):
        self.events=[dict(r,actual_release_date="2026-09-16",actual_release_timestamp="2026-09-16T12:00:00Z") for r in self.events]
        self.vintages=[dict(r,vintage_timestamp="2026-09-16T12:00:00Z") for r in self.vintages]
        audit=self.run_builder()
        self.assertEqual({r["availability_status"] for r in audit},{"READY"})
        # Use a new owned run because a changed cutoff is a changed specification.
        self.run=self.root/"date_only_run"
        self.run.mkdir()
        self.spec["run_root"]=str(self.run)
        self.spec["activity_controls"]["cutoffs_file"]=str(self.run/"cutoffs.csv")
        self.cutoff[0].update(meeting_cutoff="2026-09-16",cutoff_precision="date")
        audit=self.run_builder()
        self.assertEqual({r["availability_status"] for r in audit},{"BLOCKED_SAME_DAY_CUTOFF"})

    def test_missing_vintage_never_uses_current(self):
        for row in self.vintages:row["vintage_status"]="BLOCKED_VINTAGE"
        audit=self.run_builder()
        self.assertEqual({r["availability_status"] for r in audit},{"BLOCKED_VINTAGE"})
        self.assertTrue(all(not r["selected_value"] for r in audit))

    def test_full_membership_public_availability(self):
        self.spec["sample"].update(first_meeting=203,last_meeting=281,meeting_numbers=list(range(203,282)))
        self.cutoff=[]
        # An explicit test convention is deliberately present only at281.
        self.cutoff=[dict(meeting_number=281,meeting_cutoff="2026-09-16T12:00:00Z",timezone="UTC",cutoff_precision="second",evidence_url="https://example.test/fixture",convention="Synthetic test")]
        audit=self.run_builder()
        self.assertEqual(len(audit),237)
        self.assertEqual({int(r["meeting_number"]) for r in audit},set(range(203,282)))
        self.assertEqual({r["availability_status"] for r in audit if int(r["meeting_number"])==203},{"BLOCKED_PUBLIC_AVAILABILITY"})
        self.assertFalse((self.run/"data/analysis/copom_meeting_activity_controls.csv").exists())

    def test_duplicate_release_fails_closed(self):
        self.events.append(dict(self.events[0]))
        with self.assertRaises(AssertionError):self.run_builder()
        self.assertFalse((self.run/"data/analysis/copom_meeting_activity_controls.csv").exists())

    def test_offline_import_sentinels_resume_and_duplicate_target(self):
        sgs=[dict(series_code=code,series_name=meta[0],date="2026-09-16",value=100,source_url="https://example.test/fixture",fetched_at="2026-11-01T00:00:00Z") for code,meta in bcb.SGS.items()]
        focus=[]
        for entity in ("ExpectativasMercadoInflacao12Meses","ExpectativasMercadoTop5Inflacao12Meses","ExpectativasMercadoSelic"):
            row=dict.fromkeys(bcb.FOCUS_COLUMNS,"")
            row.update(source_entity=entity,indicator="Selic" if entity.endswith("Selic") else "IPCA",date="2026-09-16",smoothed="N" if not entity.endswith("Selic") else "",mean=3,median=3,sd=0,min=3,max=3,respondents=5,calculation_base=0 if "Top5" not in entity else "",source_url="https://example.test/fixture",fetched_at="2026-11-01T00:00:00Z")
            if "Top5" in entity:row["calculation_type"]="CURTO_PRAZO"
            if entity.endswith("Selic"):row["meeting"]="R7/2026"
            focus.append(row)
        for filename,rows in (("sgs.csv",sgs),("focus.csv",focus)):
            with (self.snapshot/filename).open("w",encoding="utf-8",newline="") as output:
                writer=csv.DictWriter(output,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        manifest=dict(status="acquired_current_values_vintages_unverified",retrieved_at="2026-11-01T00:00:00Z",normalized_sha256={name:hashlib.sha256((self.snapshot/name).read_bytes()).hexdigest() for name in ("sgs.csv","focus.csv")})
        (self.snapshot/"source_manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
        self.spec["raw_snapshot"]["manifest_sha256"]=hashlib.sha256((self.snapshot/"source_manifest.json").read_bytes()).hexdigest()
        self.specfile.write_text(json.dumps(self.spec),encoding="utf-8")
        (self.run/"effective_spec.json").write_bytes(self.specfile.read_bytes())
        database=self.run/self.spec["paths"]["database"]
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            connection.execute("INSERT INTO metadata VALUES(?,?)",("replication_specification",json.dumps(dict(spec_sha256=hashlib.sha256(self.specfile.read_bytes()).hexdigest(),fingerprint=self.spec["fingerprint"],release_id=self.spec["release_id"],design_type=self.spec["design_type"]))))
            connection.execute("CREATE TABLE external_bcb_sgs(series_code INTEGER NOT NULL,series_name TEXT NOT NULL,date TEXT NOT NULL,value REAL,source_url TEXT NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(series_code,date))")
            connection.execute("INSERT INTO external_bcb_sgs VALUES(999999,'old sentinel','2000-01-01',7,'https://example.test/sentinel','old')")
            connection.commit()
        environment=dict(os.environ,REPLICATION_SPEC_PATH=str(self.specfile),http_proxy="http://127.0.0.1:1",https_proxy="http://127.0.0.1:1")
        command=[str(RSCRIPT),"--vanilla",str(SOURCE_ROOT/"code/collection/scripts/collect_referee_completion_bcb.R")]
        for repeat in range(2):
            completed=subprocess.run(command,env=environment,capture_output=True,text=True)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM external_bcb_sgs WHERE series_code=999999").fetchone()[0],7)
            self.assertEqual(connection.execute("SELECT date FROM external_bcb_sgs WHERE series_code=29602").fetchone()[0],"2026-09-16")
            self.assertEqual(connection.execute("SELECT count(*) FROM external_bcb_snapshot").fetchone()[0],1)
        # A duplicate same-date target must fail before any existing database rows change.
        with (self.snapshot/"focus.csv").open("a",encoding="utf-8",newline="") as output:
            csv.DictWriter(output,fieldnames=bcb.FOCUS_COLUMNS).writerow(focus[0])
        manifest["normalized_sha256"]["focus.csv"]=hashlib.sha256((self.snapshot/"focus.csv").read_bytes()).hexdigest()
        (self.snapshot/"source_manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
        self.spec["raw_snapshot"]["manifest_sha256"]=hashlib.sha256((self.snapshot/"source_manifest.json").read_bytes()).hexdigest()
        self.specfile.write_text(json.dumps(self.spec),encoding="utf-8")
        (self.run/"effective_spec.json").write_bytes(self.specfile.read_bytes())
        with closing(sqlite3.connect(database)) as connection:before=connection.execute("SELECT * FROM external_bcb_sgs ORDER BY series_code,date").fetchall()
        completed=subprocess.run(command,env=environment,capture_output=True,text=True)
        self.assertNotEqual(completed.returncode,0)
        self.assertIn("Duplicate Focus",completed.stderr)
        with closing(sqlite3.connect(database)) as connection:after=connection.execute("SELECT * FROM external_bcb_sgs ORDER BY series_code,date").fetchall()
        self.assertEqual(before,after)


if __name__=="__main__":
    unittest.main(verbosity=2)
