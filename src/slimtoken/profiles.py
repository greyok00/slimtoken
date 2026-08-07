"""profiles — named aggressiveness presets over EXISTING MinifyConfig knobs.

This module introduces NO new optimization heuristics. It only selects values
for fields that already exist on :class:`slimtoken.pipeline.MinifyConfig`
(``enabled_stages``, ``token_budget``, ``keep_last``, ``distill_max_chars``,
``tool_compress``, ``tool_skip``). Two presets:

  - ``aggressive`` — the DEFAULT. All lossless stages plus distill of old
                     prose, a generous budget backstop, and lossy tool-result
                     compression ON. Trades a little fidelity for the most
                     context headroom. The recommended profile for general use.
  - ``safe``       — fully lossless: tools + system + messages + dedup. No
                     distillation, no hard budget prune, no lossy compression.
                     Nothing is ever truncated or dropped; only whitespace,
                     schema noise, and duplicate tool results are collapsed.
                     Use this when you need bit-for-bit fidelity (e.g. the model
                     must see exact tool output, or you're debugging the request).

Decoupled from :mod:`slimtoken.proxy` on purpose — this is the config surface for
the MCP server, the CLI ``optimize``/``presets`` subcommands, and the agent
skill. The proxy keeps its own env-derived :func:`build_minify_cfg` unchanged
(the proxy's losslessness is governed by ``SLIMTOKEN_TOOL_COMPRESS``, not by
profiles, so the proxy stays lossless-by-default regardless of which profile is
the default here).
"""
from __future__ import annotations

import os
from typing import Optional, Set

from .pipeline import MinifyConfig


# Stage sets (subsets of the existing pipeline stages — no new stages invented).
_LOSSLESS = {"tools", "system", "messages", "dedup"}
_AGGRESSIVE = {"tools", "system", "messages", "dedup", "distill"}

PROFILES = {
    "aggressive": {
        "enabled_stages": _AGGRESSIVE,
        "token_budget": 131072,      # general backstop (only catches bloat)
        "keep_last": 4,              # distill applies to more turns
        "distill_max_chars": 160,    # shorter summaries of old prose
        "tool_compress": True,      # lossy type-specific tool-result compression
    },
    "safe": {
        "enabled_stages": _LOSSLESS,
        "token_budget": 0,           # no hard prune
        "keep_last": 8,
        "distill_max_chars": 240,    # unused (distill off), kept for completeness
        "tool_compress": False,
    },
}


def _env_tool_skip() -> Set[str]:
    raw = os.environ.get("SLIMTOKEN_MINIFY_TOOL_SKIP", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def profile_names() -> list:
    return list(PROFILES.keys())


def profile_config(name: Optional[str] = "aggressive") -> MinifyConfig:
    """Build a :class:`MinifyConfig` for a named profile.

    ``name`` defaults to ``"aggressive"``. Unknown names fall back to
    ``"aggressive"`` (never raise — callers like the MCP server pass untrusted
    input). The ``SLIMTOKEN_MINIFY_TOOL_SKIP`` env var is honored if set
    (the one profile-relevant env knob; it's a per-tool whitelist, orthogonal
    to aggressiveness). Honors the master switch: ``SLIMTOKEN_MINIFY=0``
    returns a config with NO stages (passthrough).
    """
    if name not in PROFILES:
        name = "aggressive"
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
        "aggressive": "Default. All lossless stages + distill of old prose + generous budget backstop + lossy tool-result compression. Most context headroom.",
        "safe": "Lossless only: tools + system + messages + dedup. Nothing truncated or dropped. Use when you need exact fidelity.",
    }