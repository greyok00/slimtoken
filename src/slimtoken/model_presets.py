
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .pipeline import minify_request
from .profiles import build_config
from .tokencount import count_obj





MODEL_PRESETS: List[Dict] = [
    {"vram_gb": 4, "model": "Llama 3.2 3B", "quant": "Q4_K_M",
     "context": 8192, "notes": "best all-rounder at 4GB; low latency"},
    {"vram_gb": 4, "model": "Qwen 2.5 3B", "quant": "Q4_K_M",
     "context": 32768, "notes": "stronger reasoning than Llama 3B at this size"},
    {"vram_gb": 4, "model": "Phi-4 Mini", "quant": "Q4_0",
     "context": 16384, "notes": "fast; tight context on 4GB"},

    {"vram_gb": 8, "model": "LFM2.5-8B-A1B (MoE, 1.5B active)", "quant": "Q4",
     "context": 32768, "notes": "~5GB at Q4; real context headroom on 8GB"},
    {"vram_gb": 8, "model": "Qwen 2.5 7B", "quant": "Q4_K_M",
     "context": 32768, "notes": "solid general-purpose 7B"},
    {"vram_gb": 8, "model": "Gemma 3 12B", "quant": "Q4",
     "context": 16384, "notes": "borderline fit on 8GB; drop context if OOM"},

    {"vram_gb": 16, "model": "Qwen 3 14B", "quant": "Q4_K_M",
     "context": 65536, "notes": "good quality/size balance on 16GB"},
    {"vram_gb": 16, "model": "Mistral Nemo 12B", "quant": "Q4_K_M",
     "context": 131072, "notes": "large native context; long-context workloads"},
    {"vram_gb": 16, "model": "Llama 3.1 8B", "quant": "Q4_K_M",
     "context": 131072, "notes": "fast; lots of context headroom on 16GB"},
]


def list_presets(vram_gb: Optional[int] = None) -> List[Dict]:

    if vram_gb is None:
        return [dict(r) for r in MODEL_PRESETS]
    return [dict(r) for r in MODEL_PRESETS if r["vram_gb"] == vram_gb]


def presets_by_tier() -> Dict[int, List[Dict]]:

    out: Dict[int, List[Dict]] = {}
    for r in MODEL_PRESETS:
        out.setdefault(r["vram_gb"], []).append(dict(r))
    return out






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


_FALLBACK_SRC = '''\
def build_config() -> MinifyConfig:
    """Fallback sample: real source files are used when the repo is present."""
    stages = {s for s in _DEFAULT_STAGES if _bool(f"SLIMTOKEN_MINIFY_{s.upper()}", True)}
    return MinifyConfig(
        token_budget=_int("SLIMTOKEN_MINIFY_BUDGET", 131072),
        enabled_stages=stages,
        keep_last=_int("SLIMTOKEN_KEEP_LAST", 4),
    )


class Compressor:
    def __init__(self, keep_last=0):
        self.keep_last = keep_last
        self.count = 0

    def compress(self, messages):
        cutoff = len(messages) - self.keep_last
        out = []
        for i, msg in enumerate(messages):
            if i >= cutoff or msg.get("role") != "user":
                out.append(msg)
                continue
            for block in msg.get("content", []):
                if block.get("type") == "tool_result":
                    block["content"] = self._skeleton(block["content"])
                    self.count += 1
            out.append(msg)
        return out
'''


