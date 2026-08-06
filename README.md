# slimtoken

**A token-optimization proxy that shrinks what you send to an LLM — before it's sent. Every feature is ON by default. Zero config. MIT-licensed, pure Python, runs anywhere.**

Every request your coding agent makes — tool schemas, system prompt, chat
history — is minified, de-duplicated, and distilled, then forwarded to your local
llama-server *or* a cloud API. Fewer tokens in means faster prompt-eval, lower
cost, and more room for context. Drop it in by pointing `ANTHROPIC_BASE_URL` (or
any Anthropic-compatible client's base URL) at it.

It works the moment you install it. **Nothing to enable, no flags, no config
file.** Every optimization runs by default; the only env vars that exist are
*opt-out* kill-switches you never need. An optional `config-optimizer` script can
additionally tune your *backend* to your hardware, and an optional `build-speed.sh`
can compile the package to native code — both opt-in.

- 🟢 **MIT-licensed, pure stdlib** — no runtime dependencies; runs on any Python 3.9+ / any OS.
- 🟢 **Clean install / clean uninstall** — only touches `ANTHROPIC_BASE_URL` in your shell rc (backed up + restored). Never touches `settings.json`, `CLAUDE.md`, or `mcp.json`.
- 🟢 **Works with or without** CortexAgent / CortexLLM (distinct ports + env, coexists).

---

## 📦 All features (all ON by default)

| # | Feature | What it does | Default |
|--:|---------|--------------|:------:|
| 1 | **Tool minify** | Strips boilerplate from tool `description`s (keeps first fenced example), drops `$comment`/`title`/`examples`; keeps `name`/`required`/`enum`/`type`/structure verbatim | ✅ |
| 2 | **System minify** | Fence-aware whitespace collapse; preserves `<tag>` memory markers + code fences byte-identical; collapses duplicate banner lines | ✅ |
| 3 | **Message minify** | Collapses blank-line runs + trailing whitespace in chat text; passes tool_use / tool_result / image blocks through untouched | ✅ |
| 4 | **Dedup tool results** | Collapses repeated `tool_result` contents (same big file re-read each turn); latest kept verbatim, older duplicates stubbed (SHA-256 content hash) | ✅ |
| 5 | **Distill old turns** *(prompt pruning)* | Extractive summaries of old assistant prose beyond the last 8 turns; fence-aware, preserves tool blocks, **no model call** | ✅ |
| 6 | **Token budget** | Generous backstop (131 072); only catches pathological bloat, never normal sessions | ✅ |
| 7 | **Drop-in proxy** | Sits in front of any Anthropic-compatible backend via `ANTHROPIC_BASE_URL`; local HTTP or cloud HTTPS | ✅ |
| 8 | **Grammar strip** | Drops the `grammar` field the model already infers | ✅ |
| 9 | **Metrics** | `GET /metrics` exposes token savings + counts | ✅ |
| 10 | **TLS** | Native TLS for cloud HTTPS upstreams (SNI; optional mTLS / insecure) | ✅ |
| 11 | **Lazy MCP** | One stub tool per configured MCP server; spawns the real server on call (config-driven, empty config = no-op) | ✅ |
| 12 | **Code-fence aware** | ` ``` ` blocks are **byte-identical** in/out; malformed fences over-preserve (safe) | ✅ |
| 13 | **Pair-safe pruning** | Never orphans a `tool_result` from its `tool_use`; valid drop points only | ✅ |
| 14 | **Overhead-optimized** | Identity-based change detection — unchanged content returns the original object, zero-copy | ✅ |
| 15 | **Optional Cython build** | `scripts/build-speed.sh` compiles every module to native `.so` on your machine | ⚙️ opt-in |
| 16 | **Clean install / uninstall** | Reversible rc wiring; never touches agent config files | ✅ |
| 17 | **Config optimizer** *(optional)* | `slimtoken config-optimizer` recommends llama-server args for your GPU/model (safe, recommend-only) | ⚙️ opt-in |

> **No opt-in.** Features 1–14 + 16 have no "enable" flag — only opt-out kill-switches
> (`SLIMTOKEN_MINIFY_*=0`). Defaults are the recommended config.

---

## ⚡ How much faster? (measured, honestly)

Reproduce with `python3 bench/benchmark.py` (and `--backend http://127.0.0.1:8082`
for the end-to-end number). No fabrication, no cherry-picking.

