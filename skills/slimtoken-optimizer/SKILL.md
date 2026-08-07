---
name: slimtoken-optimizer
description: Shrink LLM prompts before sending them — collapse duplicate tool results, distill old turns, minify tool schemas and system prompts, and prune to a token budget. Lossy by default (distill + tool-result compression) for the most context headroom; disable any stage via the SLIMTOKEN_* env knobs. Works with any local or cloud model via the slimtoken CLI or MCP server.
---

# slimtoken-optimizer

Trim prompt tokens before a request goes out. Use this whenever a conversation has
grown long, tool results are large, or you're about to hit a context/token-budget
limit on a local or cloud model. Model-agnostic — it rewrites the request, not
the model.

## When to use
- A tool returned a large result (file dump, directory listing, log, JSON) and the
  same content appears more than once across turns.
- The transcript is long and older turns are now low-value.
- You're close to a model's context limit and need headroom cheaply.
- You want to know how many tokens a request actually costs before sending.

## How to use

**Primary — CLI** (no server needed, works offline):

```bash
# Count tokens in a request (cl100k, approximate for non-cl100k models)
slimtoken optimize --input request.json                 # always-on: full pipeline, most headroom
slimtoken optimize -i request.json --max-input-tokens 8192   # also prune to a budget
# stdin works too:  cat request.json | slimtoken optimize

# See recommended local-model configs + measured reduction by GPU VRAM tier
slimtoken presets --measure            # 4 / 8 / 16 GB tiers, real % drop
slimtoken presets --vram-gb 8 --measure
```

**Fallback — MCP stdio** (when the host agent runtime speaks MCP and you want a
persistent tool surface):

```
tools: slimtoken.optimize_messages, slimtoken.estimate_tokens,
       slimtoken.prune_context, slimtoken.minify_tool_result,
       slimtoken.inspect_budget, slimtoken.get_config,
       slimtoken.list_model_presets
```
Run the server with `slimtoken-mcp` (or `python -m slimtoken.mcp_server`) and point
your MCP client at it over stdio. The MCP tools call the same core pipeline as the
CLI — nothing is reimplemented.

## One config, no profiles

There are no named profiles. slimtoken always runs the full pipeline by default —
tools · system · messages · dedup · distill · tool-result compression. Every stage
is a raw `SLIMTOKEN_*` env switch; there is no `--profile` flag.

- Turn the whole thing off: `SLIMTOKEN_MINIFY=0` (raw passthrough).
- Turn off one lossy stage: e.g. `SLIMTOKEN_MINIFY_DISTILL=0` (keep old turns
  verbatim) or `SLIMTOKEN_TOOL_COMPRESS=0` (keep tool results verbatim).

All stages are **pair-safe**: tool_use/tool_result pairs are never split or
reordered, and code fences are preserved.

## Rules
- The default is lossy — it trades a little fidelity (distilled old turns,
  compressed tool results) for the most context headroom. Reach for
  `SLIMTOKEN_MINIFY=0` or a per-stage kill-switch only when the user needs exact
  fidelity (e.g. debugging, or the model must see raw tool output verbatim).
- Never claim a reduction number — measure it (`slimtoken presets --measure` or
  the stats line from `optimize`). The software computes the real drop.
- This skill rewrites the request; it does not change tokenizer selection or model
  behavior.

See `references/optimization-policies.md` for the full stage list, pair-safety
rules, and what each lossy stage actually discards.