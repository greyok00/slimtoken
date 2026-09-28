
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


# ── Modes ────────────────────────────────────────────────────────────────────
# 2026-09-27 (owner): "we need our version to be Code/verbose mode but still be
# 'slimtoken' if that makes sense, then a full lossy fast as fucking possible
# mode for realtime comms."
#
# A mode is a set of STARTING VALUES, not a lock: any SLIMTOKEN_* variable set
# in the environment overrides the mode for that one knob, so a caller can run
# `code` and turn a single stage up without restating the rest.
#
# MODE_CODE (default) — for an agent that reads files, edits them and calls
#   tools. Three things are removed, and the newest turns are never one of them:
#     * a tool result byte-identical to an earlier one — the duplicate is
#       stubbed and the LAST copy stays verbatim (dedup);
#     * prose in assistant turns more than `keep_last` messages old — fenced
#       blocks survive that verbatim (distill_old_turns.py:52);
#     * the middle of an OLD tool result — kept as head + tail with a marker
#       saying what was dropped (`[slimtoken-compressed] N B -> M B`), and only
#       for results older than the last `keep_last` messages (tool_compress).
#   So: this mode DOES shorten old file reads. What it guarantees is that the
#   evidence you just fetched, and the words you just typed, reach the model
#   unaltered — not that nothing is ever abbreviated.
#   The `tools` stage is deliberately OFF here: it rewrites tool schemas
#   (strips `title`/`examples`/`$comment`), which changes how a model fills in
#   arguments, so a tool-calling agent must opt into it, not inherit it.
#
# MODE_REALTIME — for TALKING to a model, not for working with one. No code, no
#   file reads, no tool contracts; the goal is the shortest prompt that still
#   reads as the conversation. User turns are elided as well as assistant turns
#   (`distill_include_user`) and prose is cut to `distill_max_chars` characters;
#   every old tool result is skeletonised (`keep_last=2`, not 4).
#   Do NOT run an agent session in this mode. An elided instruction still reads
#   as a complete instruction and a stubbed file read still reads as an empty
#   file, so the model acts confidently on evidence that is no longer there.
#   Measured, not assumed: on an 8-turn agent session (8 distinct real file
#   reads) code mode leaves the newest read byte-identical and the realtime mode
#   does NOT — `keep_last=2` is not enough to cover a read the model is still
#   waiting on, and a skeletonised read is what made the model re-run its own
#   tool call on 2026-09-27 01:34.
_MODE_CODE = "code"
_MODE_REALTIME = "realtime"

MODES: Dict[str, Dict] = {

    _MODE_CODE: {
        "stages": {"dedup", "distill"},
        "keep_last": 4,
        "distill_max_chars": 4096,
        "distill_include_user": False,
        "tool_compress": True,
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

# Why each mode is worth having, in the words the CLI prints. These are reasons,
# not marketing: the realtime entry says what it costs you.
MODE_NOTES: Dict[str, str] = {
    _MODE_CODE:
        "default. Your newest turns pass through verbatim — the file you just "
        "read and the instruction you just typed reach the model unaltered. What "
        "gets shortened is OLD material: a duplicate tool result, prose in an "
        "old assistant turn, and the middle of an old file read (marked with "
        "what was dropped). Tool schemas are left exactly as written.",
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
    """SLIMTOKEN_MODE, validated. An unknown name falls back to `code` — the
    safe direction — and says so once, because a typo that silently selects the
    lossy mode is the failure this whole layer exists to prevent."""
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
