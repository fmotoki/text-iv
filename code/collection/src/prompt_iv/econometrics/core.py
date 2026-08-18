from __future__ import annotations

import numpy as np
import pandas as pd


def add_constant(df: pd.DataFrame, columns: list[str]) -> np.ndarray:
    return np.column_stack([np.ones(len(df)), df[columns].to_numpy(dtype=float)])


def ols(y: np.ndarray, x: np.ndarray) -> dict[str, object]:
    y = np.asarray(y, dtype=float).reshape(-1, 1)
    x = np.asarray(x, dtype=float)
    beta = np.linalg.lstsq(x, y, rcond=None)[0].reshape(-1)
    fitted = x @ beta
    residuals = y.reshape(-1) - fitted
    ssr = float(np.sum(residuals**2))
    tss = float(np.sum((y.reshape(-1) - y.mean()) ** 2))
    r2 = 1.0 - ssr / tss if tss else float("nan")
    return {"beta": beta, "fitted": fitted, "residuals": residuals, "r2": r2, "ssr": ssr, "n": len(y)}


def residualize_by_group(values: pd.Series, groups: pd.Series) -> pd.Series:
    return values.astype(float) - values.astype(float).groupby(groups).transform("mean")


def tsls_just_identified(y: pd.Series, treatment: pd.Series, instrument: pd.Series, groups: pd.Series) -> dict[str, float]:
    """Just-identified IV after residualizing all variables by fixed-effect groups."""
    y_res = residualize_by_group(y, groups)
    x_res = residualize_by_group(treatment, groups)
    z_res = residualize_by_group(instrument, groups)
    denominator = float(np.dot(z_res, x_res))
    if abs(denominator) < 1e-12:
        raise ValueError("Instrument has no residual variation after fixed effects.")
    beta = float(np.dot(z_res, y_res) / denominator)
    first_stage = float(np.dot(z_res, x_res) / np.dot(z_res, z_res))
    y_hat = beta * x_res
    residuals = y_res - y_hat
    tss = float(np.sum((y_res - y_res.mean()) ** 2))
    ssr = float(np.sum(residuals**2))
    r2 = 1.0 - ssr / tss if tss else float("nan")
    return {
        "beta_iv": beta,
        "first_stage_coef": first_stage,
        "r2_within": r2,
        "n": float(len(y_res)),
        "clusters": float(groups.nunique()),
    }
