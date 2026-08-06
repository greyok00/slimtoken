# slimtoken

A token-optimization proxy that sits between an Anthropic-compatible client and
its backend (local llama-server or a cloud API). It minifies tool schemas, the
system prompt, and message history, collapses duplicate tool results, and
distills old turns before forwarding the request. Fewer input tokens means
faster prompt-eval and lower cost.

Point `ANTHROPIC_BASE_URL` at it. MIT-licensed, pure stdlib, no runtime
dependencies. All optimizations are on by default.

## Install

```bash
bash scripts/install.sh                       # pure Python; wires ANTHROPIC_BASE_URL
slimtoken serve --upstream http://127.0.0.1:8082     # or https://api.anthropic.com
```

Uninstall:

```bash
bash scripts/uninstall.sh                     # or: slimtoken uninstall
```

Install only writes a marker block to your shell rc that exports
`ANTHROPIC_BASE_URL` (prior value backed up to `~/.slimtoken/prev_env` and
restored on uninstall). It does not touch `settings.json`, `CLAUDE.md`, or
`mcp.json`.

## Features

All on by default. There are no opt-in flags — only opt-out env vars (see
Config).

| Stage | Effect |
|-------|--------|
| tools | Drop `$comment`/`title`/`examples` from schemas; keep `name`/`required`/`enum`/`type`/structure. Compress `description` (keep first fenced example). |
| system | Collapse whitespace and duplicate banner lines outside code fences; preserve `<tag>` markers and fenced code byte-for-byte. |
| messages | Collapse blank-line runs and trailing whitespace in text blocks; pass `tool_use`/`tool_result`/`image` blocks through untouched. |
| dedup | Collapse repeated `tool_result` contents (SHA-256 keyed); latest kept verbatim, older copies stubbed. Threshold: `SLIMTOKEN_DEDUP_MIN_CHARS` (200). |
| distill | Truncate old assistant prose beyond the last `SLIMTOKEN_KEEP_LAST` (8) turns to `SLIMTOKEN_DISTILL_MAX_CHARS` (240) chars per turn. Fence-aware; preserves tool blocks; no model call. |
| budget | Hard token cap (`SLIMTOKEN_MINIFY_BUDGET`, 131072); drops a leading prefix of messages, pair-safe, only when over budget. |

Other behavior:

- Code fences (` ``` ` / `~~~`) are preserved byte-identical; malformed fences are over-preserved rather than risk corruption.
- Pruning is pair-safe: a `tool_result` is never orphaned from its `tool_use`.
- `grammar` field is stripped from request bodies.
- `GET /metrics` returns per-stage token savings and counts.
- TLS for cloud HTTPS upstreams (SNI; optional mTLS via `SLIMTOKEN_TLS_*`; `SLIMTOKEN_TLS_INSECURE=1` to skip verify).
- Identity-based change detection: unchanged content is returned zero-copy, so the pipeline adds no overhead when there is nothing to shrink.
- Lazy MCP: one stub tool per configured MCP server in `~/.slimtoken/lazy_mcp.json`; the real server is spawned on call. Empty/absent config = no-op.

## Config

Defaults are the recommended values. Set any to `0` to disable.

| Env var | Default | Meaning |
|---------|---------|---------|
| `SLIMTOKEN_MINIFY` | 1 | master switch; 0 = passthrough |
| `SLIMTOKEN_MINIFY_TOOLS` | 1 | |
| `SLIMTOKEN_MINIFY_SYSTEM` | 1 | |
| `SLIMTOKEN_MINIFY_MESSAGES` | 1 | |
| `SLIMTOKEN_MINIFY_DEDUP` | 1 | |
| `SLIMTOKEN_MINIFY_DISTILL` | 1 | |
| `SLIMTOKEN_MINIFY_BUDGET` | 131072 | 0 disables hard prune (distill still runs) |
| `SLIMTOKEN_KEEP_LAST` | 8 | recent turns kept verbatim by distill/budget |
| `SLIMTOKEN_DEDUP_MIN_CHARS` | 200 | only dedup tool results at least this long |
| `SLIMTOKEN_DISTILL_MAX_CHARS` | 240 | max chars per distilled old turn |
| `SLIMTOKEN_MINIFY_TOOL_SKIP` | _(none)_ | comma-list of tool names to never minify |
| `SLIMTOKEN_PORT` | 8181 | listen port |
| `SLIMTOKEN_UPSTREAM` | _(required to serve)_ | backend URL |

## Optional

```bash
slimtoken config-optimizer          # print recommended llama-server args for your VRAM + model
```

`config-optimizer` inspects GPU VRAM and model size and recommends `--ctx`,
`--ubatch`, `-ngl`, and cache sizes that fit without OOMing. It prints the
command and `CORTEXAGENT_*` exports; it does not change anything itself.

## Benchmarks

```bash
python3 bench/benchmark.py                                  # payload, per-stage, overhead
python3 bench/benchmark.py --backend http://127.0.0.1:8082 # + end-to-end
```

Payload reduction (default config, est. tokens at ~4 chars/token):

| payload | raw | default | saved |
|---------|----:|--------:|------:|
| small   | 1,069 | 935 | 12.5% |
| medium  | 2,590 | 2,196 | 15.2% |
| large   | 4,802 | 4,039 | 15.9% |
| bloated | 38,596 | 7,200 | 81.3% |

bloated = the same big file re-read every turn plus a long verbose explanation
every turn. dedup collapses the repeated reads and distill compresses the old
prose.

Per-stage (medium payload, each stage alone):

| stage | reduction |
|-------|----------:|
| tools | 14.1% |
| system | 0.2% |
| messages | 1.0% |
| dedup | 0.0% |
| distill | 0.0% |
| all | 15.2% |

dedup and distill show 0% on a medium payload because there is nothing
duplicate or old enough to act on. They apply on bloated sessions (1.1% →
81.3% above).

Proxy overhead (large payload, n=80):

| mode | median | p95 |
|------|-------:|----:|
| pure Python | ~2.9 ms | ~3.9 ms |

The pipeline adds single-digit milliseconds. The speed gain comes from sending
fewer tokens, not from the proxy itself.

End-to-end (same payload with tools, raw-direct vs. through-proxy, live
llama-server, model-reported input_tokens):

| | raw | optimized | saved |
|--|----:|----------:|------:|
| input tokens | 1,164 | 1,028 | 11.7% |

## Tests

```bash
python3 tests/test_all.py                                       # 49 checks
```

Covers fence byte-identity, pair-safety, dedup, distill, default reduction
≥50% on a bloated payload, lazy-MCP smoke, and a proxy end-to-end.

## License

MIT, Copyright (c) 2026 greyok00. See [LICENSE](LICENSE).