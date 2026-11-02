from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from prompt_iv.econometrics import add_constant, ols
from prompt_iv.paths import load_spec, specification_metadata, validate_artifact_path


MIXTURE_COLUMNS = [
    "forecast_anchored",
    "forecast_unanchored",
    "wc_orig",
    "ibcbr_agro",
    "ibcbr_service",
    "ibcbr_industry",
]


def estimate_baseline(dataset_path: str | Path, output_json: str | Path | None = None) -> dict[str, object]:
    spec = load_spec(require_frozen=True)
    validate_artifact_path(dataset_path, spec, allow_frozen=True)
    if output_json:
        output_json = validate_artifact_path(output_json, spec)
    df = pd.read_stata(dataset_path).sort_values("reuniao").reset_index(drop=True)
    if df["reuniao"].astype(int).tolist() != spec["sample"]["meeting_numbers"]:
        raise ValueError("Calibration panel does not match the selected exact meeting set")
    needed = ["inflacao12m", *MIXTURE_COLUMNS]
    working = df.dropna(subset=needed).copy()
    if len(working) != len(df):
        raise ValueError("Missing calibration values cannot silently change the selected sample")

    x = add_constant(working, MIXTURE_COLUMNS)
    y = working["inflacao12m"].to_numpy(dtype=float)
    mixture = ols(y, x)
    working["llm_comp"] = mixture["fitted"]

    fit = ols(working["inflacao12m"].to_numpy(dtype=float), add_constant(working, ["llm_comp"]))

    ar = working[["reuniao", "inflacao12m", "score_orig"]].copy()
    ar["lag_inflacao12m"] = ar["inflacao12m"].shift(1)
    ar = ar.dropna()
    focus_ar = ols(ar["inflacao12m"].to_numpy(dtype=float), add_constant(ar, ["score_orig", "lag_inflacao12m"]))
    beta_focus = float(focus_ar["beta"][1])
    rho_focus = float(focus_ar["beta"][2])

    report = {
        **specification_metadata(spec),
        "dataset": str(dataset_path),
        "design_status": (
            "Terra 5.6 original-minute calibration only. Prompt-IV is estimated "
            "from the frozen common-prior panels by the R replication master."
        ),
        "n_meetings": int(working["reuniao"].nunique()),
        "mixture": {
            "columns": ["constant", *MIXTURE_COLUMNS],
            "beta": [float(x) for x in mixture["beta"]],
            "r2": float(mixture["r2"]),
            "n": int(mixture["n"]),
        },
        "llm_fit": {
            "columns": ["constant", "llm_comp"],
            "beta": [float(x) for x in fit["beta"]],
            "r2": float(fit["r2"]),
            "n": int(fit["n"]),
        },
        "focus_ar": {
            "columns": ["constant", "score_orig", "lag_inflacao12m"],
            "beta": [float(x) for x in focus_ar["beta"]],
            "r2": float(focus_ar["r2"]),
            "beta_focus": beta_focus,
            "rho_focus": rho_focus,
            "n": int(focus_ar["n"]),
            "interpretation": (
                "Observational external benchmark only; no long-run transformation "
                "or division by Prompt-IV is reported."
            ),
        },
    }

    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
