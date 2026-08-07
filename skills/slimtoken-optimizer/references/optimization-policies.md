# Optimization policies

The exact stages slimtoken applies, in pipeline order, and what each one
discards. All stages are **pair-safe**: a tool_use and its tool_result are never
separated or reordered, so tool-call validity is preserved end-to-end.

## Pipeline order (existing, frozen)

1. **tools** — minify tool schemas. Strips redundant whitespace, collapses
   verbose descriptions, trims unused JSON-schema fields (`$comment`, `examples`,
   `title`) while keeping the schema valid. **Lossless** — the tool still behaves
   identically.
2. **system** — minify the system prompt. Collapses runs of blank lines, trims
   trailing whitespace per line, preserves `<cold_memory>`/`<recent_context>`
   block boundaries and code fences. **Lossless.**
3. **messages** — minify message content. Fence-aware whitespace collapse inside
   text blocks; does not touch tool_use/tool_result structure. **Lossless.**
4. **dedup** — collapse duplicate tool results. When the same large tool_result
   content appears more than once (e.g. a file re-read every turn), all but the
   last occurrence are replaced with a short stub
   `[slimtoken: identical to a later tool_result; omitted N chars]`. **Lossless**
   in practice — the latest copy is always kept verbatim; only stale duplicates
   are stubbed.
5. **distill** (balanced/aggressive only) — summarize old turns. Turns older than
  `keep_last` (default 8 balanced, 4 aggressive) are replaced with a short
  distilled summary. The most recent `keep_last` turns are always kept verbatim.
  **Lossy for old turns only** — recent context is untouched.
6. **tool_compress** (aggressive only, opt-in via `SLIMTOKEN_TOOL_COMPRESS=1` or
  the `aggressive` profile) — type-specific reduction of large tool_result
  content: directory listings, git output, logs, JSON, source. Emits a compact
  representation plus a `[slimtoken-compressed]` metadata header. **Lossy.**
7. **budget** — prune to a token budget. When set (`token_budget` /
  `--max-input-tokens`), drops leading messages pair-safely (never breaking a
  tool_use/result pair) until under budget. Uses a real cl100k token count and
  incremental prefix sums (O(1) per candidate — no re-serialization).

## Profiles → stages enabled

| profile | enabled stages | token_budget | keep_last | distill_max_chars | tool_compress |
|---------|----------------|--------------|-----------|------------------|---------------|
| `safe` | tools, system, messages, dedup | 0 (off) | 8 | 240 | off |
| `balanced` | + distill | 131072 | 8 | 240 | off |
| `aggressive` | + distill | 32768 | 4 | 160 | on |

`token_budget=0` means budget pruning is off; the field is still present in the
config but `enforce_budget` is a no-op when the budget is 0.

## Env overrides (proxy config; do not change behavior when unset)
- `SLIMTOKEN_MINIFY=0` → disable the whole pipeline (fast-path passthrough).
- `SLIMTOKEN_MINIFY_TOOL_SKIP=Read,LS` → skip named tools (case-insensitive,
  prefix match) so critical tools are never minified.
- `SLIMTOKEN_TOOL_COMPRESS=1` → enable lossy tool-result compression independent
  of profile.
- `SLIMTOKEN_MAX_TOKENS=N` / `SLIMTOKEN_STOP=...` → output-side filtering on the
  proxy (cap/truncate streamed completions). Off when unset.

## Token counting
Counts use the real **cl100k_base** tokenizer (bundled with slimtoken, runs
offline), cached by content hash with an LRU. `count_obj` sums per-message
cached counts — it **never serializes the whole request body** just to take a
length. The count is exact for cl100k models (Claude, GPT) and approximate for
others (Llama, Qwen, Gemma) — use it for budgeting, not billing.

## What this skill does NOT do
- Does not pick or switch tokenizers.
- Does not add new optimization heuristics.
- Does not change model behavior or output.
- Does not touch the host agent's own config — it only rewrites the request body.