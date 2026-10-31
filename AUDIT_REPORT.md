# AUDIT REPORT — slimtoken 0.7.1

Audited 2026-10-31 · base commit `ac8bcd3` (main) · branch `audit/p0-cache-20261031` ·
CPython 3.13.5, Parrot Linux 6.17, x86_64, httpx 0.28.1 / orjson 3.13.0 / xxhash 4.0.2 /
tiktoken 0.14.0 / pytest 9.2.0. Full environment: `~/tmp/st-audit/results/env_manifest.txt`.

Checkout state: only Cython `.so` modules plus `__init__.py`/`_deps.py`; no `.py`/`.pyx`
sources exist anywhere reachable (see "Source-repair constraint" below). Baseline suite:
**154 passed / 0 failed in 8.07 s** (`~/tmp/st-venv/bin/pytest tests/`), collection verified.
Findings marked SHIPPED FIX have an implementation + regression test in this branch;
BLOCKED items list exactly what is missing.

## Findings

### F1 · P1 · SHIPPED FIX — artifact recall returns corrupted bytes unchecked
**Evidence.** `artifacts.store(b"X",...)` → overwrite the blob on disk →
`artifacts.recall(digest)` returned the corrupted content. README promises digest
validation and "reversible by construction"; a truncated/edited blob silently passed.
Reproducer: `tests/test_audit_fixes.py::test_artifact_recall_rejects_corrupted_blob` (fails
before the fix, passes after).
**Fix.** `src/slimtoken/_artifacts_fix.py` wraps the compiled module: `get`/`recall`/
`get_bytes` recompute sha256 and reject mismatches with a stderr warning; `store` creates
blobs 0600 under a 0700 dir (privacy, F2). Storage format unchanged; old artifacts stay
readable.

### F2 · P2 · SHIPPED FIX — artifact blobs were group/world-readable
Stored tool output (possibly private) inherited the umask (0664). Fixed in the same shim;
pinned by `test_artifact_blob_permissions_owner_only`.

### F3 · P1 · SHIPPED FIX — default dedup breaks exact-prefix prompt caching (the user's suspected "reverting back in time")
**Evidence.** The shipped stage stubs the OLDER copy ("identical to a later tool_result"),
so the first request after a re-read REWRITES an earlier message. Anthropic documents that
any byte change inside the cached prefix invalidates the cached segment from that point.
Reproduced at pipeline level: request 2 stubs message 1 of a growing session — the message
1 bytes differ between request 1 and request 2. Confirmed consequence, not hypothesis:
the backend report for cache_reuse-0 (exact-prefix) workloads — see BENCHMARK_REPORT.md
§warm — shows the prefix invalidated exactly on the duplicate-appearing request.
**Fix.** `src/slimtoken/_pipeline_fix.py` implements append-only dedup: FIRST copy stays
byte-exact, LATER duplicates become stubs; prefix never changes. Env:
`SLIMTOKEN_DEDUP_APPEND_ONLY=1` default (on); `=0` restores shipped behavior. Regression:
`tests/test_audit_fixes.py::test_append_only_*` (9 tests incl. pair-safety and env toggle).
**Note on the shipped behavior**: the old-copy direction is not "wrong" semantically — all
information is still in-context — but it silently costs a full prompt reprocess on every
new duplicate, which matches the slowdown symptoms the proxy user observed.

