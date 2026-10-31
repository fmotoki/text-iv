"""Download all available Copom minutes without overwriting existing files.

Requires requests, beautifulsoup4 and PyMuPDF (imported as fitz). The Banco
Central API provides older minutes as HTML or links to PDFs. Historical files
retain the available document text; modern files contain the policy discussion
in the same format as the existing recent minutes.
"""

import re
from pathlib import Path

import fitz
import requests
from bs4 import BeautifulSoup


# The script lives in replication/code/01_data_preparation.
MINUTES_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "copom_minutes"
API_URL = "https://www.bcb.gov.br/api/servico/sitebcb/copom"

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

print(f"Finished. Existing files were left unchanged.")