### 1. Each feature on/off — tokens saved (≈ prompt-eval speedup)

The dominant speed lever is **sending fewer tokens**: prompt-eval time on the
backend scales with input tokens, so X% fewer tokens ≈ ~X% faster prompt-eval.
Medium payload, each stage alone vs. original:

| Stage ON (alone) | Tokens | Saved | Reduction |
|------------------|-------:|------:|----------:|
| (nothing)         | 2,590 |     0 |     0.0% |
| + tools           | 2,225 |   365 |    14.1% |
| + system          | 2,585 |     5 |     0.2% |
| + messages        | 2,565 |    25 |     1.0% |
| + dedup           | 2,590 |     0 |     0.0% |
| + distill         | 2,590 |     0 |     0.0% |
| **all (default)** | **2,196** | **394** | **15.2%** |

`tools` does the heavy lifting on a normal request. `dedup` + `distill` read 0%
on a *medium* payload because there's nothing to dedup and nothing old enough to
distill — they earn their keep on **bloated** sessions (see next table).

### 2. Payload reduction — default config (all stages ON)

| Payload | Raw tokens | Default out | Saved |
|---------|-----------:|------------:|------:|
| small   |      1,069 |         935 | 12.5% |
| medium  |      2,590 |       2,196 | 15.2% |
| large   |      4,802 |       4,039 | 15.9% |
| **bloated** | **38,596** |   **7,200** | **81.3%** |

"Bloated" = the same big file re-read every turn + a long verbose explanation
every turn — exactly what real coding-agent sessions accumulate. `dedup`
collapses the repeated file reads and `distill` (prompt pruning) compresses the
old verbose prose → **81.3% smaller**.

### 3. Proxy overhead — what the pipeline *costs*

| Mode | median | p95 |
|------|-------:|----:|
| pure Python (default) | ~2.9 ms | ~3.9 ms |
| Cython `.so` (optional) | ~3.0 ms | ~5.0 ms |

Single-digit milliseconds — noise next to any LLM round-trip. The optional
Cython build compiles the hot path to native code, but **on this pure-stdlib
workload it's within run-to-run noise of pure Python** (sometimes slightly
*slower*: Cython's p95 is higher here). The code is already fast interpreted; the
real speedup comes from sending fewer tokens, not from compiling the proxy. The
Cython build is there for those who want native code / other Python versions.

### 4. Fully optimized — all stages + real backend

Same Anthropic payload (with tools) sent RAW-direct vs OPTIMIZED-through-the-proxy
to a live llama-server; the model's own reported `input_tokens`:

| Metric | Raw | Optimized | Saved |
|--------|----:|----------:|------:|
| input tokens (median) | 1,164 | 1,028 | **11.7%** |

The model itself reports **11.7% fewer tokens actually processed** — real
prompt-eval time and cost saved on top of an already-optimized backend. That's
the combined effect of all minify stages working together.

### What "fully optimized" stacks

| Layer | What it speeds up | Measured effect |
|-------|---------------------|-----------------|
| Minify stages (default) | tokens sent → backend prompt-eval | 12.5–81.3% fewer tokens |
| Optional `config-optimizer` | the *backend* (llama-server `--ctx`/`--ubatch`/`-ngl` for your VRAM) | larger ctx / no OOM (recommend-only) |
| Optional `build-speed.sh` (Cython) | proxy hot path | ~3 ms either way (within noise here) |
| **Combined real-world** | **e2e vs raw backend** | **11.7% fewer input tokens** |

> Reproduce: `python3 bench/benchmark.py --backend http://127.0.0.1:8082`

---

## ⚙️ Going further — the two optional levers

Everything above is what you get for free, by default. Two **optional** scripts
add more — both are opt-in and safe:

```bash
slimtoken config-optimizer     # recommends llama-server args for your GPU+model
bash scripts/build-speed.sh     # compiles slimtoken to native .so on your machine
```

| | Default (proxy) | + `config-optimizer` | + `build-speed.sh` |
|--|------------------|----------------------|--------------------|
| Tunes | the **request** (tokens sent) | the **backend** (llama-server args) | the **proxy** (native compile) |
| Risk | read-only on requests | recommend-only; you review + run | local build; needs gcc+Cython |
| Gain | 12.5–81.3% fewer tokens | bigger ctx / faster eval / no OOM | ~3ms either way (within noise here) |

