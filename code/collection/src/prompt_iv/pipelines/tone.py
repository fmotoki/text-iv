from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from prompt_iv.io.minutes import list_minutes, read_text
from prompt_iv.tone import SentimentAnalyzer


def score_minutes_folder(minutes_dir: str | Path, output_csv: str | Path | None = None) -> pd.DataFrame:
    analyzer = SentimentAnalyzer()
    rows = []
    for item in list_minutes(minutes_dir):
        text = read_text(item.path)
        result = analyzer.analyze(text)
        rows.append(
            {
                "reuniao": item.meeting_number,
                "filename": item.path.name,
                "tone_score": result.tone_score,
                "word_count": result.word_count,
                "hawk_terms": result.hawk_count,
                "dove_terms": result.dove_count,
            }
        )
    df = pd.DataFrame(rows)
    if output_csv:
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_csv, index=False)
    return df


def score_text_columns(
    input_csv: str | Path,
    text_columns: Iterable[str],
    output_csv: str | Path | None = None,
    drop_text: bool = True,
) -> pd.DataFrame:
    df = pd.read_csv(input_csv, low_memory=False)
    analyzer = SentimentAnalyzer()
    text_columns = list(text_columns)
    for column in text_columns:
        if column not in df.columns:
            raise KeyError(f"Column {column!r} not found in {input_csv}")
        results = df[column].apply(analyzer.analyze)
        df[f"{column}_score"] = results.apply(lambda item: item.tone_score)
        df[f"{column}_hawk_count"] = results.apply(lambda item: item.hawk_count)
        df[f"{column}_dove_count"] = results.apply(lambda item: item.dove_count)
        df[f"{column}_word_count"] = results.apply(lambda item: item.word_count)
    if drop_text:
        df = df.drop(columns=[column for column in text_columns if column in df.columns])
    if output_csv:
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_csv, index=False)
    return df


def score_counterfactual_minutes(
    input_csv: str | Path,
    output_csv: str | Path | None = None,
    drop_text: bool = True,
) -> pd.DataFrame:
    return score_text_columns(
        input_csv=input_csv,
        text_columns=["original_text", "hawk_text", "dove_text"],
        output_csv=output_csv,
        drop_text=drop_text,
    )
