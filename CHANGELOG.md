# Changelog

All notable changes to slimtoken are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project adheres
to [Semantic Versioning](https://semver.org/).

## [0.3.6] — 2026-08-16

### Added
- **`slimtoken.prompt_reframe`** — new CPU-only module that tightens
  natural-language prompts before they reach a model. Five stages, all
  callable independently or via the bundled `frame_prompt` pipeline:
  `classify_domain`, `reframe_prompt`, `shrink_prompt`, `minify_prompt`,
  `build_system`. Pure stdlib (Python 3.10+), deterministic, dependency
  free. The shrink stage is a TextRank-lite sentence ranker that keeps
  the user's own words so there is no chance of semantic drift.
- **MCP server**: `slimtoken.mcp_server.prompt_reframe_server` —
  stdio JSON-RPC 2.0 server exposing the six reframe primitives as
  the `slimtoken.reframe.*` tool surface. Same wire format as the main
  `slimtoken-mcp`; usable from any host that speaks MCP.
- **Agent Skill**: `skills/prompt-reframe/` — manifest + references
  + a one-shot `scripts/reframe.py` for piping prompts through the
  rewriter from a shell.

### Why
The main proxy already minifies the *request body* (tools, system,
messages, dedup, distill, budget). This release adds a tightener for
the *natural-language intent* of a single user prompt — a different
problem, used at a different point in the pipeline. The rewriter is
dependency-free so it embeds cleanly in batch jobs, CLI tools, MCP
servers, and edge environments that can't afford an LLM call.

### Notes
- No PII, no upstream-tool references; the module is intentionally
  generic.
- Backwards compatible: existing proxy / CLI / MCP / Skill surfaces
  are unchanged. New code lives next to them, not on top of them.
- The TextRank-lite fall-back guarantees that even with no LLM in
  reach, you get a deterministic, intent-preserving shorter prompt.

## [0.3.5] — 2026-08-13
Proxy is the documented default; every-message minification; CLI
tightens; lead README with measured token reduction.

## [0.3.4] — 2026-08-12
Output filter (filler-strip) defaults ON; code-fence-aware content
minify tightened.

## [0.3.3] — 2026-08-11
Initial MIT public release: async proxy + MCP server (8 tools) +
Agent Skill + CLI; Anthropic / OpenAI / Ollama backends; dense + MoE
high-context VRAM presets (4 / 8 / 16 GB); always-on config via raw
`SLIMTOKEN_*` env knobs; bundled orjson / xxhash / tiktoken.

[0.3.6]: https://github.com/greyok00/slimtoken/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/greyok00/slimtoken/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/greyok00/slimtoken/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/greyok00/slimtoken/releases/tag/v0.3.3
