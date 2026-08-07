"""profiles — named aggressiveness presets over EXISTING MinifyConfig knobs.

This module introduces NO new optimization heuristics. It only selects values
for fields that already exist on :class:`slimtoken.pipeline.MinifyConfig`
(``enabled_stages``, ``token_budget``, ``keep_last``, ``distill_max_chars``,
``tool_compress``, ``tool_skip``). The three presets:

  - ``safe``       — fully lossless: tools + system + messages + dedup. No
                     distillation, no hard budget prune, no lossy compression.
                     Nothing is ever truncated or dropped; only whitespace,
                     schema noise, and duplicate tool results are collapsed.
  - ``balanced``   — the proxy's current default: all lossless stages plus
                     distill of old prose + a generous budget backstop. The
                     recommended profile for general use.
  - ``aggressive`` — smaller / constrained-VRAM models: tighter budget, fewer
                     kept turns, shorter distill, and lossy tool-result
                     compression ON. Trades fidelity for context headroom.

Decoupled from :mod:`slimtoken.proxy` on purpose — this is the config surface for
the MCP server, the CLI ``optimize``/``presets`` subcommands, and the agent
skill. The proxy keeps its own env-derived :func:`build_minify_cfg` unchanged.
"""
from __future__ import annotations

import os
from typing import Optional, Set

from .pipeline import MinifyConfig


# Stage sets (subsets of the existing pipeline stages — no new stages invented).
_LOSSLESS = {"tools", "system", "messages", "dedup"}
_BALANCED = {"tools", "system", "messages", "dedup", "distill"}
_AGGRESSIVE = {"tools", "system", "messages", "dedup", "distill"}

PROFILES = {
    "safe": {
        "enabled_stages": _LOSSLESS,
        "token_budget": 0,           # no hard prune
        "keep_last": 8,
        "distill_max_chars": 240,    # unused (distill off), kept for completeness
        "tool_compress": False,
    },
    "balanced": {
        "enabled_stages": _BALANCED,
        "token_budget": 131072,      # generous backstop (only catches bloat)
        "keep_last": 8,
        "distill_max_chars": 240,
        "tool_compress": False,
    },
    "aggressive": {
        "enabled_stages": _AGGRESSIVE,
        "token_budget": 32768,       # tighter — prune harder on small models
        "keep_last": 4,              # distill applies to more turns
        "distill_max_chars": 160,    # shorter summaries of old prose
        "tool_compress": True,      # lossy type-specific tool-result compression
    },
}


def _env_tool_skip() -> Set[str]:
    raw = os.environ.get("SLIMTOKEN_MINIFY_TOOL_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def profile_names() -> list:
    return list(PROFILES.keys())


def profile_config(name: Optional[str] = "balanced") -> MinifyConfig:
    """Build a :class:`MinifyConfig` for a named profile.

    ``name`` defaults to ``"balanced"``. Unknown names fall back to
    ``"balanced"`` (never raise — callers like the MCP server pass untrusted
    input). The ``SLIMTOKEN_MINIFY_TOOL_SKIP`` env var is honored if set
    (the one profile-relevant env knob; it's a per-tool whitelist, orthogonal
    to aggressiveness). Honors the master switch: ``SLIMTOKEN_MINIFY=0``
    returns a config with NO stages (passthrough).
    """
    if name not in PROFILES:
        name = "balanced"
    if os.environ.get("SLIMTOKEN_MINIFY", "1").strip().lower() in ("0", "false", "no", "off"):
        return MinifyConfig(enabled_stages=set(), tool_skip=_env_tool_skip())
    p = PROFILES[name]
    return MinifyConfig(
        token_budget=p["token_budget"],
        enabled_stages=set(p["enabled_stages"]),
        tool_skip=_env_tool_skip(),
        keep_last=p["keep_last"],
        dedup_min_chars=int(os.environ.get("SLIMTOKEN_DEDUP_MIN_CHARS", "200")),
        distill_max_chars=p["distill_max_chars"],
        tool_compress=p["tool_compress"],
    )


def profile_doc() -> dict:
    """Static description of each profile (for get_config / docs)."""
    return {
        "safe": "Lossless only: tools + system + messages + dedup. Nothing truncated or dropped.",
        "balanced": "All lossless + distill of old prose + generous budget backstop. Default.",
        "aggressive": "Tighter budget, fewer kept turns, shorter distill, lossy tool-result compression. For small/constrained-VRAM models.",
    }