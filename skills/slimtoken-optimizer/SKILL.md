---
name: slimtoken-optimizer
description: Shrink LLM prompts before sending them — collapse duplicate tool results, distill old turns, minify tool schemas and system prompts, and prune to a token budget. Lossless by default; lossy modes are opt-in. Works with any local or cloud model via the slimtoken CLI or MCP server.
---

# slimtoken-optimizer

Trim prompt tokens before a request goes out. Use this whenever a conversation has
grown long, tool results are large, or you're about to hit a context/token-budget
limit on a local or cloud model. Model-agnostic — it rewrites the request, not the
model.

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
slimtoken optimize --input request.json --profile safe        # lossless, prints stats to stderr
slimtoken optimize --input request.json --profile balanced     # + distill old turns
slimtoken optimize --input request.json --profile aggressive   # + lossy tool-result compression
slimtoken optimize -i request.json -p aggressive --max-input-tokens 8192   # also prune to a budget
# stdin works too:  cat request.json | slimtoken optimize -p balanced

# See recommended local-model configs + measured reduction by GPU VRAM tier
slimtoken presets --measure            # 4 / 8 / 16 / 24 GB tiers, real % drop
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

## Profiles (aggressiveness presets)
| profile | stages | lossy? | use when |
|---------|--------|-------|----------|
| `safe` | tools · system · messages · dedup | no | default; never loses information |
| `balanced` | + distill old turns | no | long chats; keeps recent N turns verbatim | 
| `aggressive` | + lossy tool-result compression | yes | tight VRAM/budget; compact tool output |

All stages are **pair-safe**: tool_use/tool_result pairs are never split or
reordered, and code fences are preserved.

## Rules
- Default to `safe`. Only escalate to `aggressive` when the user asks for max
  compression or is up against a hard limit.
- Never claim a reduction number — measure it (`slimtoken presets --measure` or
  the stats line from `optimize`). The software computes the real drop.
- This skill rewrites the request; it does not change tokenizer selection or model
  behavior.

See `references/optimization-policies.md` for the full stage list, pair-safety
rules, and what each lossy stage actually discards.