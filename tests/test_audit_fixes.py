"""Regression tests for audit fixes (2026-10-31, branch audit/p0-cache-20261031).

Covers:
  F1 — artifact recall must refuse a corrupted blob (digest verification on read).
  F2 — artifact blobs are owner-only (0600) after store.
  F4 — append-only dedup keeps the FIRST copy byte-exact and stubs only LATER
       duplicates, so earlier messages (the cached prefix) never change.
"""
import hashlib, json, os, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Fixes are wired into the package import (see slimtoken/__init__.py).
import slimtoken
import slimtoken.artifacts as artifacts
import slimtoken.pipeline as pipeline


def _tr(uid: str, text):
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uid,
                                         "content": text}]}


def _tu(uid: str):
    return {"role": "assistant", "content": [{"type": "tool_use", "id": uid, "name": "R", "input": {}}]}


BIG = "FILE CONTENTS\n" + ("line of code\n" * 200)


# ------------------------------------------------------------------ F1/F2 artifacts
def test_artifact_recall_rejects_corrupted_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    d = artifacts.store(b"ORIGINAL-CONTENT", kind="test")
    assert d is not None
    # corrupt the stored blob on disk
    blob = Path(artifacts._blob_path(d))
    original = blob.read_bytes()
    blob.write_bytes(b"CORRUPTED-BYTES")
    assert artifacts.recall(d) is None, "corrupted artifact must not be returned"
    assert artifacts.get_bytes(d) is None
    blob.write_bytes(original)          # restore for later tests
    assert artifacts.recall(d) == "ORIGINAL-CONTENT"


def test_artifact_recall_truncated_blob_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    d = artifacts.store(b"X" * 1000, kind="test")
    blob = Path(artifacts._blob_path(d))
    original = blob.read_bytes()
    blob.write_bytes(original[:500])    # truncated
    assert artifacts.recall(d) is None
    blob.write_bytes(original)


def test_artifact_blob_permissions_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    os.umask(0o002)
    d = artifacts.store(b"private tool output", kind="test")
    p = Path(artifacts._blob_path(d))
    assert p.stat().st_mode & 0o777 == 0o600, "artifact owner-only perms, got %o" % (p.stat().st_mode & 0o777)


# ------------------------------------------------------------------ F4 append-only dedup
def _run(body, **cfgkw):
    cfg = pipeline.MinifyConfig(**cfgkw)
    return pipeline.minify_request(body, cfg)


def test_append_only_keeps_first_copy_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    body = {"system": "s", "tools": [], "messages": [
        _tu("t1"), _tr("t1", [{"type": "text", "text": BIG}]),
        _tu("t2"), _tr("t2", [{"type": "text", "text": BIG}]),
        _tu("t3"), _tr("t3", [{"type": "text", "text": BIG}]),
    ]}
    out, st = _run(body)
    contents = [b["content"][0]["text"] if isinstance(b["content"], list) else b["content"]
                for m in out["messages"] if isinstance(m.get("content"), list)
                for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    full = [c for c in contents if c == BIG]
    stubs = [c for c in contents if "slimtoken:" in str(c) and "omitted" in c]
    assert len(full) == 1, "exactly one verbatim copy (the FIRST), got %d" % len(full)
    assert len(stubs) == 2, "later copies stubbed, got %d" % len(stubs)
    assert "an earlier tool_result" in stubs[0], "stub must point backwards (cache-safe)"


def test_append_only_prefix_is_byte_stable(tmp_path, monkeypatch):
    """The core cache-safety property: after a duplicate appears later in the
    conversation, every EARLIER message is byte-identical to what was sent
    before. Simulates a warm multi-turn session with a re-read on turn 2."""
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    turn1 = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a",
              "content": [{"type": "text", "text": BIG}]}]}]
    turn2 = [{"role": "assistant", "content": "noted"},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b",
              "content": [{"type": "text", "text": BIG}]}]}]
    b1 = {"system": "s", "tools": [], "messages": list(turn1)}
    b2 = {"system": "s", "tools": [], "messages": list(turn1) + list(turn2)}
    o1, _ = _run(b1)
    o2, _ = _run(b2)
    # prefix (first len(o1) messages of o2) must match o1 exactly
    assert o2["messages"][:len(o1["messages"])] == o1["messages"], \
        "append-only dedup must never rewrite earlier messages"
    # and the second run of o1 must be byte-stable too
    o1b, _ = _run(b1)
    assert o1b == o1


