"""cli — entry point: slimtoken {serve|config-optimizer|install|uninstall}.

Install/uninstall ONLY touch the ANTHROPIC_BASE_URL line in one shell rc file
(with a marker block + a backup of the prior value). They never touch Claude
Code's settings.json / CLAUDE.md / mcp.json — so uninstall is clean and
reversible: restore the prior BASE_URL (or unset it) and the proxy is gone.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

MARKER_BEGIN = "# >>> slimtoken >>>"
MARKER_END = "# <<< slimtoken <<<"
DEFAULT_URL = "http://127.0.0.1:8181"
STATE_DIR = Path(os.environ.get("SLIMTOKEN_STATE_DIR", str(Path.home() / ".slimtoken")))
PREV_ENV = STATE_DIR / "prev_env"


def _detect_rc(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    return Path.home() / ".bashrc"


def _read_block(rc: Path):
    """Return (before, url_line, after) if marker block present, else (full, None, None)."""
    if not rc.exists():
        return "", None, ""
    text = rc.read_text()
    i = text.find(MARKER_BEGIN)
    if i < 0:
        return text, None, ""
    j = text.find(MARKER_END, i)
    if j < 0:
        return text, None, ""  # malformed → treat as absent
    before = text[:i]
    block = text[i + len(MARKER_BEGIN):j]
    after = text[j + len(MARKER_END):]
    # find the export line inside block
    url_line = None
    for ln in block.splitlines():
        if "ANTHROPIC_BASE_URL=" in ln:
            url_line = ln.strip()
            break
    return before, url_line, after


def cmd_install(args):
    rc = _detect_rc(args.rc)
    url = args.url
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    before, existing_url_line, after = _read_block(rc)
    # Back up a prior BASE_URL that lives OUTSIDE the marker block.
    prior = None
    if existing_url_line is None:
        for ln in before.splitlines() + after.splitlines():
            if "ANTHROPIC_BASE_URL=" in ln and MARKER_BEGIN not in ln:
                try:
                    prior = ln.split("=", 1)[1].strip().strip("'\"")
                except Exception:
                    prior = None
                break
    if prior and not PREV_ENV.exists():
        PREV_ENV.write_text(prior)
    block = f"\n{MARKER_BEGIN}\nexport ANTHROPIC_BASE_URL={url}\n{MARKER_END}\n"
    if existing_url_line is not None:
        # replace the url inside the existing block
        text = rc.read_text()
        import re
        text = re.sub(
            r"(" + MARKER_BEGIN + r".*?ANTHROPIC_BASE_URL=).*?(\n.*?" + MARKER_END + r")",
            r"\g<1>" + url + r"\g<2>", text, flags=re.DOTALL)
        rc.write_text(text)
    else:
        rc.write_text(before.rstrip("\n") + "\n" + block + after.lstrip("\n"))
    print(f"installed: ANTHROPIC_BASE_URL={url}")
    print(f"  written to {rc}")
    print(f"  prior value backed up to {PREV_ENV}" if prior and not PREV_ENV.exists()
          else ("  (no prior ANTHROPIC_BASE_URL to back up)" if not prior
                else f"  (prior backup already at {PREV_ENV})"))
    print(f"restart your shell (or `source {rc}`), then run `slimtoken serve`.")


def cmd_uninstall(args):
    rc = _detect_rc(args.rc)
    if not rc.exists():
        print("nothing to uninstall (no rc file)")
        return 0
    text = rc.read_text()
    i = text.find(MARKER_BEGIN)
    j = text.find(MARKER_END, i) if i >= 0 else -1
    if i < 0 or j < 0:
        print("no slimtoken marker block found — already clean")
    else:
        # remove the marker block (and the surrounding blank line)
        import re
        text = re.sub(re.escape(MARKER_BEGIN) + r".*?" + re.escape(MARKER_END) + r"\n?",
                      "", text, flags=re.DOTALL)
        rc.write_text(text)
        print(f"removed slimtoken block from {rc}")
    # restore prior BASE_URL if we have it
    if PREV_ENV.exists():
        prior = PREV_ENV.read_text().strip()
        if prior:
            with open(rc, "a") as f:
                f.write(f"\nexport ANTHROPIC_BASE_URL={prior}\n")
            print(f"restored prior ANTHROPIC_BASE_URL={prior}")
        PREV_ENV.unlink()
    else:
        print("ANTHROPIC_BASE_URL now unset (Claude Code uses its default).")
    print("pip uninstall slimtoken to remove the package.")
    return 0


def cmd_serve(args):
    # set env from CLI flags, then run the proxy.
    if args.port:
        os.environ["SLIMTOKEN_PORT"] = str(args.port)
    if args.upstream:
        os.environ["SLIMTOKEN_UPSTREAM"] = args.upstream
    from . import proxy
    proxy.main()


def cmd_config_optimizer(args):
    from . import config_optimizer as co
    vram = float(args.vram_gb) if args.vram_gb else None
    rec = co.recommend(
        vram_gb=vram, model_path=args.model,
        model_size_gb=float(args.model_size_gb) if args.model_size_gb else None,
        kv_per_token_bytes=int(args.kv_per_token),
        native_ctx=int(args.native_ctx))
    print(co.format_report(rec))


def cmd_lazy_mcp(args):
    from . import lazy_mcp
    if args.smoke == "smoke":
        return lazy_mcp.smoke()
    if not args.name:
        print("usage: slimtoken lazy-mcp --name SERVER_NAME  (or: slimtoken lazy-mcp smoke)",
              file=sys.stderr)
        return 2
    lazy_mcp.run_server(args.name)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="slimtoken",
                                 description="token-optimization layer for LLM requests")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the optimization proxy")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--upstream", type=str, default=None,
                   help="upstream URL (http://127.0.0.1:8080 or https://api.anthropic.com)")
    s.set_defaults(func=cmd_serve)

    c = sub.add_parser("config-optimizer", help="recommend llama-server args for a GPU+model")
    c.add_argument("--vram-gb", type=float, default=None, help="GPU VRAM (auto-detect if omitted)")
    c.add_argument("--model", type=str, default=None, help="path to .gguf")
    c.add_argument("--model-size-gb", type=float, default=None, help="model size in GB (if no path)")
    c.add_argument("--kv-per-token", type=int, default=5120, help="KV bytes/token (hybrid MoE~5120, dense~8192)")
    c.add_argument("--native-ctx", type=int, default=262144, help="model's max ctx")
    c.set_defaults(func=cmd_config_optimizer)

    i = sub.add_parser("install", help="point ANTHROPIC_BASE_URL at the proxy")
    i.add_argument("--url", type=str, default=DEFAULT_URL)
    i.add_argument("--rc", type=str, default=None, help="shell rc file (auto-detect)")
    i.set_defaults(func=cmd_install)

    u = sub.add_parser("uninstall", help="restore prior ANTHROPIC_BASE_URL + remove block")
    u.add_argument("--rc", type=str, default=None)
    u.set_defaults(func=cmd_uninstall)

    l = sub.add_parser("lazy-mcp", help="run a lazy MCP stub server (one tool per MCP server)")
    l.add_argument("--name", type=str, default=os.environ.get("SLIMTOKEN_LAZY_MCP_NAME", ""),
                   help="MCP server name (from ~/.slimtoken/lazy_mcp.json)")
    l.add_argument("smoke", nargs="?", default=None, help="run 'smoke' to self-test")
    l.set_defaults(func=cmd_lazy_mcp)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())