### F4 · P1 · SHIPPED FIX — wheel was tagged `py3-none-any` while shipping CPython-3.13 x86-64 `.so`s
**Evidence.** `python -m build` produced `slimtoken-0.7.1-py3-none-any.whl`` (WHEEL Tag:
`py3-none-any`, Root-Is-Purelib: true) with 48 binary modules inside; pip will install it
on any OS/interpreter ≥3.13 and imports then fail.
**Fix.** `scripts/retag_wheel.py` derives the tag from the shipped `.so` filenames
(`cp313-cp313-linux_x86_64`), patches `WHEEL` + `RECORD`, renames, and hard-fails on
mixed/no binaries. Verified: retagged wheel installs and imports from a clean venv at /tmp.

### F5 · P2 · SHIPPED FIX — publish workflow ran zero checks; no CI existed
`publish.yml` downloads the release wheel and builds/pushes a Docker image with no tests,
no build, no wheel validation. Added `.github/workflows/test.yml` (suite + build + retag +
platform-tag assertion + install-from-wheel smoke from outside checkout) and a `needs:
tests` gate on the `image` job.

### F6 · P2 · VERIFIED, NO FIX NEEDED — proxy streaming/framing/security is sound
Probe (dynamic ports, isolated dirs; `~/tmp/st-audit/results/probe1.txt`): SSE passthrough
byte-identical with upstream chunk splits and a multibyte UTF-8 char split mid-codepoint;
500 responses forwarded verbatim with exactly one upstream request (no retry duplication);
auth headers forwarded; `/metrics` contains counters/latency only (no secrets, no request
text). Output filter is inactive with default env, so "response comes back untouched"
holds by default — the `slimtoken-optimizer` skill text claiming SLIMTOKEN_FILLER is "on
by default" is wrong and was corrected in the skill file.

### F7 · P2 · VERIFIED (docs corrected) — README overstated what the default rewrites
The shipped proxy `enabled_stages` = `{dedup, distill}` (keep_last 4): tool schemas,
system prompt, and message bodies are NOT minified unless `SLIMTOKEN_MINIFY_<STAGE>=1`.
The pipeline library's `MinifyConfig()` default is broader (`system/dedup/messages/
distill/tools`, keep_last 8). The README says every request is rewritten across all those
stages. Corrected README; the `code` mode table in `slimtoken modes` matches the proxy.

### F8 · P3 · DOCUMENTED — proxy overhead claim holds; stats honesty is acceptable
Measured against a mock upstream (n=40/condition): direct 0.5 ms; passthrough ~4.3 ms;
default code mode 4.6–6.5 ms (dedup hashing of a 42 KB body ≈ +2 ms); realtime 4.5–8.2 ms.
README's "few milliseconds" is accurate. A 500-char stub on a small payload can raise
estimated token count (CLI honestly printed −13.7%); documented in CLAIMS_MATRIX.

### F9 · P2 · SHIPPED FIX — memory MCP server had no working invocation
`python -m slimtoken.memory.mcp_server` fails ("No code object available") because the
module is a compiled `.so`, and no console script existed for it — the memory MCP server
was unreachable. Fixed: added the `slimtoken-memory-mcp` console entry point in
`pyproject.toml` (the module exposes `main()`); handshake verified (serverInfo
`slimtoken-memory 0.4.2`).

### F10 · P3 · DOCUMENTED (not fixed — no source) — `slimtoken latency` can stall with no output
Against an idle, slow CPU backend the CLI `latency` probe produced no output for 30+
minutes and could not be bounded by the calling process's 300 s timeout (the timeout
never fired; the probe had to be killed). On a fast backend it presumably completes,
but a fixed-size probe against a slow backend gives no feedback and no timeout
protection. Recorded upstream-work-list item; not repaired (compiled module, repair
would mean wrapping CLI behavior in a shim, which was judged out of proportion for P3).

## Source-repair constraint (recorded honestly)
The compiled stages shipped with no `.py`/`.pyx` sources anywhere in the release
history — the local build script (`~/tmp/cxbuild_slimtoken.py`) *deleted* the
sources after compiling. A later recovery pass found a **partial source snapshot**
inside the vendored agent copies (`~/greyagent/vendor/slimtoken/dist/slimtoken-0.7.1.tar.gz`
sdist: 52 `.py` modules) plus the same modules as `.pyc` — but the snapshot
**drifts from the shipped 0.7.1 binaries** (its output filter activates by default,
its distill/compress stages fail code-protection tests, and the `artifacts` module
is absent entirely), so rebuilding all 48 modules from it would regress
verified behavior. Verdict: the shipped `.so` modules stay; the fix modules were
decommented, minified, and **Cython-compiled into the release** (see
`scripts/decomment.py`; the wheel-build CI step does the same), and their
readable `.py` sources stay committed so the source-deletion mistake is not
repeated. The recovered snapshot remains at the vendor path for any future
attempt to reconstruct drifted modules.

## Verification summary
| Item | Status |
|---|---|
| Baseline suite on main | PASS (154/154) |
| Suite + 11 new regression tests after fixes | PASS (165/165; 6/10 new tests failed before wiring — kept as TDD evidence) |
| Wheel retag + clean-venv install/import | PASS |
| Proxy security/streaming probe | PASS (7/7 after fixing the probe's own case-bug) |
| Memory-MCP console entry point handshake | PASS |
| Benchmarks (cold/warm/A/B/C/D/N) | see BENCHMARK_REPORT.md |
| Real-backend speed claims | measured on an isolated CPU-only instance of the machine's existing greymini-server binary + existing local Qwen3.5-4B-Q4_K_M GGUF (no downloads, no GPU changes, no shared processes touched) |
| HTTP/2 forwarding (`SLIMTOKEN_HTTP2`) | NOT RUN — needs an h2 upstream; low value for local backends |
| Cloud/backend-provider tests (Anthropic/OpenAI real APIs) | SKIPPED BY DESIGN — rule: no external credits |