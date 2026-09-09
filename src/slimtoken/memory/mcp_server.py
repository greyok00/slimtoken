
from __future__ import annotations

import json
import sys
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError as e:
    raise SystemExit(
        "slimtoken.memory.mcp_server requires the `mcp` package. "
        "Install with: pip install slimtoken[mcp]"
    ) from e

from .engine import (
    append, read_last, search,
    write_cold, cold_get, cold_list,
    HOT_DIR, COLD_DIR,
)
from .stats import stats as _stats
from .integrity import check as _integrity_check
from .scheduler import parse as _cron_parse, Schedule as _Schedule
from .workflow import Workflow as _Workflow
from .dag import Task as _Task, StepKind as _StepKind



app = MCPServer(
    name="slimtoken-memory",
    version="0.4.2",
    instructions=(
        "CortexLLM memory layer. The default per-prompt call is "
        "memory_thin_append(content=str, role='user', platform='<name>'). "
        "Hard rule (2026-08-11): no caps. Hot grows unbounded. "
        "Use memory_search for keyword lookups. Use memory_write with "
        "tier='cold' + category=<name> for curated long-term facts. "
        "Single working tier (hot) since v0.4.2 — warm was removed."
    ),
)



def _payload(value: Any) -> dict:

    if isinstance(value, (dict, list)):
        return {"text": json.dumps(value, indent=2, ensure_ascii=False)}
    return {"text": str(value)}



def _tool_thin_append(content: str, role: str = "user",
                      platform: str = "default",
                      tiers: str = "hot") -> dict:
    tier_tuple = tuple(t.strip() for t in tiers.split(",") if t.strip()) or ("hot",)
    p = append(role=role, content=content, platform=platform, tiers=tier_tuple)
    return {"status": "ok", "path": str(p)}


def _tool_thin_read(n: int = 5, platform: str = "default",
                    tier: str = "hot") -> list:
    return read_last(n, platform=platform)


def _tool_read(tier: str, platform: str = "default", category: str = "",
               n: int = 50) -> Any:
    if tier == "cold":
        if not category:
            return {"error": "category required for cold tier"}
        return cold_get(category)
    if tier == "hot":
        return read_last(n, platform=platform)
    return {"error": f"unknown tier {tier!r}"}


def _tool_write(tier: str, content: str, platform: str = "default",
                category: str = "", role: str = "user",
                source: str = "default", replace: bool = False) -> dict:
    if tier == "cold":
        if not category:
            return {"error": "category required for cold tier"}
        try:
            knowledge = json.loads(content)
        except (ValueError, TypeError):
            knowledge = {"fact": content}
        p = write_cold(category, knowledge, source=source, replace=replace)
        return {"status": "ok", "path": str(p)}
    p = append(role=role, content=content, platform=platform, tiers=("hot",))
    return {"status": "ok", "path": str(p)}


def _tool_search(query: str, tier: str = "hot", platform: str = "default",
                 limit: int = 10) -> list:
    return search(query, tier=tier, platform=platform, limit=limit)


def _tool_clear(tier: str, platform: str = "default", category: str = "") -> dict:
    cleared: list[str] = []
    if tier in ("hot", "all"):
        target = HOT_DIR / f"{platform}.jsonl"
        if target.exists():
            target.unlink()
            cleared.append(str(target))
    if tier in ("cold", "all"):
        if not category:
            if tier == "cold":
                return {"error": "category required to clear cold"}
        else:
            target = COLD_DIR / f"{category}.json"
            if target.exists():
                target.unlink()
                cleared.append(str(target))
    return {"status": "ok", "cleared": cleared}


def _tool_cold_list() -> list:
    return cold_list()


def _tool_stats(platform: str = "") -> dict:

    return _stats(platform=platform or None)


def _tool_integrity(tier: str = "hot", platform: str = "default") -> dict:

    if tier == "hot":
        path = HOT_DIR / f"{platform}.jsonl"
    elif tier == "cold":
        return {"error": "cold is JSON per category; use memory_cold_list"}
    else:
        return {"error": "tier must be 'hot' or 'cold'"}
    return _integrity_check(str(path))


def _tool_cron_parse(expr: str, iso_now: str = "") -> dict:

    from datetime import datetime
    if iso_now:
        try:
            now = datetime.fromisoformat(iso_now)
        except ValueError as e:
            return {"error": f"invalid iso_now: {e}"}
    else:
        now = datetime.now()
    try:
        due = _cron_parse(expr, now=now)
    except ValueError as e:
        return {"error": str(e)}
    return {"expr": expr, "now": now.isoformat(), "due": due}


