
from __future__ import annotations

import os
from typing import Set

from .pipeline import MinifyConfig


_DEFAULT_STAGES = ("tools", "system", "messages", "dedup", "distill")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_tool_skip() -> Set[str]:
    raw = os.environ.get("SLIMTOKEN_MINIFY_TOOL_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def build_config() -> MinifyConfig:

    if not _bool("SLIMTOKEN_MINIFY", True):
        return MinifyConfig(enabled_stages=set(), tool_skip=_env_tool_skip())
    stages = {s for s in _DEFAULT_STAGES if _bool(f"SLIMTOKEN_MINIFY_{s.upper()}", True)}
    return MinifyConfig(
        token_budget=_int("SLIMTOKEN_MINIFY_BUDGET", 131072),
        enabled_stages=stages,
        tool_skip=_env_tool_skip(),
        keep_last=_int("SLIMTOKEN_KEEP_LAST", 4),
        dedup_min_chars=_int("SLIMTOKEN_DEDUP_MIN_CHARS", 200),
        distill_max_chars=_int("SLIMTOKEN_DISTILL_MAX_CHARS", 160),
        distill_include_user=_bool("SLIMTOKEN_DISTILL_INCLUDE_USER", False),
        tool_compress=_bool("SLIMTOKEN_TOOL_COMPRESS", False),
        minify_dom=_bool("SLIMTOKEN_MINIFY_DOM", False),
    )




build_minify_cfg = build_config