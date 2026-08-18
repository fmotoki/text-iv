"""Counterfactual text generation helpers."""

from .corpus import assemble_counterfactual_corpus
from .numeric import check_numeric_multiset, extract_numeric_tokens
from .execution import execute_rewrite_manifest
from .prompts import build_rewrite_prompt, build_rewrite_prompt_versioned, normalize_prompt_version
from .tasks import RewriteTask, build_rewrite_tasks, write_rewrite_task_manifest

__all__ = [
    "RewriteTask",
    "assemble_counterfactual_corpus",
    "build_rewrite_prompt",
    "build_rewrite_prompt_versioned",
    "build_rewrite_tasks",
    "check_numeric_multiset",
    "execute_rewrite_manifest",
    "extract_numeric_tokens",
    "normalize_prompt_version",
    "write_rewrite_task_manifest",
]
