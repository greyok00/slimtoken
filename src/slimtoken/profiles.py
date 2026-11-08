"""Optimization mode profiles and environment-driven MinifyConfig assembly."""

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

        "tool_compress": False,   # REMOVED — kept as inert key so old tooling reading MODES doesn't KeyError (owner 2026-11-08)
        "minify_dom": False,
        "dedup_min_chars": 200,
        "token_budget": 131072,
    },

    _MODE_REALTIME: {
        "stages": {"tools", "system", "messages", "dedup", "distill"},
        "keep_last": 2,
        "distill_max_chars": 160,
        "distill_include_user": True,
        "tool_compress": False,   # REMOVED (was True) — source cannot re-enable (owner 2026-11-08)
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
        "user turns too and cuts prose to 160 chars/turn. Do not use it for "
        "agent work — elided text still "
        "reads as complete, so the model answers confidently from evidence it "
        "no longer has.",
}

def mode_note(name: str) -> str:
    """Return the human-readable description of a mode."""
    return MODE_NOTES.get(name, "")

def current_mode() -> str:
    """Resolve SLIMTOKEN_MODE, falling back to the default with a warning."""
    raw = os.environ.get("SLIMTOKEN_MODE", DEFAULT_MODE).strip().lower()
    if raw in MODES:
        return raw
    print(f"slimtoken: unknown SLIMTOKEN_MODE={raw!r} — using "
          f"{DEFAULT_MODE!r} (known: {', '.join(sorted(MODES))})", file=sys.stderr)
    return DEFAULT_MODE

def build_config() -> MinifyConfig:
    """Build a MinifyConfig from the active mode plus SLIMTOKEN_* overrides.

    Owner order 2026-11-08: the lossy result-rewriting stages (tool_compress,
    minify_dom) are REMOVED UNCONDITIONALLY — no mode or env var can enable
    them. tool_compress skeletonized the current turn's tool results (model
    couldn't read its own evidence → task loops, 2026-09-27); minify_dom
    rewrites HTML tool results (2026-10-03). The pipeline stages (tools schema
    minify, system minify, messages, dedup, distill) remain available — they
    proved lossless-with-markers and saved 55% on a live pi session."""
    if not _bool("SLIMTOKEN_MINIFY", True):
        return MinifyConfig(enabled_stages=set(), tool_skip=_env_tool_skip())
    m = MODES[current_mode()]
    stages = {s for s in _DEFAULT_STAGES
              if _bool(f"SLIMTOKEN_MINIFY_{s.upper()}", s in m["stages"])}
    stages -= {"tool_compress", "minify_dom"}  # hard-removed, see docstring
    return MinifyConfig(
        token_budget=_int("SLIMTOKEN_MINIFY_BUDGET", m["token_budget"]),
        enabled_stages=stages,
        tool_skip=_env_tool_skip(),
        keep_last=_int("SLIMTOKEN_KEEP_LAST", m["keep_last"]),
        dedup_min_chars=_int("SLIMTOKEN_DEDUP_MIN_CHARS", m["dedup_min_chars"]),
        distill_max_chars=_int("SLIMTOKEN_DISTILL_MAX_CHARS", m["distill_max_chars"]),
        distill_include_user=_bool("SLIMTOKEN_DISTILL_INCLUDE_USER",
                                   m["distill_include_user"]),
        # tool_compress / minify_dom: REMOVED from MinifyConfig entirely
        # (owner order 2026-11-08) — see docstring above.
    )

build_minify_cfg = build_config
