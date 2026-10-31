# TEST RESULTS — slimtoken audit (2026-10-31, branch `audit/p0-cache-20261031`)

All runs use `~/tmp/st-venv/bin/pytest` (CPython 3.13.5) against the checkout.
Raw outputs: `~/tmp/st-audit/results/baseline_pytest.txt`, `pytest_after_fix.txt`.

## Suite progression

| Run | Result | Notes |
|---|---|---|
| Baseline on main @ ac8bcd3, pre-changes | **154 passed, 0 failed** (8.07 s) | healthy starting point |
| New regression tests added (`tests/test_audit_fixes.py`), fixes NOT yet wired | 6/10 new tests FAIL (expected; kept as TDD evidence — proves each test can fail) | 3 artifacts-shim tests + append-only tests fail against shipped behavior |
| After wiring (`slimtoken/__init__.py` registers the repair shims) | 7 failed / 158 passed | exposed 2 real wiring bugs (see "Wiring shakedown" below) |
| After wiring fixes + updating 2 shipped tests that encoded the *old* dedup direction | 5 failed / 164 passed | one remaining shim signature gap |
| Final | **165 passed, 0 failed** (7.48 s) | 154 baseline-relevant + 11 new regression tests |

## Wiring shakedown (real bugs the suite caught in the fix code itself)

1. The artifacts shim replaced `sys.modules["slimtoken.artifacts"]` but left the
   package attribute pointing at the compiled module, so `import slimtoken.artifacts`
   still resolved native code. Fixed by setting `slimtoken.artifacts` on the package
   (`_artifacts_fix.apply_fix`).
2. The shim's `recall(digest)` dropped the native `recall(digest, offset, limit)`
   slicing API — restored (offset/limit slice the decoded text, same semantics,
   verified by `test_artifact_recall_slices`).
3. Two of my own regression tests had fixture bugs (`tool_use` blocks misread as
   results; a compound assertion that mis-parsed plain-string form) — fixed the tests,
   not the product.

## Deliberate shipped-test updates (policy change, not assertion-weakening)

`dedup` now stubs the LATER duplicate (append-only, cache-safe) instead of the OLDER
copy. Two shipped tests asserted the old direction and were updated to the new policy
with *stronger* assertions, not weaker ones:

- `test_exactness.py::test_dedup_stub_carries_retrievable_digest` — now asserts the
  first copy is byte-exact `X*400`, the later copy carries the digest stub, and the
  digest recalls the dropped copy verbatim (original only checked "recall in stub").
- `test_mcp_server.py::test_optimize_openai_format` — replaced "newest tool result
  intact" with: first copy intact, later copy stubbed pointing backward ("an earlier
  tool_result"), stub carries `slimtoken recall` marker + 64-hex digest and the
  digest retrieves `big` verbatim.

Legacy shipped behavior remains reachable and pinned by
`tests/test_audit_fixes.py` via `SLIMTOKEN_DEDUP_APPEND_ONLY=0`.

## Final ledger

| Check | Status |
|---|---|
| Full suite with all fixes wired | **PASS — 165/165** |
| New regression tests proven capable of failing (pre-wiring run) | PASS (6/10 failed as designed) |
| Append-only prefix byte-stability, string-form, min-chars, toggle=0 restores legacy, pair-safety | PASS (`tests/test_audit_fixes.py`) |
| Artifact corruption/truncation rejection, owner-only perms | PASS |
| Wheel retag → install → import from clean venv outside checkout | PASS |
| Memory-MCP console entry point + MCP handshake | PASS |
| Proxy streaming/framing/security probe suite | PASS (7/7) |
| `slimtoken latency` probe | INCONCLUSIVE — two attempts: against the busy bench backend it returned 0 samples; against an idle isolated proxy it produced no output within 30 min (its own 300 s timeout also failed to fire — minor CLI robustness issue worth fixing upstream when source exists). The t0–t4 latency path itself is exercised and covered by the proxy probe; per-request latency numbers come from the benchmark harness instead. |
| HTTP/2 (`SLIMTOKEN_HTTP2`) forwarding | NOT RUN — no h2 upstream available; low value for local loopback backends |
| Real Anthropic / OpenAI cloud APIs | SKIPPED BY DESIGN — no external credits rule |
| Source-level restoration of original `.py` pipeline code | BLOCKED — sources were deleted post-Cython-compile (see AUDIT_REPORT); repairs shipped as tested `.py` shims |