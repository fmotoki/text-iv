from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class BacklogItem:
    item_id: str
    kind: str
    task: str
    source: str
    effort: str
    blocks: str
    waits_on: str
    status: str = "pending"


ROW_RE = re.compile(
    r"(?P<id>[DT]\d+)\s*&\s*(?P<task>.*?)\s*&\s*(?P<source>.*?)\s*&\s*"
    r"(?P<effort>.*?)\s*&\s*(?P<blocks>.*?)\s*&\s*(?P<waits>.*?)\s*&\s*"
    r"(?P<done>.*?)\s*&",
    flags=re.DOTALL,
)


def _clean_latex(text: str) -> str:
    text = re.sub(r"%.*", "", text)
    text = text.replace("\\texttt{", "").replace("\\emph{", "")
    text = text.replace("\\donebox", "")
    text = text.replace("\\colon", ":")
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("$", "")
    text = text.replace("--", "-")
    return " ".join(text.split())


def parse_revision_plan(path: str | Path) -> list[BacklogItem]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    items: list[BacklogItem] = []
    for match in ROW_RE.finditer(text):
        item_id = _clean_latex(match.group("id"))
        items.append(
            BacklogItem(
                item_id=item_id,
                kind="data" if item_id.startswith("D") else "text",
                task=_clean_latex(match.group("task")),
                source=_clean_latex(match.group("source")),
                effort=_clean_latex(match.group("effort")),
                blocks=_clean_latex(match.group("blocks")),
                waits_on=_clean_latex(match.group("waits")),
                status="pending",
            )
        )
    return items


def write_backlog_yaml(items: list[BacklogItem], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated from referee/revision-plan TeX.",
        "# Status defaults to pending because \\donebox in the TeX source is an empty checkbox.",
        "items:",
    ]
    for item in items:
        lines.extend(
            [
                f"  - id: {item.item_id}",
                f"    kind: {item.kind}",
                f"    status: {item.status}",
                f"    effort: {item.effort!r}",
                f"    source: {item.source!r}",
                f"    blocks: {item.blocks!r}",
                f"    waits_on: {item.waits_on!r}",
                "    task: >-",
                f"      {item.task}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
