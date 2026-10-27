slimtoken makes every LLM request smaller before it leaves your machine. It
rewrites tool schemas, system prompts, and message history, deduplicates
byte-identical tool results, distills old turns into extractive summaries, and
prunes to a token budget. A standalone token-optimization and memory layer —
built as its own project rather than a fork of an existing harness — it grew
out of token-reduction work the author wrote for a local agent stack and has
been a separate package since its first public release.

It runs entirely locally, three ways: an asyncio proxy that any
Anthropic/OpenAI/Ollama-compatible client can point at, an MCP server for
tool-capable agents, and a plain Python import. The default "code" mode never
alters a tool result except an exact duplicate — and each dropped duplicate is
stored under a sha256 digest, so `slimtoken recall` brings back the exact
original. Memory is provided by slimtoken.memory: an uncapped hot/warm/cold
store at ~/.slimtoken/memory with atomic writes, integrity checks, a
scheduler, and a loop guard, exposed over MCP or direct import. Every
compression pass is artifact-backed and refuses to replace code-like content
it cannot store, so nothing is silently lost.

The status line is one `slimtoken modes` command: it prints the two modes,
which stages each enables, and — with `--measure` — the real token table
computed by the bundled cl100k tokenizer on your own machine. `slimtoken
latency` times a single request end-to-end through a running proxy.

For developers who want the agent harness they already have and care that
each call it makes costs less: point it at the proxy and keep the harness.

Licensed under the MIT License.