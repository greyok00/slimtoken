
from __future__ import annotations

import os
import sys
from typing import Dict, Set

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










































_MODE_CODE = "code"
_MODE_REALTIME = "realtime"

MODES: Dict[str, Dict] = {

    _MODE_CODE: {
        "stages": {"dedup", "distill"},
        "keep_last": 4,
        "distill_max_chars": 4096,
        "distill_include_user": False,







        "tool_compress": False,
        "minify_dom": False,
        "dedup_min_chars": 200,
        "token_budget": 131072,
    },

    _MODE_REALTIME: {
        "stages": {"tools", "system", "messages", "dedup", "distill"},
        "keep_last": 2,
        "distill_max_chars": 160,
        "distill_include_user": True,
        "tool_compress": True,
        "minify_dom": False,
        "dedup_min_chars": 80,
        "token_budget": 131072,
    },
}

DEFAULT_MODE = _MODE_CODE



MODE_NOTES: Dict[str, str] = {
    _MODE_CODE:
        "default. No tool result is ever rewritten, old or new — the file you "
        "just read and the file you read ten turns ago both reach the model "
        "byte-for-byte. What gets shortened is a tool result byte-identical to "
        "an earlier one (the duplicate is stubbed, the last copy stays) and prose "
        "in an old assistant turn. Tool schemas are left exactly as written.",
    _MODE_REALTIME:
        "lossy, for STT/TTS conversation where nothing is being built. Elides "
        "user turns too, cuts prose to 160 chars/turn, and shortens even the "
        "newest tool result. Do not use it for agent work — elided text still "
        "reads as complete, so the model answers confidently from evidence it "
        "no longer has.",
}


def mode_note(name: str) -> str:
    return MODE_NOTES.get(name, "")


def current_mode() -> str:

    raw = os.environ.get("SLIMTOKEN_MODE", DEFAULT_MODE).strip().lower()
    if raw in MODES:
        return raw
    print(f"slimtoken: unknown SLIMTOKEN_MODE={raw!r} — using "
          f"{DEFAULT_MODE!r} (known: {', '.join(sorted(MODES))})", file=sys.stderr)
    return DEFAULT_MODE


def build_config() -> MinifyConfig:

    if not _bool("SLIMTOKEN_MINIFY", True):
        return MinifyConfig(enabled_stages=set(), tool_skip=_env_tool_skip())
    m = MODES[current_mode()]
    stages = {s for s in _DEFAULT_STAGES
              if _bool(f"SLIMTOKEN_MINIFY_{s.upper()}", s in m["stages"])}
    return MinifyConfig(
        token_budget=_int("SLIMTOKEN_MINIFY_BUDGET", m["token_budget"]),
        enabled_stages=stages,
        tool_skip=_env_tool_skip(),
        keep_last=_int("SLIMTOKEN_KEEP_LAST", m["keep_last"]),
        dedup_min_chars=_int("SLIMTOKEN_DEDUP_MIN_CHARS", m["dedup_min_chars"]),
        distill_max_chars=_int("SLIMTOKEN_DISTILL_MAX_CHARS", m["distill_max_chars"]),
        distill_include_user=_bool("SLIMTOKEN_DISTILL_INCLUDE_USER",
                                   m["distill_include_user"]),
        tool_compress=_bool("SLIMTOKEN_TOOL_COMPRESS", m["tool_compress"]),
        minify_dom=_bool("SLIMTOKEN_MINIFY_DOM", m["minify_dom"]),
    )




build_minify_cfg = build_config
