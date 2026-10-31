# CLAIMS MATRIX — slimtoken 0.7.1 @ ac8bcd3 (branch audit/p0-cache-20261031)

Audited 2026-10-31 on CPython 3.13.5 / Parrot Linux 6.17 / x86_64.
Every claim that materially affects a user's choice to run or trust slimtoken.
Sources: README.md, the `slimtoken-optimizer` skill text, pyproject metadata.
Status legend: VERIFIED (tested, holds) · OVERSTATED (docs say more than code
does) · BROKEN (code contradicts the promise) · UNTESTED-HERE.

## Request pipeline

| Claim | Source | Implem. | Default | Result |
|---|---|---|---|---|
| Tool-schema minify (title/$comment/examples dropped, name+required+enum kept) | README | `minify_tools` | **on in pipeline library; off in the shipped proxy** (proxy stages = {dedup, distill}); opt-in via `SLIMTOKEN_MINIFY_TOOLS=1` | VERIFIED both levels (`test_tools`, proxy e2e asserts schemas pass through) |
| System-prompt minify, fence-aware, memory tags kept | README | `minify_system` | same split as above — **library on, proxy off** | VERIFIED (`test_system_and_budget`, proxy e2e asserts system untouched) |
| Message minify (blank-line collapse outside fences) | README | `message_minify` | same split | VERIFIED |
| Dedup: older byte-identical tool_result replaced by digest stub; changed versions never deduped | README | `dedup_tool_results` + `artifacts.store` | **on (both pipeline and proxy)** | VERIFIED incl. near-duplicate non-fire; stub is deterministic and re-minify-stable |
| Dedup drops nothing: digest stubs + `slimtoken recall <digest>` returns exact original | README | artifacts | on | VERIFIED round-trip; **BROKEN on corrupted blob — see F1** |
| Distill old turns (extractive, no model call), `keep_last` newest kept verbatim, pair-safe | README | `distill_old_turns` | on; proxy keep_last=4 | VERIFIED pair-safety (`test_pair_safety_defaults`, `test_distill_*`) |
| Token-budget backstop prune (`SLIMTOKEN_MINIFY_BUDGET`, default 131072) | README | `token_budget` | on | VERIFIED (drops whole turns, no orphan tool_results); budget token estimate ran ~6% under a fresh cl100k count in one probe — estimate basis, report as limitation |
| Code fences never touched (well-formed, malformed, inline, indented) | README/skill | all stages | all modes | VERIFIED (`test_fences`, dedup skips fenced, distill protects code) |
| Tool_use/tool_result pairs never split or reordered | skill | all stages | all modes | VERIFIED default + budget (`test_pair_safety_defaults`) |
| `SLIMTOKEN_MINIFY=0` raw passthrough | README | config | opt-in | VERIFIED (`test_modes`, proxy env2 test) |

## Modes

| Claim | Source | Result |
|---|---|---|
| `code` (default) = dedup + distill only, no tool-result rewrite beyond exact duplicates | skill | **OVERSTATED at pipeline level**: `MinifyConfig()` default stages also include system/messages/tools; the *proxy* ships code-mode as {dedup, distill} matching the skill. Docs reconciled: two different objects called "default". | 
| `realtime` = all stages incl. lossy tool-result rewrite + schema strip + prose cut | README/skill | VERIFIED (`test_modes`, `test_tool_result_compress`, `test_dom_stage`); still agent-unsafe by design |
| Dedup preserves prefix-cache reuse | (implied by lossless framing) | **BROKEN in 0.7.1**: first request where a later identical copy appears stubs the OLDER copy → rewrites an earlier message → cached prefix invalid from that point (Anthropic documented rule). **Fixed in 0.7.2** with append-only policy (later copies stubbed, earlier messages byte-stable); knob `SLIMTOKEN_DEDUP_APPEND_ONLY` (=0 restores shipped behavior). Measured in BENCHMARK_REPORT.md §warm, see AUDIT_REPORT F3. |

## Proxy

| Claim | Source | Result |
|---|---|---|
| "Response comes back essentially untouched... without rewriting model output" | README | Output filter inactive with default env → byte-identical SSE passthrough VERIFIED incl. arbitrary chunk splits and multibyte UTF-8 split mid-character (probe 2026-10-31). Active only when `SLIMTOKEN_MAX_TOKENS`/`SLIMTOKEN_STOP`/`SLIMTOKEN_FILLER` set. **Skill text "SLIMTOKEN_FILLER — on by default" is WRONG**: `output_filter.from_env()` returns None (inactive) with no env. |
| Overhead "a few milliseconds" | README | Measured: see BENCHMARK_REPORT §proxy overhead (single-digit ms through proxy vs direct on mock upstream). |
| Anthropic / OpenAI / Ollama formats | README | Anthropic messages + OpenAI chat & Ollama adapters VERIFIED (`test_adapters`, multimodal roundtrip). Endpoint coverage per adapters module; streaming OpenAI deltas covered (`test_output_filter_openai_delta`). |
| Correct HTTP framing, no duplicate upstream request, error passthrough | implied | VERIFIED (probe): 500 body forwarded, single upstream call, dynamic-port probe. Auth headers forwarded (value preserved, name case lowercased by httpx). |
| Metrics do not leak secrets or request content | privacy promise | VERIFIED on live proxy: `/metrics` contains counters/latency only. |
| Strips `grammar` for llama-server | tests | VERIFIED (`test_proxy_e2e`). |
| HTTP/2 opt-in (`SLIMTOKEN_HTTP2`) | README | UNTESTED-HERE (needs an h2 upstream; low value for local backends) |

## Artifacts & memory & MCP

| Claim | Source | Result |
|---|---|---|
| Every dropped byte stored under sha256 digest; `recall` exact | README | Round-trip VERIFIED; traversal/hostile-digest inputs rejected; missing → None. **BUT `recall` of a stored-but-later-corrupted blob returns the corrupted bytes with no digest check → BROKEN (F1), fixed in this branch.** | 
| Artifact files private | "writes are local files, nothing leaves the machine" | PARTIAL: default perms 0664 umask-dependent → world/group readable (F2, hardened). |
| Memory store hot/warm/cold, atomic, integrity, restart-safe | README | VERIFIED by `test_memory` (81 tests incl. integrity + failure paths). |
| MCP tools servers + lazy-MCP stubs | README | VERIFIED (`test_mcp_server`, Lazy MCP smoke); lazy-MCP config isolated default; no servers installed by default confirmed. |
| CLI install/uninstall rewrites ANTHROPIC_BASE_URL, idempotent | README | VERIFIED (`test_install_uninstall`, `test_uninstall_no_duplicate`). |

## Packaging (details in AUDIT_REPORT F5–F7)

| Claim | Source | Result |
|---|---|---|
| CPython 3.13 Linux x86_64 only, no source fallback | README Install | VERIFIED (wheel is platform-tagged; requires-python >=3.13). Wheel build/tagging validated in Phase 4. |
| Docker image publish | workflows | **Publish workflow runs zero tests** and no build job exists (F6). |