"""Check a rebuilt public-data snapshot for meetings 203 through 281.

This checks publication-date coverage, not the statistical model. Same-day
releases remain explicit because meeting dates alone do not establish timing.
"""
import argparse
import calendar
import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="Also compare numeric files with the original snapshot, if retained locally")
    parser.add_argument("--dates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.snapshot / "source_manifest.json").read_text(encoding="utf-8"))

    # Verify the saved evidence and ensure the rebuild did not change the numbers.
    for record in manifest["requests"]:
        actual = hashlib.sha256((args.snapshot / record["file"]).read_bytes()).hexdigest()
        if actual != record["sha256"]:
            raise ValueError("Original-response hash mismatch: " + record["file"])
    for name, expected in manifest["normalized_sha256"].items():
        if hashlib.sha256((args.snapshot / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Normalized-file hash mismatch: " + name)
    if args.previous is not None:
        for name in ("sgs.csv", "focus.csv"):
            if (args.snapshot / name).read_bytes() != (args.previous / name).read_bytes():
                raise ValueError("Numeric input changed during replay: " + name)
    verified = {int(r["series_code"]) for r in manifest["requests"] if r.get("metadata_status") == "verified"}
    if verified != {7478, 24364, 10813, 432, 13522, 24363, 433, 29602, 29604, 29606}:
        raise ValueError("Not all ten SGS definitions are verified")

    with args.dates.open(encoding="utf-8", newline="") as stream:
        meetings = list(csv.DictReader(stream))
    if [int(r["meeting_number"]) for r in meetings] != list(range(203, 282)):
        raise ValueError("Meeting sample must be exactly 203 through 281")
    with (args.snapshot / "ibc_release_calendar.csv").open(encoding="utf-8", newline="") as stream:
        events = list(csv.DictReader(stream))
    actual_events = [r for r in events if r["series_code"] == "24364" and r["event_status"] == "actual"]
    with (args.snapshot / "sgs.csv").open(encoding="utf-8", newline="") as stream:
        sgs = list(csv.DictReader(stream))
    ibc = {r["date"][:7]: r for r in sgs if r["series_code"] == "24364"}
    with (args.snapshot / "focus.csv").open(encoding="utf-8", newline="") as stream:
        focus = list(csv.DictReader(stream))
    targets = [r for r in focus if r["source_entity"] == "ExpectativasMercadoInflacao12Meses" and r["indicator"] == "IPCA" and r["smoothed"] == "N" and r["calculation_base"] == "0"]

    # Keep every meeting. Never fall back past an unresolved latest notice.
    coverage = []
    for meeting in meetings:
        day = meeting["meeting_date"]
        prior = [r for r in actual_events if r["actual_release_date"] < day]
        if not prior:
            raise ValueError("No prior aggregate IBC-Br release for meeting " + meeting["meeting_number"])
        selected = max(prior, key=lambda r: datetime.fromisoformat(r["actual_release_timestamp"].replace("Z", "+00:00")))
        month = selected["reference_month"]
        if not month or month not in ibc:
            raise ValueError("Latest release has no matching numeric observation for meeting " + meeting["meeting_number"])
        if len([r for r in targets if r["date"] == day]) != 1:
            raise ValueError("Missing/ambiguous same-date Focus target for meeting " + meeting["meeting_number"])
        year, month_number = map(int, month.split("-"))
        month_end = date(year, month_number, calendar.monthrange(year, month_number)[1])
        publication_day = date.fromisoformat(selected["actual_release_date"])
        if publication_day <= month_end:
            raise ValueError("Release precedes the end of its reference month")
        same_day = [r for r in actual_events if r["actual_release_date"] == day]
        coverage.append(dict(meeting_number=int(meeting["meeting_number"]), meeting_date=day,
            reference_month=month, publication_date=selected["actual_release_date"],
            publication_timestamp=selected["actual_release_timestamp"], release_id=selected["release_id"],
            publication_lag_days=(publication_day-month_end).days,
            same_day_release_ids=" | ".join(r["release_id"] for r in same_day),
            timing_status="SAME_DAY_RELEASE_REQUIRES_MEETING_TIME" if same_day else "PRIOR_DATE_RELEASE_AVAILABLE",
            current_value=ibc[month]["value"], value_status="CURRENT_REVISED_VALUE_NOT_ORIGINAL_VINTAGE",
            source_url=selected["evidence_url"]))

    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "meeting_bcb_coverage.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(coverage[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(coverage)
    summary = dict(status="PASS_PUBLICATION_DATE_COVERAGE", meetings=len(coverage),
        first_meeting=coverage[0]["meeting_number"], last_meeting=coverage[-1]["meeting_number"],
        numeric_files_unchanged=True if args.previous is not None else None, verified_sgs_series=sorted(verified),
        original_response_hashes_verified=len(manifest["requests"]),
        normalized_hashes_verified=len(manifest["normalized_sha256"]),
        missing_reference_months_actual=sum(r["event_status"] == "actual" and not r["reference_month"] for r in events),
        same_day_meetings=[r["meeting_number"] for r in coverage if r["same_day_release_ids"]],
        vintage_limitation="Current values remain distinct from original-release values; this is not full model readiness.")
    (args.output / "bcb_validation.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
