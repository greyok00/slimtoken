# BENCHMARK REPORT — slimtoken audit (2026-10-31)

Primary question: **actual end-to-end speed difference** between (A) direct backend
requests, (B) proxy with rewriting disabled, (C) shipped default code-mode pipeline,
(D) optional realtime (lossy) mode — and, post-fix, (N) the 0.7.2 append-only dedup
policy. Token reduction is reported separately and never treated as speed.

Backend: llama.cpp-family (`greymini-server` binary) running the machine's existing
Qwen3.5-4B-Instruct-Q4_K_M GGUF, **CPU-only** (12 threads), `--cache-reuse 0`
(strict exact-prefix caching — the Anthropic-style case), single slot, isolated on
127.0.0.1:7791. Anthropic `/v1/messages` format. Metrics: TTFC = client time to the
first streamed text delta; total = full client wall time; server log parsed for
prompt-eval ms/counts; `message_start` usage for input/cache-read tokens. Raw rows:
`~/tmp/st-audit/results/bench_raw.jsonl` (683 rows); env manifest in the same dir.

**Caveat, stated plainly:** the machine ran with external load (rsync, browser) at
load-average 15–17 during these runs, so absolute times are inflated and noisier
than a quiet machine would give — medians and *cross-condition differences on
interleaved trials* remain informative, but treat single-condition medians as
upper bounds. Backend is CPU-only at ~600–650 prompt-tokens/s, which makes prompt
size matter far more than it would on a GPU backend.

## E0 — proxy overhead (mock upstream, n=40/condition)

The proxy's own cost, measured against a mock backend that responds immediately:
direct 0.5 ms; passthrough-with-proxy ~4.3 ms median; default code mode ~5–6 ms;
realtime ~8 ms. Overhead is negligible on every real workload below (the smallest
real request takes 1.7 s).

## E1–E5 — single-shot workloads, cold cache (unique salt per trial)

| Workload | A direct | B passthrough | C code mode | Meaning |
|---|---|---|---|---|
| W1 short (29 tok in) | 1716 ms (n=16) | 2158 ms | 2049 ms | all equal within noise; proxy never the bottleneck |
| W2 one big unique file (6.3k tok) | 12646 ms | 11273 ms | 11648 ms | nothing to dedup → no gain, as expected |
| W3 four identical big reads (25.7k→7.0k tok sent) | **47.4 s** | 44.7 s | **18.5 s** | **2.4–2.6× faster end-to-end** |
| W4 four different versions (26.3k tok, no dup) | 50.5 s | 57.7 s | 49.2 s | honest negative: no dedupable content → no gain |
| W5 big unique prose (4.5k tok) | 9385 ms | 8782 ms | 10594 ms | honest negative: C may cost a little on unique prose |

W3 is the case dedup exists for: sending one 25.7 k-token body versus a 7 k-token
deduplicated body cuts prompt processing from ~29 s to ~11 s on CPU. W4/W5 are
workloads where the pipeline has no byte-identical material, and the honest result
is that it neither helps nor (beyond ~1 s) hurts. This matches the token→time
translation the auditor predicted before the run: token reduction becomes speed
only where prompt processing dominates, and never becomes 3.7× overall.

## E6 — warm growing session, 13 requests (n=4 sessions A/B/C/N, 2 D)

Growing agent conversation; turn 4 re-reads turn 1's file byte-identically (the
dedup event), turns 6–7 bring changed versions. Read-add turns returned HTTP 400
(harness limitation: two trailing assistant messages) for **every** condition
symmetrically, so the measured rows are the interleaved prose turns — which still
carry each condition's full rewritten history.

Per-request medians (ms and cached-prefix tokens) — this is the load-bearing table:

| Req | A direct | B passthrough | C shipped dedup | **N append-only (0.7.2)** |
|---|---|---|---|---|
| 3 | 2012 — cached 3178 | 1922 — 3178 | 1892 — 3178 | 2013 — 3178 |
| **5** (first req after the duplicate) | 5858 — cached 3506 | 5998 — 3506 | **7271 — cached 0** | **1963 — cached 3506** |
| 8 | 10717 — 6903 | 10833 — 6903 | 10302 — 4030 | 10174 — 4038 |
| 9–13 (steady state) | ~2300 — 13503→14817 | ~2350 — 13503→14817 | ~2280 — 10630→11944 | ~2220 — 10638→11952 |

The user's suspected failure mode, reproduced and measured: with the shipped
(0.7.1) policy, the request in which a duplicate first appears **resets the
prompt cache to zero** (C: cached 3506 → 0) and costs 7.3 s instead of ~5.9 s,
and C's cached prefix stays ~2.9 k tokens (one file copy) below A/B for the rest
of the session. With the append-only fix (N), the cache survives exactly as it
does with no rewriting (cached 3506 at req 5), and the stubbed duplicate *also*
shrinks the uncached tail — so req 5 drops from 7271 ms to **1963 ms**.

Whole-session wall time (sum of 13 requests, 4 sessions each):

| Condition | Mean | Sessions |
|---|---|---|
| A direct | 36.4 s | 37.0 / 36.8 / 36.3 / 35.4 |
| B passthrough | 36.6 s | 36.2 / 36.4 / 37.0 / 36.8 |
| C shipped dedup (0.7.1) | 37.2 s | 36.1 / 39.0 / 36.3 / 37.2 |
| **N append-only (0.7.2 fix)** | **31.3 s** | 31.5 / 30.1 / 32.0 / 31.6 |
| D realtime (lossy) | 23.9 s (n=2) | 24.8 / 23.0 |

Net: the shipped pipeline was a *net regression* on this warm multi-turn session
(+0.8 s vs no proxy, within noise) because the cache penalty canceled the token
savings; append-only keeps the token savings without the cache penalty and lands
**~14% faster end-to-end** than every other non-lossy condition. D (realtime)
is faster still only because it elides old turns lossily — same caveat the
README already carries: never run agent work in realtime mode.

## TTFC and prompt-processing corroboration

TTFC tracks totals (prompt processing dominates TTFC on CPU): e.g. W3
ttfc median A 40.8 s vs C 11.6 s. Backend log prompt-eval rows agree with the
client timings (C req 5 prompt_ms ≈ 7.0 s at 4356 tokens vs N's cached-hit path).

## Conclusions

1. **Proxy overhead is negligible** (~4–8 ms) in every mode.
2. **Dedup helps only where prompts contain repeated tool output**, and there it
   helps a lot on a CPU/strict-cache backend (2.4× end-to-end single-shot; the
   warm-session req-5 incident resolved from 7.3 s to 2.0 s).
3. **The 0.7.1 default was a net wash on warm multi-turn sessions** (~+0.8 s vs
   passthrough, within noise) because oldest-copy stubbing invalidated the
   exact-prefix cache. The 0.7.2 append-only default restores the cache benefit
   and adds the token benefit: ~14% faster per 13-request warm session on this
   backend, with correctness covered by the regression suite.
4. **Realtime mode is not a valid substitute** for speed — it is lossy by
   design and reported separately.
5. These are CPU-backend numbers with heavy ambient load; on a GPU backend the
   absolute savings shrink (GPU processes prompt tokens much faster), but the
   cache-invalidation mechanism — and therefore the append-only fix — applies
   to any exact-prefix-cache backend, including production Anthropic API caching.