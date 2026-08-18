from __future__ import annotations

from pathlib import Path

import pandas as pd


def summarize_table(path: str | Path, nrows: int = 0) -> dict[str, object]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".dta":
        df = pd.read_stata(path, columns=None)
    elif suffix == ".csv":
        df = pd.read_csv(path, nrows=nrows or None, low_memory=False)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, nrows=nrows or None)
    else:
        raise ValueError(f"Unsupported table format: {path}")
    return {
        "path": str(path),
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "column_names": [str(column) for column in df.columns],
    }


def summarize_data_dir(folder: str | Path) -> pd.DataFrame:
    rows = []
    for path in sorted(Path(folder).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".csv", ".dta", ".xlsx", ".xls"}:
            continue
        try:
            summary = summarize_table(path)
            rows.append(
                {
                    "path": str(path),
                    "rows": summary["rows"],
                    "columns": summary["columns"],
                    "status": "ok",
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "path": str(path),
                    "rows": None,
                    "columns": None,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return pd.DataFrame(rows)
