"""Download available Copom minutes and update their meeting-date mapping.

Requires requests, beautifulsoup4 and PyMuPDF (imported as fitz). The Banco
Central API provides older minutes as HTML or links to PDFs. Historical files
retain the available document text; modern files contain the policy discussion
in the same format as the existing recent minutes. Existing minute text files
are never overwritten; _dates.csv is refreshed for meetings 203 onward.
"""

import csv
import io
import re
import tempfile
from datetime import date
from pathlib import Path

import fitz
import requests
from bs4 import BeautifulSoup


# The script lives in replication/code/01_data_preparation.
MINUTES_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "copom_minutes"
API_URL = "https://www.bcb.gov.br/api/servico/sitebcb/copom"
FIRST_ANALYSIS_MEETING = 203

# Increase the requested count if the listing fills the current limit.
quantity = 1000
while True:
    response = requests.get(f"{API_URL}/atas", params={"quantidade": quantity}, timeout=30)
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
    if record["nroReuniao"] >= FIRST_ANALYSIS_MEETING
)
if (not meeting_dates
        or [number for number, _ in meeting_dates]
        != list(range(FIRST_ANALYSIS_MEETING, meeting_dates[-1][0] + 1))):
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
            or not set(existing_numbers).issubset({number for number, _ in meeting_dates})):
        raise ValueError("The API listing is missing meetings in the existing date mapping")

date_buffer = io.StringIO(newline="")
date_writer = csv.writer(date_buffer, lineterminator="\n")
date_writer.writerow(["meeting_number", "meeting_date"])
date_writer.writerows(meeting_dates)
date_bytes = date_buffer.getvalue().encode("utf-8")

missing_meetings = [
    number for number in sorted(meeting_numbers)
    if not (MINUTES_DIR / f"ata{number}.txt").exists()
]
print(f"API lists {len(meeting_numbers)} meetings; {len(missing_meetings)} files are missing")

# Prepare all missing texts before writing any of them. This keeps an API or
# extraction error from leaving an incomplete set of newly created files.
minutes = {}
for meeting in missing_meetings:
    response = requests.get(
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
        response = requests.get(record["urlPdfAta"], timeout=60)
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

print("Finished. Existing minute text files were left unchanged.")
