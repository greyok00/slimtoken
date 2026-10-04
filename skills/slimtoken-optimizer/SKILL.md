---
name: slimtoken-optimizer
description: Shrink LLM prompts before sending them — collapse duplicate tool results, distill old turns, minify tool schemas and system prompts, prune to a token budget, and (opt-in) prune large HTML tool results. On the output side, the proxy can cap/truncate streamed completions and strip lead-in filler. The default `code` mode rewrites no tool result at all; the `realtime` mode is the lossy one. Any stage can be switched via the SLIMTOKEN_* env knobs. Works with any local or cloud model via the slimtoken CLI or MCP server.
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

**Default — the proxy.** slimtoken runs as a proxy in front of the model API
(`slimtoken serve --upstream <model>`). Every request routed through it is
minified automatically — **you don't need to call anything**. If the proxy is in
the path, the work is already done. Do not re-minify a request that already went
through the proxy.

**Fallback 1 — CLI** (when the proxy isn't in the path; no server needed):

```bash
# Count tokens in a request (cl100k, approximate for non-cl100k models)
slimtoken optimize --input request.json                 # default mode: dedup + distill, no tool result rewritten
slimtoken optimize -i request.json --max-input-tokens 8192   # also prune to a budget
# stdin works too:  cat request.json | slimtoken optimize

# See recommended local-model configs + measured reduction by GPU VRAM tier
slimtoken presets --measure            # 4 / 8 / 16 GB tiers, real % drop
slimtoken presets --vram-gb 8 --measure
```

**Fallback 2 — MCP stdio** (when the host agent runtime speaks MCP and you want a
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

## Two modes, plus raw switches

`SLIMTOKEN_MODE` picks one of two modes. Every stage is also a raw `SLIMTOKEN_*`
switch, and a switch you set explicitly overrides the mode for that one knob.

- **`code`** (default) — dedup + distill only. **No tool result is ever rewritten,
  old or new**, and tool schemas are untouched. Use it for agent work.
- **`realtime`** — every stage on, including the one that rewrites old tool
  results and the one that strips tool-schema fields, with prose cut to 160 chars.
  Lossy on purpose, for conversation. Do not run agent work in it.
- `slimtoken modes` lists them; `slimtoken modes --measure` prints what each one
  actually saves.

- Turn the whole thing off: `SLIMTOKEN_MINIFY=0` (raw passthrough).
- Turn off one stage: e.g. `SLIMTOKEN_MINIFY_DISTILL=0` (keep old turns
  verbatim). `SLIMTOKEN_TOOL_COMPRESS=1` turns **on** the one stage that rewrites
  old tool results — it is off by default in `code`.
- Turn on the opt-in DOM stage: `SLIMTOKEN_MINIFY_DOM=1` (prune large HTML
  tool_results).
- Output side: `SLIMTOKEN_FILLER` strips lead-in filler ("Sure!",
  "Here is the code:") from the streamed response head — **on by default**,
  `=0` to disable; `SLIMTOKEN_STATS_FILE` persists cumulative minify stats.

All stages are **pair-safe**: tool_use/tool_result pairs are never split or
reordered, and code fences are preserved.

## Rules
- The default mode is not lossy on tool results — it never rewrites one. What it
  removes is a byte-identical duplicate (lossless: those bytes are still later in
  the conversation) and prose in old assistant turns. Reach for
  `SLIMTOKEN_MINIFY=0` when the user needs the request forwarded untouched.
- Never claim a reduction number — measure it (`slimtoken presets --measure` or
  the stats line from `optimize`). The software computes the real drop.
- This skill rewrites the request; it does not change tokenizer selection or model
  behavior.

See `references/optimization-policies.md` for the full stage list, pair-safety
rules, and what each lossy stage actually discards.