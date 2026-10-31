<p align="center">
  <img src="docs/images/slimtoken-banner.png" alt="SlimToken"/>
</p>

# SlimToken

SlimToken is a token-optimization and memory layer that sits between your agent
and its model backend. It rewrites each request before it leaves your machine:
duplicate tool output is collapsed to a short pointer, old turns are distilled,
and oversized requests are pruned to a token budget. It runs as a proxy, an MCP
server, a CLI, or a plain Python library — anything that speaks Anthropic,
OpenAI, or Ollama HTTP.

**Why you'd use it:**

- **Cloud models:** fewer tokens sent per request → lower API cost and lower
  rate-limit consumption. Nothing else about the interaction changes.
- **Local models:** fewer tokens to process → faster first token and faster
  total response. On a CPU backend, prompt processing is often the majority of
  the wait, so trimming prompt tokens is the cheapest speedup available.
- **Both:** it keeps the conversation below a token budget, so long agent
  sessions don't hit the model's context limit.

Measured on a local CPU backend with strict prompt caching
(full methodology and raw data: [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)):

| Workload | Tokens sent before | Tokens sent after | Effect |
|---|---|---|---|
| Same large tool output read 4 times | 25,713 | 7,029 | 2.4× faster end-to-end |
| 13-request growing agent session | 14,161/request | 11,288/request | ~14% faster whole session |
| No repeated content | unchanged | unchanged | no benefit, no harm (~5 ms overhead) |

## Quick start

```bash
git clone https://github.com/greyok00/slimtoken
cd slimtoken
./scripts/install.sh   # pip install -e . + slimtoken install
slimtoken serve        # proxy on port 8181; point your client at it
```

`slimtoken install` rewrites `ANTHROPIC_BASE_URL` to point at the proxy. Any
client that sends Anthropic/OpenAI/Ollama requests through it is optimized
without further setup.

## What runs by default vs. what you can turn on

The default `code` mode is conservative: it only removes content that is stored
elsewhere, so nothing is lost. Code blocks in tool output, code fences, and the
newest turns are never touched. The `realtime` mode is deliberately lossy and
is for speech-style conversation only — do not use it for agent work.

| Stage | Default | Opt-in with | What it does |
|---|---|---|---|
| Tool-result dedup | ✅ on (`SLIMTOKEN_MINIFY_DEDUP=1`) | `=0` to disable | Replaces a byte-identical repeat of a tool result with a ~100-char pointer; the dropped copy is stored and `slimtoken recall <digest>` returns it verbatim |
| Distill old turns | ✅ on | `SLIMTOKEN_MINIFY_DISTILL=0` to disable | Replaces old assistant prose (beyond the newest few turns) with extractive summaries — actual sentences, no rephrasing, no model call; JSON/code exempt |
| Token budget | ✅ on (131,072; `SLIMTOKEN_MINIFY_BUDGET`) | set any ceiling | Prunes whole turns (never splitting tool pairs) when a request exceeds the ceiling |
| Tool-schema minify | off | `SLIMTOKEN_MINIFY_TOOLS=1` | Drops redundant JSON-schema fields from tool definitions |
| System-prompt minify | off | `SLIMTOKEN_MINIFY_SYSTEM=1` | Shrinks your system prompt; fence-aware, code stays verbatim |
| Message minify | off | `SLIMTOKEN_MINIFY_MESSAGES=1` | Collapses blank lines in message bodies outside code fences |
| Tool-result compress | off | `SLIMTOKEN_TOOL_COMPRESS=1` | Truncates very large single tool results head/tail with a digest stub |
| DOM pruning | off | `SLIMTOKEN_MINIFY_DOM=1` | Prunes text inside large HTML tool results (head/tail cap) |
| Output filter | off | `SLIMTOKEN_MAX_TOKENS` / `SLIMTOKEN_STOP` / `SLIMTOKEN_FILLER=1` | Caps/truncates streamed completions; passes responses through byte-identical unless set |
| Whole pipeline | ✅ on | `SLIMTOKEN_MINIFY=0` | Disables all rewriting for that process — raw passthrough |

Two guarantees apply to everything in the default mode:

1. **Prompt-cache safety (0.7.2).** A repeat is stubbed at its *later* position,
   so earlier messages never change — exact-prefix prompt caches (Anthropic API
   caching, llama.cpp KV reuse) stay valid. Setting
   `SLIMTOKEN_DEDUP_APPEND_ONLY=0` restores the pre-0.7.2 oldest-copy behavior.
2. **Reversibility.** Every dropped byte is stored under its sha256 digest in
   `~/.local/state/slimtoken/artifacts` and is recallable verbatim.

## Modes

