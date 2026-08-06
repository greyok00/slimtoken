# slimtoken

**A token-optimization proxy that shrinks what you send to an LLM — before it's sent. Every feature is ON by default. Zero config required.**

Every request your coding agent makes — tool schemas, system prompt, chat
history — is minified, de-duplicated, and distilled in pure stdlib, then
forwarded to your local llama-server *or* a cloud API. Fewer tokens in means
faster prompt-eval, lower cost, and more room for context. Drop it in by
pointing `ANTHROPIC_BASE_URL` (or any Anthropic-compatible client's base URL)
at it.

It works the moment you install it. **There is nothing to enable, no flags to
set, no config file to write.** Every optimization below runs by default. The
only knobs that exist are *opt-out* kill-switches (set an env var to `0` to
turn something off) — you never need them. A separate, **optional**
`config-optimizer` script can additionally tune your *backend* (llama-server)
to your hardware; that's the only "extra" step, and it too is opt-in.

---

## ✨ Why slimtoken

| Problem slimtoken solves | What happens without it |
|--------------------------|-------------------------|
| 30 verbose tool schemas re-sent every request | Wasted prompt tokens, slow eval |
| The same big file re-read every turn | Context fills with duplicates |
| Long old explanations still in full | Old prose crowds out new context |
| Whitespace / boilerplate bloat | Free tokens left on the table |
| Coding-agent history grows unbounded | OOMs / cost spikes on long sessions |

---

## 📦 All features (all ON by default)

| # | Feature | What it does | On by default? |
|--:|---------|--------------|:--------------:|
| 1 | **Tool minify** | Strips boilerplate from tool `description`s, drops `$comment`/`title`/`examples`, keeps `name`/`required`/`enum`/`type`/structure verbatim | ✅ |
| 2 | **System minify** | Fence-aware whitespace collapse on the system prompt; preserves `<tag>` memory markers + code fences byte-identical | ✅ |
| 3 | **Message minify** | Collapses blank-line runs + trailing whitespace in chat text; passes tool_use/tool_result/image blocks through untouched | ✅ |
| 4 | **Dedup tool results** | Collapses repeated `tool_result` contents; latest kept verbatim, older duplicates stubbed (SHA-256 content hash) | ✅ |
| 5 | **Distill old turns** *(prompt pruning)* | Extractive summaries of old assistant prose beyond the last 8 turns; fence-aware, preserves tool blocks, **no model call** | ✅ |
| 6 | **Token budget** | Generous backstop (default 131 072); only catches pathological bloat, never normal sessions | ✅ |
| 7 | **Drop-in proxy** | Sits in front of any Anthropic-compatible backend via `ANTHROPIC_BASE_URL`; local HTTP or cloud HTTPS | ✅ |
| 8 | **Grammar strip** | Removes redundant schema grammar that the model already infers | ✅ |
| 9 | **Metrics** | `GET /metrics` exposes per-stage token savings + counts | ✅ |
| 10 | **TLS** | Native TLS for cloud HTTPS upstreams (SNI; optional mTLS / insecure) | ✅ |
| 11 | **Lazy MCP** | One stub tool per configured MCP server; spawns the real server on call (config-driven, empty config = no-op) | ✅ |
| 12 | **Code-fence aware** | ` ``` ` code blocks are **byte-identical** in/out; malformed fences over-preserve (safe) | ✅ |
| 13 | **Pair-safe pruning** | Never orphans a `tool_result` from its `tool_use`; valid drop points only | ✅ |
| 14 | **Overhead-optimized** | Identity-based change detection — unchanged content returns the original object, zero-copy | ✅ |
| 15 | **Cython-compiled** | All 15 modules ship as native `.so`; protects source + speeds the hot path | ✅ |
| 16 | **Clean install / uninstall** | Only touches `ANTHROPIC_BASE_URL` in your shell rc (backed up + restored); never touches `settings.json`, `CLAUDE.md`, or `mcp.json` | ✅ |
| 17 | **Config optimizer** *(optional)* | A separate script that tunes your *backend* (llama-server) to your GPU/model — safe, system-specs-based. **Optional**, see §"Going further" | ⚙️ optional |

> **No opt-in.** Features 1–16 have no "enable" flag. The only env vars that
> exist set a value to `0` to *disable* a stage (e.g. `SLIMTOKEN_MINIFY_TOOLS=0`)
> — you never need to set any of them. Defaults are the recommended config.

---

## 🔧 How optimized it works *by default*

Measured against the **compiled `.so`** (reproduce with
`python3 bench/benchmark.py`). Numbers are honest and reproducible — no
fabrication, no cherry-picking.

### 1. Payload reduction — fewer tokens sent (default config = all stages ON)

| Payload | Raw tokens | Default out | Saved |
|---------|-----------:|------------:|-----:|
| small   |      1,069 |         935 | 12.5% |
| medium  |      2,590 |       2,196 | 15.2% |
| large   |      4,802 |       4,039 | 15.9% |
| **bloated** | **38,596** |   **7,200** | **81.3%** |

- **Normal sessions**: ~13–16% smaller requests, automatically, out of the box.
- **Bloated sessions** (the same big file re-read every turn + long verbose
  explanations every turn): **81.3% smaller** — this is where `dedup` +
  `distill` (the prompt pruner) earn their keep.

### 2. Proxy overhead — what the minify pipeline *costs*

| Metric | Value (large payload, n=80) |
|--------|-----------------------------|
| median | **~3 ms** |
| p95    | ~4 ms |

The pipeline adds single-digit milliseconds. On any real LLM round-trip that is
noise. Identity-based change detection means unchanged content is returned
zero-copy (no JSON re-serialization).

### 3. End-to-end against a real backend

Sent the **same** Anthropic payload (with tools) raw-direct vs. optimized-through-the-proxy to a live llama-server backend, and read the model's own reported `input_tokens`:

| Metric | Raw | Optimized | Saved |
|--------|----:|----------:|------:|
| input tokens (median) | 1,164 | 1,028 | **11.7%** |

The model itself reports 11.7% fewer tokens actually processed — real money
and real prompt-eval time saved, on top of an already-optimized backend.

> Reproduce: `python3 bench/benchmark.py --backend http://127.0.0.1:8082`

---

## ⚙️ Going further — the OPTIONAL config-optimizer

Everything above is what you get for free, by default. If you also run your
**own llama-server backend**, `slimtoken config-optimizer` can additionally
tune that backend to your hardware. This is **optional** and **safe**:

```bash
slimtoken config-optimizer
```

It inspects your system specs (available VRAM, model file size) and recommends
llama-server arguments (`--ctx`, `--ubatch`, `--n-gpu-layers`, cache sizes,
weights) sized to fit your GPU without OOMing. It **only recommends** — it
prints the command, it does not silently change anything. It also surfaces the
`CORTEXAGENT_*` exports that the CortexAgent stack expects, but those are just
passed through; slimtoken does not depend on CortexAgent.

| | Default slimtoken (proxy) | + optional `config-optimizer` |
|--|----------------------------|--------------------------------|
| What it tunes | The **request** (tokens sent) | The **backend** (llama-server args) |
| Required? | No — works at install | No — fully optional |
| Risk | Read-only on requests | Recommends only; you review + run |
| Extra gain | 13–81% fewer tokens | Larger ctx / faster eval / no OOM |

The two layers are independent and stack: the proxy shrinks *what you send*,
the config-optimizer tunes *what receives it*.

---

## 🛡️ Compiled, not interpreted

slimtoken ships as **native compiled code** (`.so`), not pure Python. Every one
of the 15 modules is compiled with Cython at build time. There is **no
pure-Python fallback** — if Cython or a C compiler is unavailable, the build
fails with a clear message rather than silently shipping slow, readable source.

This does two things:

- **Speeds the hot path** — the minify pipeline runs against native code, not
  interpreted bytecode (the ~3 ms overhead above is the compiled result).
- **Protects the source** — the readable `.py` is not what runs. The installed
  wheel contains only `.so` files: **15 `.so`, 0 `.py`, 0 `.c`**.

```bash
python3 setup.py build_ext --inplace   # .so next to .py (dev / tests vs .so)
pip install .                          # installs the compiled package
pip wheel .                             # builds a .so-only wheel
```

---

## 🚀 Quick start

```bash
# 1. install (compiles to .so, wires ANTHROPIC_BASE_URL — reversible)
bash scripts/install.sh

# 2. start the proxy pointing at your backend
slimtoken serve --upstream http://127.0.0.1:8082      # local llama-server
# or
slimtoken serve --upstream https://api.anthropic.com   # cloud

# 3. use your agent as normal — requests are now minified automatically
#    (point your tool's base URL at the proxy if it isn't already)
```

That's it. No config file, no enable flags. It's already doing everything.

### Verify it's working

```bash
curl -s http://127.0.0.1:8181/metrics   # per-stage token savings + counts
```

### Uninstall (clean — restores your prior base URL)

```bash
bash scripts/uninstall.sh
# or
slimtoken uninstall
```

---

## 🔌 Lazy MCP (optional, default no-op)

If you use MCP servers, slimtoken can lazily front them: it exposes one stub
tool per configured MCP server and only spawns the real server when that tool
is actually called. Driven by a config file (`~/.slimtoken/lazy_mcp.json` or
`$SLIMTOKEN_LAZY_MCP_CONFIG`). **Empty config = no-op** — if you never create
the file, lazy MCP does nothing.

```bash
slimtoken lazy-mcp smoke        # verify the loader works
slimtoken lazy-mcp --name mysrv # spawn a configured server directly
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
python3 setup.py build_ext --inplace          # compile to .so first
python3 -m pytest tests/                       # 49 checks, all vs the .so
python3 bench/benchmark.py                      # payload + overhead
python3 bench/benchmark.py --backend http://127.0.0.1:8082   # + end-to-end
```

49 checks pass against the compiled `.so`, including fence byte-identity,
pair-safety (no orphaned tool results), dedup, distill, and a proxy e2e.

---

## 📜 License

**Proprietary.** Copyright (c) 2026 greyok00. All Rights Reserved. See
[LICENSE](LICENSE). No copy, modification, distribution, or sublicense is
granted without written permission. The compiled `.so` is the distributed
artifact; the readable source is not licensed for redistribution.

---

## 🧱 How it drops in

```
your agent  ──►  slimtoken proxy (:8181)  ──►  llama-server (:8082) / cloud
                 [minify + dedup + distill]
```

slimtoken is a standalone, independently-installable/removable layer. It coexists
with CortexAgent / CortexLLM (distinct ports + env) but needs neither. Point any
Anthropic-compatible client's base URL at it and it works.