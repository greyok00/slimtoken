# Changelog

All notable changes to slimtoken. Dates are owner-local (MST).

## 0.8.0 — 2026-11-08 — "savings you can prove, lossy stages you can't have back"

### Removed at source (owner order 2026-11-08)
- **tool_compress** — the lossy tool-result compressor is gone from the code
  paths entirely: no `MinifyConfig` field, no pipeline branch, no CLI
  `--tool-compress`, no mode enables it, no env var resurrects it. History:
  it skeletonized the *current* turn's tool results, the model answered from
  evidence it could no longer read, and agent tasks looped (proven 2026-09-27).
- **minify_dom (pipeline stage)** — no longer exists in `MinifyConfig` or the
  request pipeline; `dom_pruner.prune_dom()` remains available standalone.
- **SLIMTOKEN_FILLER output stripping** — `_env_filler()` removed; the model's
  streamed output now reaches the client byte-verbatim in all configurations.
- **SLIMTOKEN_BLOCK_TOKENS output rewriting** — `_bool_env_block()` removed;
  bare sentinel words (`endoftext`, `fim_*`, …) are never rewritten anywhere —
  not in code identifiers, not in string literals, nowhere.

### Added
- **`slimtoken savings`** CLI command — prints the hard facts from a running
  proxy's stats ledger (`SLIMTOKEN_STATS_FILE`): requests minified, tokens
  in/out/saved, mean saved %, recent-window trend. `--json` for machines.
- Guard tests: `tool_compress` cannot be re-enabled by env; tool results stay
  byte-verbatim even when the removed env vars are set; output filter is inert
  without the max-tokens/stop knobs.

### Changed
- Version guard: stats files keep the `tool_compressed` counter field (always
  0) so downstream readers of the JSON schema don't break.
- `slimtoken modes` output no longer advertises removed stages.

## 0.7.2 — earlier
- Initial portfolio release: pure-Python token optimization and memory layer,
  readable source, test suite, CI. Dedup of byte-identical tool results,
  distillation of old assistant prose (code fences verbatim), tool-schema and
  system-prompt minification, marker-backed budget drops with `recall`.