def _tool_workflow_run(tasks: list, dir: str = "/tmp/slimtoken-memory-workflow") -> dict:

    if not isinstance(tasks, list):
        return {"error": "tasks must be a list of dicts"}
    plan = []
    for t in tasks:
        if not isinstance(t, dict) or "id" not in t or "kind" not in t:
            return {"error": "each task needs id and kind"}
        plan.append(_Task(
            id=t["id"],
            name=t.get("name", t["id"]),
            kind=_StepKind[t["kind"]] if isinstance(t["kind"], str) else t["kind"],
            prompt=t.get("prompt", ""),
            depends_on=t.get("depends_on", []),
        ))
    wf = _Workflow(dir, executor=lambda task: True)
    return wf.run(plan)


def _tool_workflow_status(dir: str = "/tmp/slimtoken-memory-workflow") -> dict:

    wf = _Workflow(dir, executor=lambda task: True)
    return wf.get_status()



@app.tool(
    name="memory_thin_append",
    description=(
        "Atomic append one message to slimtoken-memory hot memory. "
        "No caps. Every prompt/response should call this. "
        "Fires the hard-rule (2026-08-11) no-cap policy."
    ),
)
async def memory_thin_append(content: str, role: str = "user",
                            platform: str = "default",
                            tiers: str = "hot") -> dict:
    return _tool_thin_append(content, role, platform, tiers)


@app.tool(
    name="memory_thin_read",
    description="Tail-read the last N entries from hot memory.",
)
async def memory_thin_read(n: int = 5, platform: str = "default",
                           tier: str = "hot") -> list:
    return _tool_thin_read(n, platform, tier)


@app.tool(
    name="memory_read",
    description="Read memory. Cold: by category. Hot: tail (use memory_thin_read).",
)
async def memory_read(tier: str, platform: str = "default",
                      category: str = "", n: int = 50) -> object:
    return _tool_read(tier, platform, category, n)


@app.tool(
    name="memory_write",
    description="Write to memory. Cold: with category. Hot: thin-append. "
                "For cold, set replace=true to overwrite the whole category "
                "with a single entry (use for singleton categories like the "
                "task list so repeated writes don't accumulate snapshots).",
)
async def memory_write(tier: str, content: str, platform: str = "default",
                       category: str = "", role: str = "user",
                       source: str = "default", replace: bool = False) -> dict:
    return _tool_write(tier, content, platform, category, role, source, replace)


@app.tool(
    name="memory_search",
    description="Keyword search across hot memory (linear scan).",
)
async def memory_search(query: str, tier: str = "hot",
                        platform: str = "default",
                        limit: int = 10) -> list:
    return _tool_search(query, tier, platform, limit)


@app.tool(
    name="memory_clear",
    description="DESTRUCTIVE. Clear a tier. Use only for cleanup.",
)
async def memory_clear(tier: str, platform: str = "default",
                       category: str = "") -> dict:
    return _tool_clear(tier, platform, category)


@app.tool(
    name="memory_cold_list",
    description="List all cold categories.",
)
async def memory_cold_list() -> list:
    return _tool_cold_list()


@app.tool(
    name="memory_stats",
    description=(
        "Report memory stats: hot/cold bytes + entries, cold category count, "
        "estimated tokens. Pass empty platform string to aggregate across all."
    ),
)
async def memory_stats(platform: str = "") -> dict:
    return _tool_stats(platform)


@app.tool(
    name="memory_integrity",
    description=(
        "Verify a hot NDJSON file: ok, line_count, valid_lines, "
        "bad_lines, truncated, last_ts, last_role. Use to detect corrupted "
        "writes or partial appends."
    ),
)
async def memory_integrity(tier: str = "hot", platform: str = "default") -> dict:
    return _tool_integrity(tier, platform)


@app.tool(
    name="cron_parse",
    description=(
        "Test whether a 5-field cron expression is due at a given ISO instant. "
        "Supports *, N, N,M, N-M, */N, and @hourly/@daily/@weekly/@monthly/@yearly aliases. "
        "Pass empty iso_now to use the current time."
    ),
)
async def cron_parse(expr: str, iso_now: str = "") -> dict:
    return _tool_cron_parse(expr, iso_now)


@app.tool(
    name="workflow_run",
    description=(
        "Execute a plan of tasks in dependency order. ``tasks`` is a list of "
        "{id, name, kind, prompt, depends_on} dicts; kind is one of COMMAND/LLM/"
        "WEBHOOK/NOTIFY. Executor is a no-op for now — the engine shell handles "
        "topo sort, batching, persistence, and progress events."
    ),
)
async def workflow_run(tasks: list, dir: str = "/tmp/slimtoken-memory-workflow") -> dict:
    return _tool_workflow_run(tasks, dir)


@app.tool(
    name="workflow_status",
    description="Return the current workflow state from disk at ``dir``.",
)
async def workflow_status(dir: str = "/tmp/slimtoken-memory-workflow") -> dict:
    return _tool_workflow_status(dir)



def main() -> int:

    import asyncio
    asyncio.run(app.run_stdio_async())
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["app", "main"]