"""Acquire Copom texts and immutable official BCB supporting snapshots.

Existing texts are never overwritten. Exact-analysis scripts import snapshots offline.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, quote
import fitz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

API_URL = "https://www.bcb.gov.br/api/servico/sitebcb/copom"
FOCUS_DATASET_URL = "https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado"
OLINDA = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata"
SECTORS = (29602, 29604, 29606)
IBC_CODES = (24363, 24364, *SECTORS)
MONTHS = {name: i + 1 for i, name in enumerate(("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"))}
# Official SGS concepts. Metadata payloads are retained and checked before freeze.
SGS = {
 7478: ("IPCA-15", "Monthly % change", "monthly", False),
 24364: ("IBC-Br seasonally adjusted", "Index", "monthly", True),
 10813: ("BRL per USD buying rate", "BRL/USD", "daily", False),
 432: ("Selic target set by Copom", "% per year", "daily", False),
 13522: ("IPCA accumulated over 12 months", "% over 12 months", "monthly", False),
 24363: ("IBC-Br", "Index", "monthly", False),
 433: ("IPCA monthly change", "Monthly % change", "monthly", False),
 29602: ("IBC-Br Agropecuária seasonally adjusted", "Index", "monthly", True),
 29604: ("IBC-Br Indústria seasonally adjusted", "Index", "monthly", True),
 29606: ("IBC-Br Serviços seasonally adjusted", "Index", "monthly", True)
}
ODATA_PAGE_SIZE = 10000
FOCUS_COLUMNS = ["source_entity", "indicator", "date", "reference_period", "meeting", "calculation_type", "smoothed", "mean", "median", "sd", "min", "max", "respondents", "calculation_base", "source_url", "fetched_at"]
RELEASE_COLUMNS = ["series_code", "reference_month", "release_id", "actual_release_date", "actual_release_timestamp", "timezone", "timestamp_precision", "scheduled_release_date", "event_status", "evidence_url", "retrieved_at", "source_hash"]

# Neither requests nor pandas implements these BCB payload contracts. These narrow
# normalizers enforce unique dated keys and preserve publication evidence explicitly.
def normalize_sgs(payload, code, source_url, fetched_at):
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"Empty/malformed SGS payload for {code}")
    rows = {}
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"data", "valor"}:
            raise ValueError(f"Unexpected SGS schema for {code}")
        day = datetime.strptime(item["data"], "%d/%m/%Y").date().isoformat()
        value = float(item["valor"])
        if not math.isfinite(value):
            raise ValueError("Nonfinite SGS observation")
        if day in rows and rows[day]["value"] != value:
            raise ValueError("Conflicting duplicate SGS observation")
        rows[day] = dict(series_code=code, series_name=SGS[code][0], date=day, value=value, source_url=source_url, fetched_at=fetched_at)
    return list(rows.values())


def normalize_release(record, retrieved_at, source_hash, scheduled=False):
    title = BeautifulSoup(record.get("descricao", ""), "html.parser").get_text(" ") if scheduled else record.get("titulo", "")
    title = re.sub(r"[\u200b-\u200d\ufeff]", "", title).lower()
    match = re.search(r"(?:referência\s*|ibc-br\s+de\s+)(" + "|".join(MONTHS) + r")(?:\s+de)?\s*(20\d{2})?", title)
    if not match:
        return []
    timestamp = record.get("dataEvento") if scheduled else record.get("DataPublicacao", record.get("dataPublicacao"))
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Release timestamp lacks an offset")
    refmonth = f"{int(match.group(2)):04d}-{MONTHS[match.group(1)]:02d}" if match.group(2) else ""
    # Missing reference-year evidence remains unmatched. Publication year alone
    # does not establish an observation year.
    # A forward-looking announcement is retained but cannot become an actual event.
    actual = not scheduled and bool(re.match(r"^bc divulga (?:o )?ibc-br de ", title)) and not re.search(r"(?:na |no dia|amanhã|será|quinta-feira|sexta-feira|segunda-feira|terça-feira|quarta-feira)", title)
    status = "scheduled" if scheduled else ("actual" if actual else "announcement")
    codes = IBC_CODES if parsed.date() >= date(2025, 4, 11) else IBC_CODES[:2]
    return [dict(series_code=code, reference_month=refmonth, release_id=("calendar_" if scheduled else "note_") + str(record["Id"]), actual_release_date=parsed.date().isoformat() if actual else "", actual_release_timestamp=timestamp if actual else "", timezone="UTC" if actual else "", timestamp_precision="second" if actual else "", scheduled_release_date=parsed.date().isoformat() if scheduled else "", event_status=status, evidence_url=("https://www.bcb.gov.br/estatisticas/ibc-br" if scheduled else "https://www.bcb.gov.br" + record["Url"]), retrieved_at=retrieved_at, source_hash=source_hash) for code in codes]


def acquire_support(session, destination, end_date):
    """BCB-specific acquisition cannot be supplied by a generic download library."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    raw = destination / "responses"
    raw.mkdir()
    retrieved = datetime.now(timezone.utc).isoformat()
    manifest, sgs, focus, events = [], [], [], []
    unverified_series = []
    # Request persistence is coupled to provenance. No existing response is replaced.
    def request_payload(url, params, filename, kind="json"):
        request_url = url + ("?" + urlencode(params, quote_via=quote) if params else "")
        print(f"Downloading {filename}", flush=True)
        response = session.get(request_url, timeout=90)
        response.raise_for_status()
        payload = response.json() if kind == "json" else response.content
        content = response.content
        (raw / filename).write_bytes(content)
        manifest.append(dict(file="responses/" + filename, url=url, parameters=params, request_url=request_url, retrieved_at=retrieved, sha256=hashlib.sha256(content).hexdigest()))
        return payload
    try:
        # Annual chunks also keep all daily requests below BCB's ten-year limit.
        for code, (name, unit, frequency, seasonal) in SGS.items():
            # The older SGS metadata server returned repeated HTTP 502 errors.
            # Use BCB's structured catalogue. Missing entries permit collection,
            # but the snapshot status must still block the offline importer.
            mdurl = "https://dadosabertos.bcb.gov.br/api/3/action/package_search"
            md = request_payload(mdurl, {"q":str(code), "rows":100}, f"sgs_{code}_metadata.json")
            if md.get("success") is not True or not isinstance(md.get("result", {}).get("results"), list) or md["result"].get("count", 0) > 100:
                raise ValueError(f"Malformed/incomplete official catalogue response for SGS {code}")
            matches = [item for item in md["result"]["results"] if str(item.get("codigo_sgs")) == str(code)]
            if len(matches) > 1:
                raise ValueError(f"Ambiguous official catalogue metadata for SGS {code}")
            if matches:
                frequency_text = unicodedata.normalize("NFKD", matches[0].get("periodicidade", "")).encode("ascii", "ignore").decode().lower()
                unit_text = unicodedata.normalize("NFKD", matches[0].get("unidade_medida", "")).encode("ascii", "ignore").decode().lower()
                frequency_verified = any(token in frequency_text for token in (("mensal", "monthly") if frequency == "monthly" else ("diaria", "daily")))
                unit_verified = any(token in unit_text for token in (("indice", "index") if unit == "Index" else (("dolar", "usd", "us$") if unit == "BRL/USD" else ("percent", "%"))))
                if not frequency_verified or not unit_verified:
                    raise ValueError(f"Conflicting official frequency/unit metadata for SGS {code}")
                manifest[-1].update(series_code=code, metadata_status="verified", catalogue_title=matches[0]["title"], verified_frequency=frequency, verified_unit=unit, seasonal_adjustment=seasonal)
            else:
                unverified_series.append(code)
                manifest[-1].update(series_code=code, metadata_status="missing_catalogue_entry", declared_frequency=frequency, declared_unit=unit)
                print(f"SGS {code}: catalogue description unavailable; analysis import remains blocked", flush=True)
            for year in range(2016, end_date.year + 1):
                start, end = date(year, 1, 1), min(date(year, 12, 31), end_date)
                url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
                params = dict(formato="json", dataInicial=start.strftime("%d/%m/%Y"), dataFinal=end.strftime("%d/%m/%Y"))
                payload = request_payload(url, params, f"sgs_{code}_{year}.json")
                rows = normalize_sgs(payload, code, manifest[-1]["request_url"], retrieved)
                if any(not start.isoformat() <= r["date"] <= end.isoformat() for r in rows):
                    raise ValueError("SGS chunk returned observations outside requested dates")
                sgs.extend(rows)
                manifest[-1].update(unit=unit, frequency=frequency, seasonal_adjustment=seasonal, observation_first=min(r["date"] for r in rows), observation_last=max(r["date"] for r in rows))
        keys = [(r["series_code"], r["date"]) for r in sgs]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate SGS chunk-boundary key")
        request_payload(OLINDA + "/$metadata", {}, "focus_metadata.xml", "bytes")
        manifest[-1]["dataset_url"] = FOCUS_DATASET_URL
        # These are the catalogue's Focus series used by the analysis. Data is the
        # statistics date, not evidence that the values were published that day.
        queries = {
          "ExpectativasMercadoInflacao12Meses": "Indicador eq 'IPCA' and Suavizada eq 'N' and baseCalculo eq 0",
          "ExpectativasMercadoTop5Inflacao12Meses": "Indicador eq 'IPCA' and Suavizada eq 'N'",
          "ExpectativasMercadoSelic": "Indicador eq 'Selic' and baseCalculo eq 0"
        }
        for entity, selector in queries.items():
            seen = set()
            for skip in range(0, 1000001, ODATA_PAGE_SIZE):
                params = {"$filter": selector + f" and Data ge '2016-01-01' and Data le '{end_date.isoformat()}'", "$orderby":"Data asc", "$top":ODATA_PAGE_SIZE, "$skip":skip, "$format":"json"}
                page = request_payload(OLINDA + "/" + entity, params, f"focus_{entity}_{skip}.json")
                manifest[-1].update(dataset_url=FOCUS_DATASET_URL,
                                    date_field="Data", date_meaning="statistics_date_not_verified_publication_time")
                if not isinstance(page, dict) or not isinstance(page.get("value"), list):
                    raise ValueError("Malformed Focus page")
                for row in page["value"]:
                    required = {"Indicador", "Data", "Mediana", "Media", "DesvioPadrao", "Minimo", "Maximo"}
                    required |= {"tipoCalculo", "Suavizada"} if "Top5" in entity else {"baseCalculo"}
                    required |= {"Reuniao"} if entity.endswith("Selic") else {"Suavizada"}
                    if not required.issubset(row) or row["Indicador"] != ("Selic" if entity.endswith("Selic") else "IPCA"):
                        raise ValueError("Unexpected Focus schema/indicator")
                    if row.get("Suavizada", "N") != "N" or row.get("baseCalculo", 0) != 0:
                        raise ValueError("Unexpected Focus smoothing/calculation selector")
                    if not "2016-01-01" <= row["Data"] <= end_date.isoformat():
                        raise ValueError("Focus date outside retrieval range")
                    key = (row["Data"], row.get("Reuniao", ""), row.get("tipoCalculo", ""), row.get("baseCalculo", ""), row.get("Suavizada", ""))
                    if key in seen:
                        raise ValueError("Duplicate Focus key, including pagination boundary")
                    seen.add(key)
                    if any(not math.isfinite(float(row[k])) for k in ("Media", "Mediana", "DesvioPadrao", "Minimo", "Maximo")):
                        raise ValueError("Nonfinite Focus statistic")
                    focus.append(dict(zip(FOCUS_COLUMNS, [entity,row["Indicador"],row["Data"],row.get("DataReferencia", ""),row.get("Reuniao", ""),row.get("tipoCalculo", ""),row.get("Suavizada", ""),row["Media"],row["Mediana"],row["DesvioPadrao"],row["Minimo"],row["Maximo"],row.get("numeroRespondentes", ""),row.get("baseCalculo", ""),manifest[-1]["request_url"],retrieved])))
                if len(page["value"]) < ODATA_PAGE_SIZE:
                    break
            else:
                raise ValueError("Focus pagination limit reached")
            if not seen:
                raise ValueError(f"Empty Focus extract for {entity}")
        # These services are called by BCB's published annual-notes and calendar components.
        request_payload("https://www.bcb.gov.br/api/paginasite/sitebcb/home/notasporano", {}, "history_page.json")
        request_payload("https://www.bcb.gov.br/api/paginasite/sitebcb/estatisticas/ibc-br", {}, "ibc_page.json")
        for year in range(2016, end_date.year + 1):
            notes = request_payload("https://www.bcb.gov.br/api/servico/sitebcb/notasporano", {"ano":year}, f"notes_{year}.json")
            if not isinstance(notes, dict) or not isinstance(notes.get("conteudo"), list) or not notes["conteudo"]:
                raise ValueError(f"Missing annual release history {year}")
            for item in notes["conteudo"]:
                if not str(item.get("DataPublicacao", "")).startswith(str(year)):
                    raise ValueError("Annual note index ignored requested year")
                if "ibc-br" not in item.get("titulo", "").lower():
                    continue
                # The annual index can encode integral note IDs as JSON floats.
                # Detail URLs require the integer form, without a trailing .0.
                note_id = int(item["Id"])
                if note_id <= 0 or float(item["Id"]) != note_id:
                    raise ValueError("Invalid release-note identifier")
                item = dict(item, Id=note_id)
                detail = request_payload("https://www.bcb.gov.br/api/servico/sitebcb/notasImprensa/" + str(item["Id"]), {"listsite":"conteudo/home-ptbr", "listname":"Notas à Imprensa"}, f"note_{item['Id']}.json")
                if len(detail.get("conteudo", [])) != 1 or detail["conteudo"][0].get("dataPublicacao") != item["DataPublicacao"]:
                    raise ValueError("Conflicting index/detail disclosure evidence")
                events.extend(normalize_release(item, retrieved, manifest[-1]["sha256"]))
            calendar = request_payload("https://www.bcb.gov.br/api/servico/sitebcb/calendario/anual", {"lista":"Índice de atividade econômica (IBC-Br)","inicioAgenda":f"'{year}-01-01'","fimAgenda":f"'{year}-12-31'"}, f"calendar_{year}.json")
            for item in calendar.get("conteudo", []):
                if not item.get("dataEvento", "").startswith(str(year)):
                    raise ValueError("Annual calendar ignored requested year")
                events.extend(normalize_release(item, retrieved, manifest[-1]["sha256"], scheduled=True))
        # Preserve the official matrix for later schema/vintage verification. Downloading
        # its present bytes alone never certifies an original release vintage.
        request_payload("https://www.bcb.gov.br/content/indeco/ibcbr/IBC-Br_todas_publicacoes.xlsx", {}, "IBC-Br_todas_publicacoes.xlsx", "bytes")
        enriched = []
        for row in sgs:
            if row["series_code"] not in IBC_CODES:
                continue
            matches = [e for e in events if e["series_code"] == row["series_code"] and e["reference_month"] == row["date"][:7] and e["event_status"] == "actual"]
            enriched.append(dict(series_code=row["series_code"],reference_month=row["date"][:7],current_value=row["value"],first_public_release_at=min((e["actual_release_timestamp"] for e in matches),default=""),series_first_publication_at="2025-04-11" if row["series_code"] in SECTORS else "",release_evidence=" | ".join(e["evidence_url"] for e in matches),vintage_status="BLOCKED_VINTAGE",retrieved_at=retrieved))
        for name, rows, columns in [("sgs.csv", sgs, ["series_code","series_name","date","value","source_url","fetched_at"]),("focus.csv",focus,FOCUS_COLUMNS),("ibc_release_calendar.csv",events,RELEASE_COLUMNS),("ibc_series_enriched.csv",enriched,["series_code","reference_month","current_value","first_public_release_at","series_first_publication_at","release_evidence","vintage_status","retrieved_at"]),("ibc_vintage_observations.csv",[],["series_code","reference_month","release_id","vintage_timestamp","value_at_release","vintage_status","source_file","source_hash"])]:
            with (destination / name).open("x", encoding="utf-8", newline="") as output:
                writer=csv.DictWriter(output,fieldnames=columns,lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
        identities = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob("*.csv")}
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as temporary_manifest:
            manifest_temp = Path(temporary_manifest.name)
            status = "acquired_current_values_metadata_incomplete" if unverified_series else "acquired_current_values_vintages_unverified"
            temporary_manifest.write(json.dumps(dict(status=status,metadata_unverified_series=unverified_series,retrieved_at=retrieved,requests=manifest,normalized_sha256=identities),ensure_ascii=False,indent=2).encode("utf-8"))
        manifest_temp.replace(destination / "source_manifest.json")
    except Exception:
        # Retain raw evidence but do not leave partial normalized inputs pretending ready.
        for name in ("sgs.csv","focus.csv","ibc_release_calendar.csv","ibc_series_enriched.csv","ibc_vintage_observations.csv"):
            (destination / name).unlink(missing_ok=True)
        (destination / "FAILED.json").write_text(json.dumps({"status":"FAILED","requests":manifest},indent=2),encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=os.environ.get("REPLICATION_SPEC_PATH"))
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--data-only", action="store_true")
    modes.add_argument("--minutes-only", action="store_true")
    modes.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not args.spec:
        parser.error("--spec or REPLICATION_SPEC_PATH is required")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    source_root = Path(spec["source_root"]).resolve(strict=True)
    if source_root != Path(__file__).resolve().parents[2]:
        raise ValueError("Specification SOURCE_ROOT does not identify this source package")
    sys.path.insert(0,str(source_root / "code" / "collection" / "src"))
    from prompt_iv.paths import load_spec
    spec = load_spec(args.spec,source_root=source_root,require_frozen=True)
    FIRST_ANALYSIS_MEETING = int(spec["sample"]["first_meeting"])
    LAST_ANALYSIS_MEETING = int(spec["sample"]["last_meeting"])
    if (FIRST_ANALYSIS_MEETING, LAST_ANALYSIS_MEETING) != (203,281):
        raise ValueError("Acquisition sample must remain exactly 203:281")
    MINUTES_DIR = source_root / "data" / "raw" / "copom_minutes"
    run_root = Path(spec["run_root"]).resolve(strict=True)
    metadata_parent = Path(spec["paths"]["metadata"])
    if not metadata_parent.is_absolute():
        metadata_parent = run_root / metadata_parent
    metadata_parent = metadata_parent.resolve()
    if run_root == source_root or not metadata_parent.is_relative_to(run_root) or "archives" in run_root.parts:
        raise ValueError("Publication metadata destination escapes the selected run")
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(total=4,backoff_factor=1,status_forcelist=[429,500,502,503,504],allowed_methods=["GET"])))
    if args.data_only or args.all:
        # Keep official raw inputs together. Each download gets a new directory
        # so earlier input versions remain available and cannot be overwritten.
        snapshot = Path(spec["raw_snapshot"]["path"])
        raw_parent = source_root / "data" / "raw" / "bcb"
        if raw_parent.resolve() != raw_parent:
            raise ValueError("BCB raw-data directory must not redirect through a filesystem link")
        if not snapshot.is_absolute() or snapshot.parent != raw_parent or snapshot.exists() or snapshot.is_symlink() or not spec["raw_snapshot"].get("download_stamp") or snapshot.name != spec["raw_snapshot"]["download_stamp"]:
            raise ValueError("A new named immutable BCB snapshot under SOURCE_ROOT/data/raw/bcb is required")
        acquire_support(session, snapshot, date.today())
    if args.minutes_only or args.all:
        existing_minute_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in MINUTES_DIR.glob("ata*.txt")}
        # Increase the requested count if the listing fills the current limit.
        quantity = 1000
        while True:
            response = session.get(f"{API_URL}/atas", params={"quantidade": quantity}, timeout=30)
            response.raise_for_status()
            listing = response.json()["conteudo"]
            if not listing:
                raise ValueError("The Banco Central API returned an empty minutes listing")
            if len(listing) < quantity:
                break
            quantity *= 2

        meeting_numbers = [record["nroReuniao"] for record in listing]
        if (any(not isinstance(number, int) or number < 1 for number in meeting_numbers)
                or len(meeting_numbers) != len(set(meeting_numbers))):
            raise ValueError("The API listing contains invalid or duplicate meeting numbers")

        # Use the API reference date (the meeting date), not the publication date.
        # A contiguous sample check prevents an incomplete listing from replacing an
        # existing mapping with a shorter one.
        meeting_dates = sorted(
            (record["nroReuniao"], record["dataReferencia"])
            for record in listing
            if FIRST_ANALYSIS_MEETING <= record["nroReuniao"] <= LAST_ANALYSIS_MEETING
        )
        if (not meeting_dates
                or [number for number, _ in meeting_dates]
                != list(range(FIRST_ANALYSIS_MEETING, LAST_ANALYSIS_MEETING + 1))):
            raise ValueError("The API listing does not contain a complete sample from meeting 203")
        for meeting, meeting_date in meeting_dates:
            try:
                if date.fromisoformat(meeting_date).isoformat() != meeting_date:
                    raise ValueError("Date is not in YYYY-MM-DD format")
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid reference date for meeting {meeting}: {meeting_date}") from error

        dates_path = MINUTES_DIR / "_dates.csv"
        if dates_path.exists():
            with dates_path.open(encoding="utf-8", newline="") as existing_file:
                existing_dates = csv.DictReader(existing_file)
                if existing_dates.fieldnames != ["meeting_number", "meeting_date"]:
                    raise ValueError(f"Unexpected date-mapping columns: {dates_path}")
                existing_numbers = [int(row["meeting_number"]) for row in existing_dates]
            if (len(existing_numbers) != len(set(existing_numbers))
                    or not set(range(FIRST_ANALYSIS_MEETING, LAST_ANALYSIS_MEETING + 1)).issubset(set(existing_numbers))):
                raise ValueError("The API listing is missing meetings in the existing date mapping")

        date_buffer = io.StringIO(newline="")
        date_writer = csv.writer(date_buffer, lineterminator="\n")
        date_writer.writerow(["meeting_number", "meeting_date"])
        date_writer.writerows(meeting_dates)
        date_bytes = date_buffer.getvalue().encode("utf-8")

        missing_meetings = [
            number for number in range(FIRST_ANALYSIS_MEETING, LAST_ANALYSIS_MEETING + 1)
            if not (MINUTES_DIR / f"ata{number}.txt").exists()
        ]
        print(f"API lists {len(meeting_numbers)} meetings; {len(missing_meetings)} files are missing")

        # Prepare all missing texts before writing any of them. This keeps an API or
        # extraction error from leaving an incomplete set of newly created files.
        minutes = {}
        for meeting in missing_meetings:
            response = session.get(
                f"{API_URL}/atas_detalhes", params={"nro_reuniao": meeting}, timeout=30
            )
            response.raise_for_status()
            records = response.json()["conteudo"]
            if len(records) != 1 or records[0]["nroReuniao"] != meeting:
                raise ValueError(f"Unexpected API response for meeting {meeting}")

            record = records[0]
            html = record.get("textoAta")
            if html:
                document = BeautifulSoup(html, "html.parser")
                discussion = document.select_one("#ataconteudo")

                if discussion:
                    # Match the existing modern files: omit footnote markers, tables,
                    # footnotes and the paragraph listing the members who voted.
                    for marker in discussion.select("sup"):
                        marker.decompose()
                    blocks = []
                    paragraph_numbers = []
                    headings = []
                    found_vote = False
                    for element in discussion.select("h3.secao, p.paragrafo"):
                        text = " ".join(element.get_text().split())
                        if element.name == "h3":
                            headings.append(text[:2])
                            blocks.append(text)
                            continue
                        number = re.match(r"^(\d+)\.\s+", text)
                        if number is None:
                            raise ValueError(f"Unexpected paragraph in meeting {meeting}: {text[:80]}")
                        if re.match(r"^\d+\.\s+Votaram por essa decisão\b", text):
                            if int(number.group(1)) != len(paragraph_numbers) + 1:
                                raise ValueError(f"Unexpected voting paragraph number in meeting {meeting}")
                            found_vote = True
                            break
                        paragraph_numbers.append(int(number.group(1)))
                        blocks.append(text)

                    if (headings != ["A)", "B)", "C)", "D)"] or not found_vote
                            or paragraph_numbers != list(range(1, len(paragraph_numbers) + 1))):
                        raise ValueError(f"Incomplete or unexpected structure for meeting {meeting}")
                    text = "\n\n".join(blocks)
                    source_format = "modern HTML"
                else:
                    # Earlier API records have varied HTML and no common discussion
                    # container. Preserve their document text and block boundaries.
                    for element in document.select("script, style"):
                        element.decompose()
                    for element in document.find_all("br"):
                        element.replace_with("\n")
                    for element in document.find_all(
                        ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"]
                    ):
                        element.insert_before("\n\n")
                        element.insert_after("\n\n")
                    blocks = [
                        " ".join(block.split())
                        for block in re.split(r"\n\s*\n", document.get_text())
                        if block.strip()
                    ]
                    text = "\n\n".join(blocks)
                    source_format = "historical HTML"
            elif record.get("urlPdfAta"):
                # The API supplies only PDF links for meetings 200–231. Extract their
                # text blocks in the PDF's native order; sorting by page coordinates
                # interleaves the two columns in these documents.
                response = session.get(record["urlPdfAta"], timeout=60)
                response.raise_for_status()
                with fitz.open(stream=response.content, filetype="pdf") as pdf:
                    blocks = [
                        " ".join(block[4].split())
                        for page in pdf
                        for block in page.get_text("blocks", sort=False)
                        if block[4].strip()
                    ]
                text = "\n\n".join(blocks)
                source_format = "PDF"
            else:
                raise ValueError(f"No text or PDF is available for meeting {meeting}")

            if len(text) < 500:
                raise ValueError(f"Extracted text is unexpectedly short for meeting {meeting}")
            minutes[meeting] = text
            print(f"Prepared meeting {meeting} ({source_format}, {len(text)} characters)")

        # Exclusive creation protects files even if another process creates one after
        # the initial directory check. Existing files are never opened for writing.
        for meeting, text in minutes.items():
            destination = MINUTES_DIR / f"ata{meeting}.txt"
            try:
                with destination.open("xb") as output:
                    output.write(text.encode("utf-8"))
            except FileExistsError:
                print(f"Skipped existing file: {destination.name}")
            else:
                print(f"Saved: {destination.name}")

        # Replace the date mapping only after all requested minute texts are prepared.
        # A temporary file in the same directory keeps the previous CSV intact if
        # writing fails and allows the final replacement to be atomic.
        if not dates_path.exists() or dates_path.read_bytes() != date_bytes:
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=MINUTES_DIR, delete=False) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    temporary_file.write(date_bytes)
                temporary_path.replace(dates_path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            print(f"Updated: {dates_path.name} ({len(meeting_dates)} meetings)")
        else:
            print(f"Already current: {dates_path.name} ({len(meeting_dates)} meetings)")

        for path, previous_hash in existing_minute_hashes.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != previous_hash:
                raise ValueError(f"Existing minute text changed: {path}")
        metadata_parent.mkdir(parents=True, exist_ok=True)
        metadata = [{"meeting_number": r["nroReuniao"], "meeting_date": r["dataReferencia"], "supplied_publication_metadata": {k:v for k,v in r.items() if "public" in k.lower()}} for r in listing if FIRST_ANALYSIS_MEETING <= r["nroReuniao"] <= LAST_ANALYSIS_MEETING]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        with (metadata_parent / f"copom_publication_metadata_{stamp}.json").open("x", encoding="utf-8") as output:
            json.dump({"retrieved_at":datetime.now(timezone.utc).isoformat(),"meeting_date_definition":"API dataReferencia; publication fields remain separate","meetings":metadata},output,ensure_ascii=False,indent=2)
        print("Finished. Existing minute text hashes are unchanged.")