def test_append_only_changed_versions_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    v1 = BIG
    v2 = "FILE CONTENTS v2\n" + ("line of code\n" * 200)
    body = {"system": "s", "tools": [], "messages": [
        _tu("t1"), _tr("t1", [{"type": "text", "text": v1}]),
        _tu("t2"), _tr("t2", [{"type": "text", "text": v2}]),
    ]}
    out, st = _run(body)
    texts = [b["content"][0]["text"] if isinstance(b.get("content"), list) else b["content"]
             for m in out["messages"] for b in m["content"]
             if isinstance(m.get("content"), list) and isinstance(b, dict)
             and b.get("type") == "tool_result"]
    assert st.dedup_count == 0 or all("omitted" not in t for t in texts), \
        "version-different results must not be deduped"


def test_append_only_recall_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    body = {"system": "s", "tools": [], "messages": [
        _tu("t1"), _tr("t1", [{"type": "text", "text": BIG}]),
        _tu("t2"), _tr("t2", [{"type": "text", "text": BIG}]),
    ]}
    out, _ = _run(body)
    stub = next(b["content"][0]["text"] for m in out["messages"]
                for b in m["content"]
                if isinstance(m.get("content"), list) and isinstance(b, dict)
                and b.get("type") == "tool_result"
                and "omitted" in json.dumps(b.get("content")))
    digest = stub.rsplit("digest ", 1)[1].split(" -")[0].strip()
    assert artifacts.recall(digest) == BIG, "dropped later copy must be recallable"


def test_append_only_plain_string_tool_result(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    body = {"system": "s", "tools": [], "messages": [
        _tr("t1", BIG),
        {"role": "assistant", "content": "ok"},
        _tr("t2", BIG),
    ]}
    out, st = _run(body)
    def _block(msg):
        return [b for b in msg["content"] if isinstance(b, dict) and b.get("type") == "tool_result"][0]
    first, second = _block(out["messages"][0])["content"], _block(out["messages"][2])["content"]
    assert isinstance(first, str) and first == BIG, \
        "first occurrence kept verbatim (string form)"
    assert isinstance(second, str) and "omitted" in second and "an earlier tool_result" in second


def test_append_only_respects_min_chars(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    small = "short text"
    body = {"system": "s", "tools": [], "messages": [
        _tr("t1", [{"type": "text", "text": small}]),
        _tr("t2", [{"type": "text", "text": small}]),
    ]}
    out, st = _run(body, dedup_min_chars=200)
    assert st.dedup_count == 0


def test_append_only_env_toggle_restores_native(tmp_path, monkeypatch):
    """SLIMTOKEN_DEDUP_APPEND_ONLY=0 must restore the shipped behavior:
    the OLDER copy is stubbed ('identical to a later tool_result')."""
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("SLIMTOKEN_DEDUP_APPEND_ONLY", "0")
    body = {"system": "s", "tools": [], "messages": [
        _tr("t1", [{"type": "text", "text": BIG}]),
        {"role": "assistant", "content": "ok"},
        _tr("t2", [{"type": "text", "text": BIG}]),
    ]}
    out, st = _run(body)
    texts = []
    for m in out["messages"]:
        if not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                texts.append(b["content"][0]["text"] if isinstance(b["content"], list) else b["content"])
    stubs = [t for t in texts if "omitted" in t]
    fulls = [t for t in texts if t == BIG]
    assert stubs and fulls
    assert "a later tool_result" in stubs[0], "native path stubs the older copy"
    monkeypatch.delenv("SLIMTOKEN_DEDUP_APPEND_ONLY")


def test_append_only_pair_safety(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_ARTIFACT_DIR", str(tmp_path))
    body = {"system": "s", "tools": [], "messages": [
        _tu("t1"), _tr("t1", [{"type": "text", "text": BIG}]),
        _tu("t2"), _tr("t2", [{"type": "text", "text": BIG}]),
    ]}
    out, st = _run(body)
    use = {b["id"] for m in out["messages"] if isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use"}
    res = {b["tool_use_id"] for m in out["messages"] if isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"}
    assert res <= use, "tool_use/tool_result pairs never orphaned"
    assert not st.errors