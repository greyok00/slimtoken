"""model_presets — default local-model configs by VRAM tier + measured reduction.

A curated table of common local models grouped by GPU VRAM tier, each with a
recommended slimtoken aggressiveness profile and usable context. The reduction
numbers are NOT hand-waved: :func:`measure_reduction` runs the EXISTING pipeline
(:func:`slimtoken.pipeline.minify_request`) on a representative payload with
the profile's :class:`MinifyConfig` and reports the real token drop. So the
"how much slimtoken optimizes this model" figure is computed by the software
itself, not asserted.

This module adds NO new optimization heuristics. It is a data table + a thin
measurement helper that calls the existing pipeline and tokenizer.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .pipeline import minify_request
from .profiles import profile_config
from .tokencount import count_obj


# ── VRAM-tier model table ────────────────────────────────────────────────────
# tier: (label, min_gb, max_gb). Each model: name, quant, context (usable, not
# nominal — KV cache + overhead eat into the advertised max), profile, notes.
MODEL_PRESETS: List[Dict] = [
    {"vram_gb": 4, "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "context": 8192, "profile": "aggressive", "notes": "best all-rounder at 4GB; low latency"},
    {"vram_gb": 4, "model": "Qwen 2.5 3B", "quant": "Q4_K_M",
     "context": 32768, "profile": "aggressive", "notes": "stronger reasoning than Llama 3B at this size"},
    {"vram_gb": 4, "model": "Phi-4 Mini", "quant": "Q4_0",
     "context": 16384, "profile": "aggressive", "notes": "fast; tight context on 4GB"},

    {"vram_gb": 8, "model": "LFM2.5-8B-A1B (MoE, 1.5B active)", "quant": "Q4",
     "context": 32768, "profile": "balanced", "notes": "~5GB at Q4; real context headroom on 8GB"},
    {"vram_gb": 8, "model": "Qwen 2.5 7B", "quant": "Q4_K_M",
     "context": 32768, "profile": "balanced", "notes": "solid general-purpose 7B"},
    {"vram_gb": 8, "model": "Gemma 3 12B", "quant": "Q4",
     "context": 16384, "profile": "aggressive", "notes": "borderline fit on 8GB; drop context if OOM"},

    {"vram_gb": 16, "model": "Qwen 3 14B", "quant": "Q4_K_M",
     "context": 65536, "profile": "balanced", "notes": "good quality/size balance on 16GB"},
    {"vram_gb": 16, "model": "Mistral Nemo 12B", "quant": "Q4_K_M",
     "context": 131072, "profile": "balanced", "notes": "large native context; long-context workloads"},
    {"vram_gb": 16, "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "context": 131072, "profile": "balanced", "notes": "fast; lots of context headroom on 16GB"},
]


def list_presets(vram_gb: Optional[int] = None) -> List[Dict]:
    """Return preset rows, optionally filtered to an exact VRAM tier."""
    if vram_gb is None:
        return [dict(r) for r in MODEL_PRESETS]
    return [dict(r) for r in MODEL_PRESETS if r["vram_gb"] == vram_gb]


def presets_by_tier() -> Dict[int, List[Dict]]:
    """Group presets by VRAM tier (4, 8, 16)."""
    out: Dict[int, List[Dict]] = {}
    for r in MODEL_PRESETS:
        out.setdefault(r["vram_gb"], []).append(dict(r))
    return out


# ── representative payloads (test DATA, not heuristics) ──────────────────────
# A "typical" payload: a handful of turns with some blank-line bloat + tool
# schemas. A "bloated" payload: the realistic worst case the proxy targets —
# the same big file re-read every turn (dedup) + verbose old prose (distill).
_VERBOSE_SYS = ("<cold_memory>\nYou are a senior engineer. Follow conventions.\n"
                "Never leak personal info.\n</cold_memory>\n\n\n\n"
                "Be concise. Use tables when comparing.\n")


def _tool(i: int) -> dict:
    return {"name": "Tool_%d" % i,
            "description": ("Use this tool to perform operation %d on the local "
                            "filesystem and return its full result.\n\n"
                            "```bash\ntool_%d /tmp/example.txt\n```\n\nMore detail."
                            % (i, i)) + " " * 120,
            "input_schema": {"type": "object", "title": "S%d" % i, "$comment": "x",
                             "required": ["path"],
                             "properties": {"path": {"type": "string",
                                                    "examples": ["/a", "/b"]}}}}


def _payload_typical() -> dict:
    msgs = []
    for i in range(6):
        msgs.append({"role": "user", "content": "question %d\n\n\n\nmore detail" % i})
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                       "name": "Tool_%d" % (i % 3),
                                                       "input": {"path": "/f%d" % i}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu%d" % i,
                                                  "content": "result %d\n\n\n\nblank\n\n\nlines" % i}]})
        msgs.append({"role": "assistant", "content": "answer %d\n\n\n\nexplanation" % i})
    msgs.append({"role": "user", "content": "now do the final task\n\n\n\nplease proceed"})
    return {"system": _VERBOSE_SYS, "tools": [_tool(i) for i in range(3)], "messages": msgs}


def _payload_bloated() -> dict:
    big_file = "".join("line %d: implementation detail here\n" % i for i in range(400))
    long_explain = ("Let me walk through my reasoning in detail. I considered several "
                    "approaches and chose this one due to the constraints. " * 25)
    msgs = []
    for i in range(8):
        msgs.append({"role": "user", "content": "please read and fix the file"})
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                       "name": "Tool_%d" % (i % 3),
                                                       "input": {"path": "/x.py"}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu%d" % i,
                                                  "content": big_file}]})
        msgs.append({"role": "assistant", "content": long_explain})
    msgs.append({"role": "user", "content": "now finalize"})
    return {"system": _VERBOSE_SYS, "tools": [_tool(i) for i in range(3)], "messages": msgs}


_PAYLOADS = {"typical": _payload_typical, "bloated": _payload_bloated}


def measure_reduction(profile: str = "balanced", size: str = "bloated") -> Dict:
    """Run the existing pipeline on a representative payload with a profile.

    Returns {"profile", "size", "tokens_in", "tokens_out", "reduction_pct",
    "stages"}. Uses the real cl100k tokenizer via :func:`count_obj` (no
    whole-body serialize). No new heuristics — just measurement.
    """
    import copy
    if size not in _PAYLOADS:
        size = "bloated"
    body = _PAYLOADS[size]()
    cfg = profile_config(profile)
    tin = count_obj(body)
    out, stats = minify_request(copy.deepcopy(body), cfg)
    tout = count_obj(out)
    pct = round(100 * (tin - tout) / tin, 1) if tin else 0.0
    return {"profile": profile, "size": size,
            "tokens_in": tin, "tokens_out": tout, "reduction_pct": pct,
            "stages": sorted(cfg.enabled_stages),
            "errors": list(stats.errors) if stats.errors else []}


def preset_with_reduction(vram_gb: Optional[int] = None) -> List[Dict]:
    """Preset rows enriched with the live measured reduction for their profile
    on a bloated payload (the case the proxy is built for)."""
    cache: Dict[str, Dict] = {}
    rows = []
    for r in list_presets(vram_gb):
        prof = r["profile"]
        if prof not in cache:
            cache[prof] = measure_reduction(prof, "bloated")
        rr = dict(r)
        rr["reduction_pct_bloated"] = cache[prof]["reduction_pct"]
        rr["tokens_in_bloated"] = cache[prof]["tokens_in"]
        rr["tokens_out_bloated"] = cache[prof]["tokens_out"]
        rows.append(rr)
    return rows