"""config — the single env-driven MinifyConfig builder. No named profiles.

slimtoken always runs the full pipeline by default (tools · system · messages
· dedup · distill · tool_compress). Every stage and knob is a raw ``SLIMTOKEN_*``
env switch an expert can tune; ``SLIMTOKEN_MINIFY=0`` turns the whole pipeline
off (passthrough). There is no "profile" abstraction — the only dial is on/off,
plus per-stage and per-knob env overrides.

This is the ONE config surface shared by the proxy, the CLI ``optimize`` /
``presets`` subcommands, the MCP server, and the agent skill. The proxy used to
carry its own ``build_minify_cfg`` with lossless-by-default knobs; that dual
config surface is gone — everything calls :func:`build_config` here.
"""
from __future__ import annotations

import os
from typing import Set

from .pipeline import MinifyConfig

# Always-on default stage set — the full pipeline, in order.
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
    """The always-on aggressive config, tuned via ``SLIMTOKEN_*`` env knobs.

    Stages default ON; ``SLIMTOKEN_MINIFY_<STAGE>=0`` disables one stage
    (e.g. ``SLIMTOKEN_MINIFY_DISTILL=0``). ``SLIMTOKEN_MINIFY=0`` disables the
    whole pipeline (passthrough).

    The pipeline is lossless by default: minify, dedup, assistant-only distill,
    and the budget backstop never remove content the model still needs. LOSSY
    stages are opt-in — ``SLIMTOKEN_TOOL_COMPRESS=1`` (type-specific tool_result
    compression), ``SLIMTOKEN_MINIFY_DOM=1`` (HTML pruning). Old user turns are
    preserved by default; ``SLIMTOKEN_DISTILL_INCLUDE_USER=1`` opts into
    distilling them too.

    Defaults: budget 131072, keep_last 4, distill_max_chars 160, tool_compress OFF.
    """
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


# Backward-compat alias for the proxy + any external caller that imported the
# old name. ``build_minify_cfg`` and ``build_config`` are the same function.
build_minify_cfg = build_config