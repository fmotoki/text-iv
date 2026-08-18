from __future__ import annotations

from pathlib import Path

from prompt_iv.generation.numeric import check_numeric_multiset
from prompt_iv.io.minutes import list_minutes, read_text, segment_text
from prompt_iv.llm.runner import DryRunRunner, LLMRequest
from prompt_iv.paths import load_paths
from prompt_iv.tone import SentimentAnalyzer


def run_smoke(root: Path | None = None) -> dict[str, object]:
    paths = load_paths(root)
    minutes_dir = paths.raw_data_dir / "copom_minutes"
    if not minutes_dir.exists():
        minutes_dir = paths.minutes_dir

    minutes = list_minutes(minutes_dir)
    if not minutes:
        raise RuntimeError(f"No minutes found in {minutes_dir}")

    sample = minutes[-1]
    text = read_text(sample.path)
    blocks = segment_text(text)
    analyzer = SentimentAnalyzer()
    tone = analyzer.analyze(text)
    ok, missing, extra = check_numeric_multiset("Selic 15,00% em 2025", "Selic 15,00% em 2025")

    runner = DryRunRunner(log_path=paths.logs_dir / "smoke_llm.jsonl")
    response = runner.generate(
        LLMRequest(
            system_prompt="Smoke test only.",
            user_prompt=f"Meeting {sample.meeting_number}, words={tone.word_count}",
            metadata={"smoke": True},
        )
    )

    return {
        "minutes_count": len(minutes),
        "sample_meeting": sample.meeting_number,
        "sample_blocks": len(blocks),
        "sample_word_count": tone.word_count,
        "sample_tone_score": tone.tone_score,
        "numeric_check": ok,
        "numeric_missing": missing,
        "numeric_extra": extra,
        "llm_dry_run": response.dry_run,
        "llm_log": str(paths.logs_dir / "smoke_llm.jsonl"),
        "minutes_dir": str(minutes_dir),
    }
