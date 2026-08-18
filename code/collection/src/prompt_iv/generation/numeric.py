from __future__ import annotations

from collections import Counter
import re


NUM_TOKEN_RE = re.compile(
    r"(?<!\w)[+-]?\d{1,3}(?:\.\d{3})*(?:,\d+)?%?(?!\w)"
    r"|(?<!\w)[+-]?\d+(?:,\d+)?%?(?!\w)",
    flags=re.UNICODE,
)


def extract_numeric_tokens(text: str) -> Counter[str]:
    return Counter(NUM_TOKEN_RE.findall(text or ""))


def check_numeric_multiset(original_text: str, generated_text: str) -> tuple[bool, list[str], list[str]]:
    original = extract_numeric_tokens(original_text)
    generated = extract_numeric_tokens(generated_text)
    missing = list((original - generated).elements())
    extra = list((generated - original).elements())
    return not missing and not extra, missing, extra