The three layers stack and are independent: the proxy shrinks *what you send*,
the config-optimizer tunes *what receives it*, the build script compiles *the
proxy itself*.

---

## 🚀 Quick start

```bash
# 1. install (pure Python, compiles nothing, wires ANTHROPIC_BASE_URL — reversible)
bash scripts/install.sh

# 2. start the proxy pointing at your backend
slimtoken serve --upstream http://127.0.0.1:8082      # local llama-server
# or
slimtoken serve --upstream https://api.anthropic.com   # cloud

# 3. use your agent as normal — requests are minified automatically
```

No config file, no enable flags. It's already doing everything.

### Optional: make it native code

```bash
bash scripts/build-speed.sh     # compiles to .so for your Python (needs gcc + Cython)
```

### Verify it's working

```bash
curl -s http://127.0.0.1:8181/metrics   # per-stage token savings + counts
```

### Uninstall (clean — restores your prior base URL)

```bash
bash scripts/uninstall.sh        # or: slimtoken uninstall
```

---

## 🔌 Lazy MCP (optional, default no-op)

If you use MCP servers, slimtoken can lazily front them: one stub tool per
configured MCP server, spawning the real server only when that tool is called.
Driven by `~/.slimtoken/lazy_mcp.json` (or `$SLIMTOKEN_LAZY_MCP_CONFIG`).
**Empty config = no-op** — if you never create the file, lazy MCP does nothing.

```bash
slimtoken lazy-mcp smoke         # verify the loader
slimtoken lazy-mcp --name mysrv   # spawn a configured server directly
```

---

## 🔧 Optional opt-out env vars (you normally never set these)

| Env var | Default | Set to `0` to… |
|---------|---------|----------------|
| `SLIMTOKEN_MINIFY` | `1` | disable minify entirely (passthrough) |
| `SLIMTOKEN_MINIFY_TOOLS` | `1` | disable tool minify |
| `SLIMTOKEN_MINIFY_SYSTEM` | `1` | disable system minify |
| `SLIMTOKEN_MINIFY_MESSAGES` | `1` | disable message minify |
| `SLIMTOKEN_MINIFY_DEDUP` | `1` | disable tool-result dedup |
| `SLIMTOKEN_MINIFY_DISTILL` | `1` | disable old-turn distillation (prompt pruning) |
| `SLIMTOKEN_MINIFY_BUDGET` | `131072` | raise/lower the budget backstop |
| `SLIMTOKEN_KEEP_LAST` | `8` | keep N recent turns verbatim before distilling |
| `SLIMTOKEN_DEDUP_MIN_CHARS` | `200` | only dedup tool results ≥ this many chars |
| `SLIMTOKEN_DISTILL_MAX_CHARS` | `240` | max chars per distilled old turn |
| `SLIMTOKEN_MINIFY_TOOL_SKIP` | _(none)_ | comma-list of tool names to never minify |
| `SLIMTOKEN_PORT` | `8181` | proxy listen port |
| `SLIMTOKEN_UPSTREAM` | _(required to serve)_ | backend URL |

These are **opt-out** overrides on already-good defaults — not opt-in flags.

---

## 🧪 Tests & benchmarks

```bash
python3 tests/test_all.py                          # 49 checks (pure Python, or .so if built)
SLIMTOKEN_BUILD_CYTHON=1 python3 setup.py build_ext --inplace   # build .so in-place, then re-run tests against it
python3 bench/benchmark.py                         # payload + per-stage + overhead
python3 bench/benchmark.py --backend http://127.0.0.1:8082      # + end-to-end
```

49 checks pass in both pure-Python and Cython modes — fence byte-identity,
pair-safety (no orphaned tool results), dedup, distill, default ≥50% reduction
on a bloated payload, lazy-MCP smoke, and a proxy e2e.

---

## 📜 License

**MIT** — Copyright (c) 2026 greyok00. See [LICENSE](LICENSE). Do whatever you
want, just keep the notice.

---

## 🧱 How it drops in

```
your agent  ──►  slimtoken proxy (:8181)  ──►  llama-server (:8082) / cloud
                 [minify + dedup + distill]
```

slimtoken is a standalone, independently-installable/removable layer. It
coexists with CortexAgent / CortexLLM (distinct ports + env) but needs neither.
Point any Anthropic-compatible client's base URL at it and it works.