def _session_sources(count: int) -> list:
    """`count` blocks of real source, one per turn.

    Real files, not a synthetic 'line N: detail here' body: every reducer in
    tool_result_compress.py inspects the text for the shape of what it is
    compressing, and placeholder text matches none of them, so a fabricated
    fixture measures the reducers' *rejection* path and reports ~0% for a
    pipeline that in fact removes most of a real file read.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = []
    for p in sorted((root / "src" / "slimtoken").glob("*.py")):
        try:
            out.append((p.name, p.read_text()))
        except OSError:
            continue
    if not out:
        return [(f"sample_{i}.py", _FALLBACK_SRC) for i in range(count)]
    while len(out) < count:
        out = out + [(n + "_b", t) for n, t in out]
    return out


def _payload_session() -> dict:
    """An agent session: eight turns, a DIFFERENT real file read on each.

    Distinct bodies on purpose. The `bloated` fixture reads the same file every
    turn, so it measures dedup and nothing else; a real session reads different
    files. Under the default `code` mode every one of those distinct reads is
    preserved, so this fixture reports 0% — that is the honest shape of the
    default, not a failure of a stage.
    """
    srcs = _session_sources(8)
    msgs = []
    for i in range(8):
        name, text = srcs[i]
        msgs.append({"role": "user", "content": "read %s and tell me what it does" % name})
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": "tu%d" % i, "name": "Read",
                                  "input": {"file_path": "/repo/src/slimtoken/" + name}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": "tu%d" % i,
                                  "content": text}]})
        msgs.append({"role": "assistant",
                     "content": "The file loads the config and wires the stages together."})
    msgs.append({"role": "user", "content": "now change the distill default and run the tests"})
    return {"system": _VERBOSE_SYS,
            "tools": [{"name": "Read", "description": "Read a file from disk." + " " * 120,
                       "input_schema": {"type": "object", "title": "R", "$comment": "x",
                                        "required": ["file_path"],
                                        "properties": {"file_path": {"type": "string",
                                                                     "examples": ["/a"],
                                                                     "description": "abs path"}}}}],
            "messages": msgs}


def _payload_voice() -> dict:
    """A spoken conversation: no tools, no code, no file reads.

    This is the shape SLIMTOKEN_MODE=realtime exists for and the shape the code
    mode is NOT measured on, so without it any claim about the realtime mode
    would be a claim about the wrong body. Turns are deliberately ordinary
    speech — the elision a lossy mode buys you is proportional to how much
    prose a turn carries, so a fixture of one-line utterances would understate
    it and a fixture of essays would flatter it.
    """
    turns = [
        ("hey can you walk me through what happened with that eviction thing "
         "again, i keep losing track of which deadline actually matters and "
         "which one is just a thing the landlord said", "Sure."),
        ("ok so the notice period is what, five days from delivery, and "
         "delivery counts from when they posted it not when i found it", "Yes."),
        ("and if i file the answer before that runs out then the five day thing "
         "stops being the thing that matters and it becomes the hearing date",
         "Correct."),
        ("right, so the practical move is to file the answer and then argue the "
         "rest at the hearing rather than trying to win it in the paperwork",
         "That is the usual approach."),
        ("what about the deposit, is that a separate claim or does it fold into "
         "the same case, because i do not want to lose the chance to raise it",
         "Separate."),
        ("can you say that again but shorter", "It is a separate claim."),
    ]
    msgs = []
    for i, (u, a) in enumerate(turns):
        msgs.append({"role": "user", "content": u + " " + u})
        msgs.append({"role": "assistant",
                     "content": ("Let me think about that for a moment. " + a + " "
                                 "Here is why, in a bit more detail than you asked "
                                 "for: the rule is the rule, and the exception you "
                                 "are hoping for is narrower than the rule. ") * 6})
    msgs.append({"role": "user", "content": "ok thanks"})
    return {"system": _VERBOSE_SYS, "messages": msgs}


_PAYLOADS = {"typical": _payload_typical, "bloated": _payload_bloated,
             "session": _payload_session, "voice": _payload_voice}


def measure_reduction(size: str = "bloated", mode: Optional[str] = None) -> Dict:
    """Reduction for one payload, optionally measured AS a named mode.

    `mode` sets SLIMTOKEN_MODE for the duration of the call rather than calling
    build_config with an argument, because the env var is how the proxy and the
    unit files actually select a mode — measuring any other path would measure
    something the owner cannot reproduce.
    """
    import copy
    if size not in _PAYLOADS:
        size = "bloated"
    saved = os.environ.get("SLIMTOKEN_MODE")
    if mode:
        os.environ["SLIMTOKEN_MODE"] = mode
    try:
        body = _PAYLOADS[size]()
        cfg = build_config()
        from .profiles import current_mode
        # read the mode while the env var is still in place — after the finally
        # below it is back to whatever the caller had
        mode_name = current_mode()
        tin = count_obj(body)
        out, stats = minify_request(copy.deepcopy(body), cfg)
        tout = count_obj(out)
    finally:
        if mode:
            if saved is None:
                os.environ.pop("SLIMTOKEN_MODE", None)
            else:
                os.environ["SLIMTOKEN_MODE"] = saved
    pct = round(100 * (tin - tout) / tin, 1) if tin else 0.0
    return {"size": size,
            "mode": mode_name,
            "tokens_in": tin, "tokens_out": tout, "reduction_pct": pct,
            "stages": sorted(cfg.enabled_stages),
            "errors": list(stats.errors) if stats.errors else []}


def preset_with_reduction(vram_gb: Optional[int] = None) -> List[Dict]:

    m = measure_reduction("bloated")
    rows = []
    for r in list_presets(vram_gb):
        rr = dict(r)
        rr["reduction_pct_bloated"] = m["reduction_pct"]
        rr["tokens_in_bloated"] = m["tokens_in"]
        rr["tokens_out_bloated"] = m["tokens_out"]
        rows.append(rr)
    return rows