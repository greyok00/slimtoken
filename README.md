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

Measured on a local CPU backend with strict prompt caching:

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

## Real-world usage

**1. In front of a local model server (CPU or GPU).** If a local backend
(llama.cpp's `llama-server`, Ollama, or anything OpenAI-compatible) answers on
port 8080, this is zero-configuration — every request trimmed before it hits
the CPU:

```bash
llama-server --port 8080 &          # your model, unchanged
slimtoken serve --upstream http://127.0.0.1:8080
# clients now point at http://127.0.0.1:8181 instead of :8080
```

On CPU, prompt processing dominates long-context requests, so cutting prompt
tokens is the one lever that directly shortens time-to-first-token.

**2. In front of a cloud API or coding agent.** The proxy speaks HTTPS to the
upstream (system CAs, no setup), so pointing it at api.anthropic.com just
works:

```bash
slimtoken serve --upstream https://api.anthropic.com
ANTHROPIC_BASE_URL=http://127.0.0.1:8181 claude    # or any Anthropic-protocol client
SLIMTOKEN_UPSTREAM=https://api.anthropic.com slimtoken serve   # same thing via env
```

Billing metering reads the same wire format the API does, so the token counts
you stop sending are the token counts you stop paying for.

**3. Run it as a service and prove the savings.** The proxy writes a cumulative
stats ledger when `SLIMTOKEN_STATS_FILE` is set (the value is also picked up if
it lands after startup, e.g. from a systemd unit's `Environment=`), and
`slimtoken savings` prints the measured facts:

```ini
# /etc/systemd/system/slimtoken.service
[Service]
Environment=SLIMTOKEN_UPSTREAM=https://api.anthropic.com
Environment=SLIMTOKEN_STATS_FILE=/var/lib/slimtoken/stats.json
ExecStart=/usr/local/bin/slimtoken serve
```

```bash
slimtoken savings              # requests minified, tokens in/out/saved, mean %, trend
slimtoken savings --json       # same, for dashboards
```

`{latency, ...}` style per-request timings are available with
`slimtoken latency --port 8181` against a running proxy.

**4. One-off, without a server.** The CLI runs the same pipeline over a saved
request file, and everything dropped by the dedup stage can be recalled
verbatim:

```bash
cat request.json | slimtoken optimize        # print token counts in/out
slimtoken recall <digest>                    # return the exact dropped bytes
slimtoken modes --measure                    # what each mode saves on your text
slimtoken savings                            # the running proxy's ledger
```

**5. Inside an MCP-aware agent runtime.** `slimtoken-mcp` exposes the same
pipeline as MCP tools (`optimize_messages`, `estimate_tokens`, `prune_context`,
`minify_tool_result`, `inspect_budget`, `get_config`, `list_model_presets`).
Also shipped: `slimtoken-reframe-mcp` (turns a rambling prompt into a tight
instruction with no model call) and `slimtoken-memory-mcp` (the memory store).

## Web lanes (0.8.2): web_search / web_fetch on every port

Every slimtoken proxy answers Ollama-style web routes on its own port, so tools
that expect them (pi coding-agent's `web_search` / `web_fetch`, any ollama
client) work even when the local ollama daemon lacks the routes:

```bash
curl -X POST http://127.0.0.1:8181/api/web_search \
     -d '{"query": "free ai testing tools", "max_results": 5}'
curl -X POST http://127.0.0.1:8181/api/web_fetch \
     -d '{"url": "https://example.com/article"}'
```

- `web_search`: SearXNG lane first (`SLIMTOKEN_SEARXNG`, default
  `http://127.0.0.1:8888`), keyless DuckDuckGo-lite fallback. Returns
  `{query, lane, results: [{title, url, description}]}`.
- `web_fetch` / `web_search`: also answer at the unprefixed `/web_search`,
  `/web_fetch`, and (GET) with query-string args.
- `web_fetch`: polite UA, redirects followed, HTML stripped to readable text
  (200 KB cap). Returns `{url, status, text, content_type}`.

These routes bypass the minify pipeline entirely (chat requests are never
web-routed), run their network I/O off the event loop via `asyncio.to_thread`,
and degrade with a JSON error — never a hang — when all lanes fail.

## What runs by default vs. what you can turn on

The default `code` mode never rewrites a tool result, never touches model
output, and keeps the newest four messages verbatim. What it removes is a
byte-identical duplicate of something already in the conversation, and it
stores every dropped byte for exact recall. That is the whole default.

| Stage | Default | Toggle | What it does |
|---|---|---|---|
| Tool-result dedup | ✅ on | `SLIMTOKEN_MINIFY_DEDUP=0` disables | A byte-identical repeat of an earlier tool result is replaced with a ~100-char pointer at its *later* position; the last copy always stays in place. `slimtoken recall <digest>` returns the dropped bytes verbatim |
| Distill old turns | ✅ on | `SLIMTOKEN_MINIFY_DISTILL=0` disables | Old assistant prose (beyond the newest 4 turns) is compressed to extractive summaries — actual sentences, no rephrasing, no model call; code and JSON are exempt |
| Token budget | ✅ on (131,072) | `SLIMTOKEN_MINIFY_BUDGET=<n>` | Prunes whole turns (never splitting tool_use/tool_result pairs) when a request exceeds the ceiling |
| Tool-schema minify | off | `SLIMTOKEN_MINIFY_TOOLS=1` | Drops redundant JSON-schema fields from tool definitions |
| System-prompt minify | off | `SLIMTOKEN_MINIFY_SYSTEM=1` | Shrinks your system prompt; fence-aware, code stays verbatim |
| Message minify | off | `SLIMTOKEN_MINIFY_MESSAGES=1` | Collapses blank lines in message bodies outside code fences |
| Output caps | off | `SLIMTOKEN_MAX_TOKENS` / `SLIMTOKEN_STOP` | Caps or truncates streamed completions; deliberate, opt-in |
| Whole pipeline | ✅ on | `SLIMTOKEN_MINIFY=0` | Disables all rewriting for that process — raw passthrough |

**Removed on purpose (0.8.0).** The stages that rewrote evidence — the
tool-result compressor, the DOM/HTML pruner, and the output fillers/blockers
that stripped lead-ins or rewrote bare `endoftext`-style words — were removed
from the code with no mode, CLI flag, or env var that can re-enable them.
Tool results reach the model byte-verbatim; model output reaches your client
byte-verbatim (the output filter is inert unless you set the caps above). See
[CHANGELOG.md](CHANGELOG.md) for the reasoning.

Two guarantees on top:

1. **Prompt-cache safety.** A repeat is stubbed at its *later* position, so
   earlier messages never change — exact-prefix prompt caches (Anthropic API
   caching, llama.cpp KV reuse) stay valid. Setting
   `SLIMTOKEN_DEDUP_APPEND_ONLY=0` restores the pre-0.7.2 oldest-copy behavior.
2. **Reversibility.** Every dropped byte is stored under its sha256 digest in
   `~/.slimtoken/artifacts` and is recallable verbatim.

## Modes

`slimtoken modes` lists them; `slimtoken modes --measure` prints what each one
saves on your own text:

- **`code`** (default) — dedup + distill + token budget, as tabled above. Not
  lossy: nothing is removed that isn't stored elsewhere.
- **`realtime`** — lossy, for speech-to-speech conversation only: all five
  stages on, prose cut to 160 chars per turn, user turns elided too. Do not
  use it for agent work — elided text still reads as complete, so the model
  answers confidently from evidence it no longer has.
- `SLIMTOKEN_MODE` selects a mode; any explicit `SLIMTOKEN_MINIFY_<STAGE>`
  switch overrides the mode for that one stage.

## Configuration

| Variable | What it controls |
|----------|------------------|
| `SLIMTOKEN_MODE` | `code` (default) or `realtime` |
| `SLIMTOKEN_MINIFY` | `0` disables all rewriting |
| `SLIMTOKEN_MINIFY_<STAGE>` | per-stage toggle (`DEDUP`, `DISTILL`, `TOOLS`, `SYSTEM`, `MESSAGES`) |
| `SLIMTOKEN_MINIFY_BUDGET` | token-budget ceiling (default 131072) |
| `SLIMTOKEN_KEEP_LAST` | newest messages kept verbatim (default 4) |
| `SLIMTOKEN_DEDUP_MIN_CHARS` | minimum result size before dedup applies (default 200) |
| `SLIMTOKEN_DISTILL_MAX_CHARS` | prose cut-off per old turn (mode default 4096) |
| `SLIMTOKEN_DISTILL_INCLUDE_USER` | `1` also elides old user turns (off in `code`) |
| `SLIMTOKEN_MINIFY_TOOL_SKIP` | comma-separated tool names never deduplicated |
| `SLIMTOKEN_DEDUP_APPEND_ONLY` | `1` (default) stubs later duplicates; `0` restores pre-0.7.2 behavior |
| `SLIMTOKEN_UPSTREAM` | backend URL (default `http://127.0.0.1:8080`) |
| `SLIMTOKEN_PORT` | proxy listen port (default 8181) |
| `SLIMTOKEN_HTTP2` | opt-in HTTP/2 forwarding |
| `SLIMTOKEN_STATS_FILE` | path for cumulative minify stats JSON (read late, so unit-file env works) |
| `SLIMTOKEN_MAX_TOKENS` / `SLIMTOKEN_STOP` | output caps; off by default |
| `SLIMTOKEN_ARTIFACT_DIR` | where recalled originals are stored (default `~/.slimtoken/artifacts`) |
| `SLIMTOKEN_STATE_DIR` | root for artifacts/stats state |
| `SLIMTOKEN_TLS_CA` / `_INSECURE` / `_CLIENT_CERT` / `_CLIENT_KEY` | TLS to the backend |
| `SLIMTOKEN_MEMORY_DIR` | memory store root (default `~/.slimtoken/memory`) |
| `SLIMTOKEN_MEMORY_*` | memory tier names, scheduler tick, distiller cadence |
| `SLIMTOKEN_LAZY_MCP_CONFIG` | path of the lazy-MCP server list (default `~/.slimtoken/lazy_mcp.json`) |

**Local model or cloud (Ollama):** the lane forwards to whatever `SLIMTOKEN_UPSTREAM` names. One line decides where the model runs:

```bash
# local llama.cpp server (default)
export SLIMTOKEN_UPSTREAM="http://127.0.0.1:8080"

# Ollama instead - local ollama :11434 or your ollama-cloud host:
export SLIMTOKEN_UPSTREAM="http://127.0.0.1:11600/v1"
```

For provider-side prompt caching keep the cache-safe stage set (`SLIMTOKEN_MINIFY_MESSAGES=0 SLIMTOKEN_MINIFY_DISTILL=0 SLIMTOKEN_MINIFY_SYSTEM=0`; tools + append-only dedup + the loop-guard stay on): lossy passes rewrite earlier turns and invalidate the cached prefix, while the cache-safe set keeps it byte-stable.

## Install

Requirements: Python 3.13+ and pip; there are no compiled modules or platform
restrictions. `tiktoken` is the only non-trivial dependency and its tokenizer
table ships inside the package (offline).

```bash
git clone https://github.com/greyok00/slimtoken
cd slimtoken && ./scripts/install.sh   # pip install -e . + slimtoken install
```

Or with plain pip: `pip install .`

## Verify

```bash
slimtoken modes --measure     # what each mode saves, measured on your tokenizer
slimtoken latency --port 8181 # per-request t0–t4 timings through a running proxy
slimtoken savings             # hard savings facts from the stats ledger
python3 -m pytest tests/      # test suite (165 tests)
```

## How it compares

| Tool | Relationship to slimtoken |
|------|--------------------------|
| CLI coding agents | The agent runs the loop and calls the model directly; slimtoken shrinks each call in between. |
| Open Interpreter / OpenHands | They host the agent loop in-process; slimtoken is a network sidecar underneath any host. |
| aider | Manages git-integrated edits; slimtoken edits nothing — it makes the requests around it cheaper. |
| LangChain-style frameworks | Shape requests inside your app; slimtoken applies the same passes externally to every client. |

## License

Apache-2.0. See [LICENSE](LICENSE).
