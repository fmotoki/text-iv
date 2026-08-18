from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .lexicon import DEFAULT_LEXICON


@dataclass(frozen=True)
class ToneResult:
    tone_score: float
    hawk_count: int
    dove_count: int
    word_count: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


class SentimentAnalyzer:
    def __init__(self, lexicon: dict | None = None) -> None:
        self.lexicon = lexicon or DEFAULT_LEXICON
        self.patterns: dict[str, list[re.Pattern[str]]] = {}
        for category, terms in self.lexicon.items():
            all_terms = terms.get("unigrams", []) + terms.get("bigrams", []) + terms.get("ngrams", [])
            all_terms = sorted(all_terms, key=len, reverse=True)
            self.patterns[category] = [
                re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE) for term in all_terms
            ]

    def analyze(self, text: str | None) -> ToneResult:
        if not isinstance(text, str) or not text:
            return ToneResult(tone_score=0.0, hawk_count=0, dove_count=0, word_count=0)

        words = re.findall(r"\b\w+\b", text, flags=re.UNICODE)
        word_count = len(words)
        hawk_count = sum(len(pattern.findall(text)) for pattern in self.patterns["hawkish"])
        dove_count = sum(len(pattern.findall(text)) for pattern in self.patterns["dovish"])
        score = (hawk_count - dove_count) / word_count if word_count else 0.0
        return ToneResult(score, hawk_count, dove_count, word_count)