`slimtoken modes` lists them; `slimtoken modes --measure` prints what each one
saves on your own text:

- **`code`** (default) — dedup + distill + budget, as tabled above.
- **`realtime`** — everything on, including the lossy stages, with the newest
  tool result shortened. Only for speech-to-speech conversation.
- `SLIMTOKEN_MODE` selects a mode; any explicit `SLIMTOKEN_MINIFY_<STAGE>`
  switch overrides the mode for that one stage.

## Configuration

| Variable | What it controls |
|----------|------------------|
| `SLIMTOKEN_MODE` | `code` (default) or `realtime` |
| `SLIMTOKEN_MINIFY` | `0` disables all rewriting |
| `SLIMTOKEN_MINIFY_<STAGE>` | per-stage toggle (`TOOLS`, `SYSTEM`, `MESSAGES`, `DEDUP`, `DISTILL`, `BUDGET`, `DOM`) |
| `SLIMTOKEN_MINIFY_BUDGET` | token-budget ceiling (default 131072) |
| `SLIMTOKEN_KEEP_LAST` | newest turns kept verbatim (proxy default 4) |
| `SLIMTOKEN_TOOL_COMPRESS` | enables tool-result compression (off in `code` mode) |
| `SLIMTOKEN_DEDUP_APPEND_ONLY` | `1` (default) stubs later duplicates; `0` restores pre-0.7.2 behavior |
| `SLIMTOKEN_UPSTREAM` | backend URL (default `http://127.0.0.1:8080`) |
| `SLIMTOKEN_PORT` | proxy listen port (default 8181) |
| `SLIMTOKEN_HTTP2` | opt-in HTTP/2 forwarding |
| `SLIMTOKEN_STATS_FILE` | path for cumulative minify stats JSON |
| `SLIMTOKEN_ARTIFACT_DIR` | where recalled originals are stored (default `~/.local/state/slimtoken/artifacts`) |
| `SLIMTOKEN_TLS_CA` / `_INSECURE` / `_CLIENT_CERT` / `_CLIENT_KEY` | TLS to the backend |
| `SLIMTOKEN_MEMORY_DIR` | memory store root (default `~/.slimtoken/memory`) |
| `SLIMTOKEN_MEMORY_*` | memory tier names, scheduler tick, distiller cadence |
| `SLIMTOKEN_LAZY_MCP_CONFIG` | path of the lazy-MCP server list (default `~/.slimtoken/lazy_mcp.json`) |

## Using it as a client surface

**MCP servers (stdio):** `slimtoken-mcp` exposes `optimize_messages`,
`estimate_tokens`, `prune_to_budget`, `minify_tool_result`,
`inspect_budget`, `get_config`, `list_model_presets`, and
`high_context_presets` over the same pipeline as the proxy. Also shipped:
`slimtoken-reframe-mcp` (turns a rambling prompt into a tight instruction with
no model call) and `slimtoken-memory-mcp` (the memory store, 0.7.2+).

**CLI:**

```bash
slimtoken optimize --input request.json    # run the pipeline, print token counts
cat request.json | slimtoken optimize      # stdin works too
slimtoken recall <digest>                  # get back exact dropped bytes
slimtoken presets                          # recommended local-model configs by GPU tier
```

**Memory:** agent notes live in `~/.slimtoken/memory` — hot/warm/cold tiers
with atomic writes, a scheduler, and a distiller that folds old material into
short summaries.

## Install

Requirements: CPython 3.13 on Linux x86_64. The release wheel carries Cython
compiled modules for that interpreter and platform — other versions/platforms
are not supported.

From a release (no clone needed):

```bash
gh release download -R greyok00/slimtoken
pip install slimtoken-0.7.2-*.whl
slimtoken install
```

From a clone:

```bash
git clone https://github.com/greyok00/slimtoken
cd slimtoken && ./scripts/install.sh
```

## Verify

```bash
slimtoken modes --measure     # what each mode saves, measured on your tokenizer
slimtoken latency --port 8181 # per-request t0–t4 timings through a running proxy
python3 -m pytest tests/      # test suite (165 tests)
```

## How it compares

| Tool | Relationship to slimtoken |
|------|--------------------------|
| Claude Code-type CLI agents | The agent runs the loop and calls the model directly; slimtoken shrinks each call in between. |
| Open Interpreter / OpenHands | They host the agent loop in-process; slimtoken is a network sidecar underneath any host. |
| aider | Manages git-integrated edits; slimtoken edits nothing — it makes the requests around it cheaper. |
| LangChain-style frameworks | Shape requests inside your app; slimtoken applies the same passes externally to every client. |

## License

MIT — see [LICENSE](LICENSE).