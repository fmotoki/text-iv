from __future__ import annotations

import importlib.metadata
import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from tokenizers import Tokenizer


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "interim" / "prompt_iv.sqlite"
RUN_IDS = (
    "v10_referee_d5_25draws_20260620",
    "v10_referee_step3_neutral_dose_5draws_20260627",
)
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIMENSIONS = 384
MAX_SENTENCES_PER_DOCUMENT = 12
MAX_WORDS_PER_SEGMENT = 80
BATCH_SIZE = 256
THREADS = 6


@dataclass(frozen=True)
class CorpusRow:
    run_id: str
    meeting_number: int
    iteration: int
    source_role: str
    text_hash: str
    text: str


def split_sentences(text: str) -> list[str]:
    rough_segments = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    segments: list[str] = []
    for rough in rough_segments:
        cleaned = re.sub(r"\s+", " ", rough).strip()
        words = cleaned.split()
        if len(words) < 3:
            continue
        for start in range(0, len(words), MAX_WORDS_PER_SEGMENT):
            chunk = " ".join(words[start : start + MAX_WORDS_PER_SEGMENT])
            if len(chunk.split()) >= 3:
                segments.append(chunk)
    return segments or [re.sub(r"\s+", " ", text).strip()]


def positional_sample(segments: list[str]) -> list[str]:
    if len(segments) <= MAX_SENTENCES_PER_DOCUMENT:
        return segments
    indices = np.linspace(
        0,
        len(segments) - 1,
        MAX_SENTENCES_PER_DOCUMENT,
        dtype=int,
    )
    return [segments[index] for index in indices]


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def normalized_mean(matrix: np.ndarray) -> np.ndarray:
    vector = normalize_rows(matrix).mean(axis=0)
    norm = np.linalg.norm(vector)
    return vector if norm == 0 else vector / norm


def jensen_shannon(left: Counter[int], right: Counter[int]) -> float:
    left_total = sum(left.values())
    right_total = sum(right.values())
    if left_total == 0 or right_total == 0:
        return math.nan
    divergence = 0.0
    for token_id in left.keys() | right.keys():
        p = left.get(token_id, 0) / left_total
        q = right.get(token_id, 0) / right_total
        midpoint = 0.5 * (p + q)
        if p > 0:
            divergence += 0.5 * p * math.log(p / midpoint)
        if q > 0:
            divergence += 0.5 * q * math.log(q / midpoint)
    return divergence


def nearest_sentence_metrics(
    rewrite_matrix: np.ndarray,
    original_matrix: np.ndarray,
) -> tuple[float, float, float, float]:
    rewrite_normalized = normalize_rows(rewrite_matrix)
    original_normalized = normalize_rows(original_matrix)
    similarities = rewrite_normalized @ original_normalized.T
    forward = similarities.max(axis=1)
    reverse = similarities.max(axis=0)
    combined = np.concatenate((forward, reverse))
    return (
        float(forward.mean()),
        float(reverse.mean()),
        float(np.quantile(combined, 0.05)),
        float(np.mean(combined < 0.80)),
    )


def fetch_rows(connection: sqlite3.Connection) -> list[CorpusRow]:
    placeholders = ",".join("?" for _ in RUN_IDS)
    query = f"""
        SELECT run_id, meeting_number, iteration, role, text_hash, text
        FROM corpus_texts
        WHERE run_id IN ({placeholders}) AND complete = 1
        ORDER BY run_id, meeting_number, iteration, role
    """
    return [CorpusRow(*row) for row in connection.execute(query, RUN_IDS)]


