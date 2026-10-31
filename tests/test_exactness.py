"""Coding-safety exactness tests (work order Phase 3).

Each test enforces one promise of coding-safe mode:
- streamed model output passes byte-identically when nothing is opted in;
- code, diffs, JSON, and logs inside compressed content stay exact;
- anything dropped from a request is retrievable from the artifact store
  by the digest published in its marker.
These tests never bind ports; they exercise pure functions.
"""
import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from slimtoken.output_filter import from_env  # noqa: E402


_OUTPUT_VARS = ("SLIMTOKEN_FILLER", "SLIMTOKEN_BLOCK_TOKENS",
                "SLIMTOKEN_MAX_TOKENS", "SLIMTOKEN_STOP")


def _clear_output_env(monkeypatch):
    for var in _OUTPUT_VARS:
        monkeypatch.delenv(var, raising=False)


def _sse(text):
    payload = json.dumps({"delta": {"text": text}})
    return "data: " + payload + "\n\n"


CODE_WITH_SPECIAL = "```python\nconst fim_prefix = 1;\nconst fim_middle = 2;\n```\n"


def test_output_filter_inert_with_no_env(monkeypatch):
    _clear_output_env(monkeypatch)
    assert from_env() is None


def test_streamed_output_byte_identical_by_default(monkeypatch):
    """No env knobs set: every byte of the model's stream is relayed.

    Kills the 'const fim_prefix = 1;' -> 'const [fim_prefix] = 1;' mangle
    and the lead-in filler strip in one promise: nothing is touched.
    """
    chunks = [
        _sse("Sure, ").encode(),
        _sse("Here is the code:\n").encode(),
        _sse(CODE_WITH_SPECIAL).encode(),
        _sse("endofprompt\n").encode(),
    ]
    _clear_output_env(monkeypatch)
    flt = from_env()
    out = b""
    for chunk in chunks:
        out += flt.feed(chunk) if flt is not None else chunk
    if flt is not None:
        out += flt.finish()
    assert out == b"".join(chunks)


def test_artifact_store_roundtrip_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import artifacts
    first = artifacts.store("the original text", kind="distill",
                            origin="distill_old_turns")
    again = artifacts.store("the original text", kind="distill",
                            origin="distill_old_turns")
    assert first == again
    assert len(first) == 64
    assert artifacts.get(first) == "the original text"


