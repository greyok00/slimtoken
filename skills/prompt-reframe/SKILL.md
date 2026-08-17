---
name: prompt-reframe
description: Tighten user prompts before they reach a model — strip conversational filler, drop fragments, dedupe sentences, rank by relevance, and compose a short, declarative system prompt that doesn't waste context. CPU-only, deterministic, dependency-free. Use it whenever a request is long, rambling, or covered in pleasantries that obscure the actual ask, and especially before a model call in a script or batch pipeline.
---

# prompt-reframe

A pure-CPU toolkit for tightening natural-language prompts. No LLM
roundtrip; every stage is a deterministic transform that runs in
microseconds. Lives in `slimtoken.prompt_reframe` and is also exposed
as a standalone MCP server (`slimtoken-reframe-mcp`).

## When to use
- The user prompt is full of "can you basically just..." and repeats
  itself; the actual ask is buried.
- A batch job needs to fan out N requests with consistent shape; the
  rewriter gives every prompt the same terse form.
- A product embeds a model and the system prompt is bloated.
- You'd like to know the domain of a request (business / code / osint
  / cybersecurity / professional / general) before deciding which
  system prompt to attach.
- You're a small CPU environment (edge device, serverless cold start,
  batch worker) and can't afford to call a model just to paraphrase.

## When NOT to use
- The prompt is already short (< 80 words) and clean. The reframe is a
  no-op there; you'd just be paying CPU for nothing.
- The user wants the model to handle nuance, hedging, or
  conversational style. The rewriter strips style on purpose.
- You need a semantic rewrite that the input doesn't already contain.
  Use an LLM for that; this toolkit only works with sentences that
  already exist in the input.

## How to use

**Default — the Python API** (works anywhere slimtoken is installed):

```python
from slimtoken.prompt_reframe import (
    classify_domain, reframe_prompt, shrink_prompt,
    minify_prompt, build_system, frame_prompt,
)

domain = classify_domain(user_prompt)
tight = shrink_prompt(user_prompt, mode='balanced')   # ~50 words
system = build_system(domain, role='generalist', style='terse')

# Or one call for the full pipeline:
reframed, system, domain = frame_prompt(user_prompt, mode='balanced')
```

**Fallback — CLI** (when you just want to see the effect):

```bash
python -m slimtoken.prompt_reframe "your rambling prompt here"
python -m slimtoken.prompt_reframe smoke               # built-in tests
python -m slimtoken.prompt_reframe json "your prompt"   # machine-readable
```

**Fallback — MCP stdio** (host agent runs tools):

```
tools: slimtoken.reframe.{classify_domain, reframe, shrink,
       minify, build_system, frame}
```

Run the server with `python -m slimtoken.mcp_server.prompt_reframe_server`
(or use the `slimtoken-reframe-mcp` entry point if installed as a
script). The server is dependency-free; it imports `slimtoken` and
nothing else.

## Pipeline stages

| Stage | Function | What it does |
|-------|----------|--------------|
| 1 | `classify_domain` | Keyword match into one of six domains |
| 2 | `reframe_prompt` | Strip filler (30+ phrases), drop fragments, dedupe sentences, normalize whitespace |
| 3 | `shrink_prompt`  | TextRank-lite sentence rank; cap to word budget; preserve original order |
| 4 | `minify_prompt`  | Collapse whitespace, drop redundant punctuation runs |
| 5 | `build_system`   | Compose a tight declarative system prompt from a fixed schema |

Call them independently or use `frame_prompt` for the whole bundle.
Stages are *lossless on actionable content* — every factual claim
survives; only conversational scaffolding is removed.

## Tuning

- `mode='aggressive'` ≈ 20 words (~ caveman)
- `mode='balanced'`   ≈ 50 words (default; tight business prose)
- `mode='preserve'`   ≈ 150 words (light cleanup only)

Pass `max_tokens=N` to override the mode budget. Set `rules=` to inject
explicit constraints into the composed system prompt.

The default `max_tokens=None` lets the `mode` win. Pre-0.3.6 callers that
passed `max_tokens=80` implicitly bypassed `mode='balanced'` — that
footgun is gone: budgets now come from `mode` unless an explicit int is
passed.

## What this skill does NOT do

- It does NOT call an LLM. Output is built from sentences in the input.
- It does NOT translate. Input must be the language you want output in.
- It does NOT add facts the user didn't say. If a claim is missing,
  shrink / minify will not invent it.
- It does NOT pick a model. Pair it with whichever model you want
  downstream.

## See also

- `references/stages.md` — the full algorithm for each stage, with
  before/after examples.
- `scripts/reframe.py` — a one-shot CLI you can pipe into or run on
  stdin.