def prepare_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS referee_text_embeddings (
            run_id TEXT NOT NULL,
            meeting_number INTEGER NOT NULL,
            iteration INTEGER NOT NULL,
            source_role TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            model_id TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            sentence_total_count INTEGER NOT NULL,
            sentence_sample_count INTEGER NOT NULL,
            subword_count INTEGER NOT NULL,
            embedding_blob BLOB NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (
                run_id, meeting_number, iteration, source_role, model_id
            )
        );

        CREATE TABLE IF NOT EXISTS referee_embedding_pair_metrics (
            run_id TEXT NOT NULL,
            meeting_number INTEGER NOT NULL,
            iteration INTEGER NOT NULL,
            source_role TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            original_text_hash TEXT NOT NULL,
            model_id TEXT NOT NULL,
            document_cosine_similarity REAL NOT NULL,
            rewrite_to_original_sentence_similarity REAL NOT NULL,
            original_to_rewrite_sentence_similarity REAL NOT NULL,
            sentence_similarity_p05 REAL NOT NULL,
            sentence_share_below_080 REAL NOT NULL,
            subword_jensen_shannon REAL NOT NULL,
            subword_count_ratio REAL NOT NULL,
            sentence_count_ratio REAL NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (
                run_id, meeting_number, iteration, source_role, model_id
            )
        );

        CREATE TABLE IF NOT EXISTS referee_embedding_manifest (
            model_id TEXT PRIMARY KEY,
            fastembed_version TEXT NOT NULL,
            tokenizer_id TEXT NOT NULL,
            pooling TEXT NOT NULL,
            sentence_sampling TEXT NOT NULL,
            max_sentences_per_document INTEGER NOT NULL,
            max_words_per_segment INTEGER NOT NULL,
            threads INTEGER NOT NULL,
            generated_at TEXT NOT NULL
        );
        """
    )


def main() -> None:
    connection = sqlite3.connect(DATABASE_PATH, timeout=60)
    connection.execute("PRAGMA busy_timeout = 60000")
    rows = fetch_rows(connection)
    if len(rows) != 7455:
        raise RuntimeError(f"Expected 7,455 logical corpus rows, found {len(rows)}")

    texts_by_hash: dict[str, str] = {}
    for row in rows:
        texts_by_hash.setdefault(row.text_hash, row.text)
    if len(texts_by_hash) != 5396:
        raise RuntimeError(
            f"Expected 5,396 unique text hashes, found {len(texts_by_hash)}"
        )

    hashes = list(texts_by_hash)
    all_segments: list[str] = []
    segment_slices: dict[str, slice] = {}
    total_sentence_counts: dict[str, int] = {}
    sampled_sentence_counts: dict[str, int] = {}
    for text_hash in hashes:
        sentences = split_sentences(texts_by_hash[text_hash])
        sampled = positional_sample(sentences)
        start = len(all_segments)
        all_segments.extend(sampled)
        segment_slices[text_hash] = slice(start, len(all_segments))
        total_sentence_counts[text_hash] = len(sentences)
        sampled_sentence_counts[text_hash] = len(sampled)

    print(
        f"Embedding {len(all_segments):,} sampled sentence segments "
        f"from {len(hashes):,} unique texts...",
        flush=True,
    )
    embedder = TextEmbedding(model_name=MODEL_ID, threads=THREADS)
    sentence_embeddings = np.vstack(
        list(embedder.embed(all_segments, batch_size=BATCH_SIZE))
    ).astype(np.float32, copy=False)
    if sentence_embeddings.shape != (len(all_segments), EMBEDDING_DIMENSIONS):
        raise RuntimeError(
            f"Unexpected embedding shape: {sentence_embeddings.shape}"
        )

    tokenizer = Tokenizer.from_pretrained(MODEL_ID)
    tokenizer.no_truncation()
    document_embeddings: dict[str, np.ndarray] = {}
    token_counts: dict[str, Counter[int]] = {}
    subword_counts: dict[str, int] = {}
    for text_hash in hashes:
        matrix = sentence_embeddings[segment_slices[text_hash]]
        document_embeddings[text_hash] = normalized_mean(matrix).astype(np.float32)
        token_ids = tokenizer.encode(
            texts_by_hash[text_hash],
            add_special_tokens=False,
        ).ids
        token_counts[text_hash] = Counter(token_ids)
        subword_counts[text_hash] = len(token_ids)

    originals: dict[tuple[str, int], CorpusRow] = {}
    for row in rows:
        if row.source_role == "original":
            originals.setdefault((row.run_id, row.meeting_number), row)
    if len(originals) != 142:
        raise RuntimeError(f"Expected 142 run-meeting originals, found {len(originals)}")

    generated_at = datetime.now(timezone.utc).isoformat()
    embedding_records = []
    pair_records = []
    for row in rows:
        embedding = document_embeddings[row.text_hash]
        embedding_records.append(
            (
                row.run_id,
                row.meeting_number,
                row.iteration,
                row.source_role,
                row.text_hash,
                MODEL_ID,
                EMBEDDING_DIMENSIONS,
                total_sentence_counts[row.text_hash],
                sampled_sentence_counts[row.text_hash],
                subword_counts[row.text_hash],
                sqlite3.Binary(embedding.tobytes()),
                generated_at,
            )
        )
        if row.source_role == "original":
            continue

        original = originals[(row.run_id, row.meeting_number)]
        original_embedding = document_embeddings[original.text_hash]
        document_cosine = float(embedding @ original_embedding)
        forward, reverse, similarity_p05, share_below = nearest_sentence_metrics(
            sentence_embeddings[segment_slices[row.text_hash]],
            sentence_embeddings[segment_slices[original.text_hash]],
        )
        pair_records.append(
            (
                row.run_id,
                row.meeting_number,
                row.iteration,
                row.source_role,
                row.text_hash,
                original.text_hash,
                MODEL_ID,
                document_cosine,
                forward,
                reverse,
                similarity_p05,
                share_below,
                jensen_shannon(
                    token_counts[row.text_hash],
                    token_counts[original.text_hash],
                ),
                subword_counts[row.text_hash] / subword_counts[original.text_hash],
                total_sentence_counts[row.text_hash]
                / total_sentence_counts[original.text_hash],
                generated_at,
            )
        )

    prepare_schema(connection)
    with connection:
        connection.execute(
            "DELETE FROM referee_text_embeddings WHERE model_id = ?",
            (MODEL_ID,),
        )
        connection.execute(
            "DELETE FROM referee_embedding_pair_metrics WHERE model_id = ?",
            (MODEL_ID,),
        )
        connection.executemany(
            """
            INSERT INTO referee_text_embeddings VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            embedding_records,
        )
        connection.executemany(
            """
            INSERT INTO referee_embedding_pair_metrics VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            pair_records,
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO referee_embedding_manifest VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                MODEL_ID,
                importlib.metadata.version("fastembed"),
                MODEL_ID,
                "Mean of L2-normalized sampled-sentence embeddings, then L2-normalized",
                "Evenly spaced positional sample over all sentence segments",
                MAX_SENTENCES_PER_DOCUMENT,
                MAX_WORDS_PER_SEGMENT,
                THREADS,
                generated_at,
            ),
        )

    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM referee_text_embeddings WHERE model_id = ?),
            (SELECT COUNT(*) FROM referee_embedding_pair_metrics WHERE model_id = ?)
        """,
        (MODEL_ID, MODEL_ID),
    ).fetchone()
    connection.close()
    print(
        f"Stored {counts[0]:,} logical embeddings and {counts[1]:,} "
        "rewrite-original pair metrics directly in SQLite.",
        flush=True,
    )


if __name__ == "__main__":
    main()