def test_artifact_recall_slices(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import artifacts
    d = artifacts.store("0123456789", kind="budget", origin="token_budget")
    assert artifacts.recall(d, offset=2, limit=3) == "234"
    assert artifacts.recall(d, offset=8) == "89"
    assert artifacts.recall(d, offset=0) == "0123456789"


def test_artifact_index_records(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import artifacts
    d = artifacts.store("hello world content", kind="dedup",
                        origin="dedup_tool_results")
    lines = (tmp_path / "arts" / "index.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["digest"] == d
    assert rec["kind"] == "dedup"
    assert rec["bytes"] == 19


def test_artifact_store_failure_returns_none(monkeypatch):
    # unwritable location -> store() returns None so the caller keeps
    # the content whole instead of dropping it unrecoverably
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", "/proc/slimtoken-nope/artifacts")
    from slimtoken import artifacts
    assert artifacts.store("x" * 10) is None


def test_artifact_rejects_bad_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import artifacts
    assert artifacts.get("not-a-digest") is None
    assert artifacts.get("../escape") is None


def test_cli_recall_prints_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import artifacts
    from slimtoken import cli
    d = artifacts.store("the exact original text", kind="distill")
    rc = cli.main(["recall", d])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "the exact original text\n"


def test_cli_recall_missing_digest_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken import cli
    rc = cli.main(["recall", "0" * 64])
    assert rc == 1


# --- dedup: byte-identical duplicates stub, but the stub keeps a digest ---

def _dup_body():
    original = "X" * 400
    return {"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": original}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": original}]},
    ]}


def _dedup_cfg():
    from slimtoken.pipeline import MinifyConfig
    return MinifyConfig(enabled_stages={"dedup"}, keep_last=8,
                        dedup_min_chars=200, token_budget=0)


def _extract_digest(marker_text):
    m = re.search(r"digest ([0-9a-f]{64})", marker_text)
    return m.group(1) if m else None


def test_dedup_stub_carries_retrievable_digest(tmp_path, monkeypatch):
    # 0.7.2: append-only dedup. The LATER copy becomes the stub and the FIRST
    # copy stays byte-exact, so exact-prefix prompt caches never see earlier
    # messages rewritten. The dropped copy must still be recallable verbatim.
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.pipeline import minify_request
    body = json.loads(json.dumps(_dup_body()))
    nb, stats = minify_request(body, _dedup_cfg())
    first = nb["messages"][0]["content"][0]["content"]
    assert first == "X" * 400
    stub = nb["messages"][2]["content"][0]["content"]
    assert isinstance(stub, str) and "recall" in stub and "digest" in stub
    digest = _extract_digest(stub)
    assert digest, stub
    from slimtoken import artifacts
    assert artifacts.get(digest) == "X" * 400


def test_dedup_without_store_keeps_original(tmp_path, monkeypatch):
    # if the original cannot be stored, it must not be dropped
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", "/proc/slimtoken-nope/artifacts")
    from slimtoken.pipeline import minify_request
    nb, stats = minify_request(_dup_body(), _dedup_cfg())
    first = nb["messages"][0]["content"][0]["content"]
    assert first == "X" * 400


# --- distill: code-like prose is never elided; elision is artifact-backed ---

def test_distill_never_touches_json_prose(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.distill_old_turns import distill_text
    text = ("I checked the response and it looks fine. " + "more words over time. " * 300 +
            ' The payload was {"results": [1, 2, 3], "ok": true} and the request returned 200.')
    assert distill_text(text, 200) == text


def test_distill_inline_code_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.distill_old_turns import distill_text
    text = "Used the helper `json.dumps` and then it worked as expected. " * 60
    assert distill_text(text, 200) == text


def test_distill_indented_code_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.distill_old_turns import distill_text
    text = ("Narrative before the code follows here. " + "fine " * 40 +
            "\n\n    indented_line_starting_with_spaces()\n" +
            "Trailing narrative that should stay intact as prose. " + "more " * 40)
    assert distill_text(text, 200) == text


def test_distill_elision_is_artifact_backed(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.distill_old_turns import distill_text
    original = ("The service was slow to start, so I checked the logs and "
                "restarted it twice before the build cleared. " * 40)
    out = distill_text(original, 200)
    assert out != original
    digest = _extract_digest(out)
    assert digest, out
    from slimtoken import artifacts
    assert artifacts.get(digest) == original


# --- budget: dropped messages are stored, not lost ---

def test_budget_dropped_messages_stored_and_recalled(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.token_budget import enforce_budget
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"turn number {i:02d} with filler " + "word " * 150}
            for i in range(30)]
    body = {"messages": msgs}
    nb = enforce_budget(body, 800, keep_last=2)
    dropped = len(body["messages"]) - len(nb["messages"])
    assert dropped > 0
    from slimtoken import artifacts
    lines = (tmp_path / "arts" / "index.jsonl").read_text().strip().splitlines()
    digests = [json.loads(l)["digest"] for l in lines if json.loads(l)["kind"] == "budget"]
    assert len(digests) == dropped
    originals = {"".join(json.dumps(m, ensure_ascii=False, default=str))
                 for m in body["messages"][:dropped]}
    for d in digests:
        assert artifacts.get(d) in originals


# --- tool_compress: dict JSON gets head/tail + markers; originals stored ---

def test_compress_json_dict_head_tail_marker():
    from slimtoken.tool_result_compress import compress_text
    data = {f"key{i:03d}": "value" * 20 for i in range(300)}
    out = compress_text(json.dumps(data))
    assert out is not None
    assert "omitted" in out and "first" in out and "last" in out


def test_compress_json_few_records_keeps_content_whole():
    from slimtoken.tool_result_compress import compress_text
    data = {"a": "x" * 3000, "b": "y" * 3000}
    pretty = json.dumps(data, indent=2)
    out = compress_text(pretty)
    assert out is not None
    assert "x" * 3000 in out and "y" * 3000 in out
    assert "truncated" not in out


def test_compress_stores_original_and_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.tool_result_compress import compress_text
    text = json.dumps({f"key{i:03d}": "value" * 20 for i in range(300)})
    out = compress_text(text)
    digest = _extract_digest(out)
    assert digest, out
    from slimtoken import artifacts
    assert artifacts.get(digest) == text


def test_compress_without_store_keeps_original(monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", "/proc/slimtoken-nope/artifacts")
    from slimtoken.tool_result_compress import compress_text
    text = json.dumps({f"key{i:03d}": "value" * 20 for i in range(300)})
    assert compress_text(text) is None


# --- dom prune: keyed by content, capped only when retrievable ---

def test_dom_prune_keyed_by_content_not_shared(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.pipeline import MinifyConfig, minify_request
    from slimtoken.dom_pruner import clear_dom_cache
    clear_dom_cache()
    h1 = "<html><body>" + "<div>alpha row</div>" * 1200 + "</body></html>"
    h2 = "<html><body>" + "<div>beta row</div>" * 1200 + "</body></html>"
    body = {"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": h1}]},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": h2}]},
    ]}
    cfg = MinifyConfig(enabled_stages=set(), minify_dom=True, token_budget=0)
    nb, stats = minify_request(copy.deepcopy(body), cfg)
    a = nb["messages"][0]["content"][0]["content"]
    b = nb["messages"][2]["content"][0]["content"]
    assert "alpha" in a and "beta" in b
    assert a != b
    clear_dom_cache()


def test_dom_cap_stores_collapsed_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path / "arts"))
    from slimtoken.dom_pruner import prune_dom, clear_dom_cache
    clear_dom_cache()
    html = "<html><body>" + "<div>row of content here</div>" * 2000 + "</body></html>"
    pruned = prune_dom(html, "sess-dom-cap")
    digest = _extract_digest(pruned)
    assert digest, pruned
    from slimtoken import artifacts
    assert artifacts.get(digest) is not None
    clear_dom_cache()


# --- pytest honesty: a check() failure must fail the pytest run ---

def _load_check_module():
    import importlib.util
    path = Path(__file__).resolve().parent / "test_all.py"
    spec = importlib.util.spec_from_file_location("slimtoken_test_all_for_check", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_failure_raises_under_pytest():
    mod = _load_check_module()
    mod.os.environ.pop("SLIMTOKEN_FILLER", None)
    with pytest.raises(AssertionError, match="check failed: deliberate"):
        mod.check("deliberate failure for test", False)


def test_check_success_does_not_raise():
    mod = _load_check_module()
    mod.check("must pass for test", True)
    assert mod.PASS >= 1