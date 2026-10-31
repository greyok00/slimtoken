"""Repair shim for slimtoken.artifacts (audit F1, F2, 2026-10-31).

Problems fixed here, on top of the shipped artifacts module:
  F1 — ``recall``/``get`` returned a stored blob without checking its sha256
       against the digest key. A blob corrupted on disk (or truncated / edited)
       came back as if it were the exact original. The README promises digest
       validation and reversibility; this file makes it true: a blob whose
       content no longer hashes to its digest is rejected and the caller gets
       None, with a warning on stderr.
  F2 — stored blobs inherit the process umask (0664 by default) although they
       may contain private tool output. New files are now written/kept
       owner-only (0600; the artifact dir gets 0700 when created from here).

The shim is registered by ``slimtoken/__init__.py`` so it is in
``sys.modules`` before any compiled module imports ``.artifacts``. Everything
else (storage layout, index file, digest format) is delegated to the original
module, so data written by earlier versions is still readable.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import sysconfig
from pathlib import Path

# import the compiled module without touching this shim's own name
_pkg_parent = Path(__file__).resolve().parent.parent
if str(_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_pkg_parent))

import slimtoken.artifacts as _native  # noqa: E402  (loads the .so)


def _blob_sha_ok(digest: str) -> bytes | None:
    """Return the blob bytes iff its sha256 matches the digest, else None."""
    data = _native.get_bytes(digest)
    if data is None:
        return None
    return data if hashlib.sha256(data).hexdigest() == digest else None


def _verified_get(digest: str):
    data = _blob_sha_ok(digest)
    if data is None and _native.get_bytes(digest) is not None:
        sys.stderr.write(
            "slimtoken: artifact %s failed digest validation (corrupted); refusing to return it\n" % digest[:16])
    return data


def store(content, kind: str = "unknown", origin: str = "") -> "str | None":
    d = _native.store(content, kind, origin)
    if d is None:
        return None
    try:
        p = Path(_native._blob_path(d))
        if p.exists():
            os.chmod(p, 0o600)
    except Exception:
        pass
    try:
        idx = Path(_native._index_path(d))
        if idx.exists():
            os.chmod(idx, 0o600)
    except Exception:
        pass
    try:
        base = Path(_native.artifact_dir())
        os.chmod(base, 0o700)
    except Exception:
        pass
    return d


def recall(digest: str, offset: int = 0, limit: int = None) -> "str | None":
    """Native signature preserved: offset/limit slice the DECODED text (chars)."""
    data = _verified_get(digest)
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    if limit is None:
        return text[offset:] if offset else text
    return text[offset:offset + limit]


def get(digest: str) -> "str | None":
    data = _verified_get(digest)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def get_bytes(digest: str) -> "bytes | None":
    return _verified_get(digest)


def apply_fix() -> None:
    """Install the verifying shim as slimtoken.artifacts in sys.modules."""
    mods = dict(_native.__dict__)  # keep native names (types, paths, helpers)
    sha_ok = hashlib.sha256
    mods.update({
        "get_bytes": get_bytes, "get": get, "recall": recall, "store": store,
        "_sha_ok": _blob_sha_ok, "_verified_recall_fix": True,
        "sha256": sha_ok, "json": json,
    })
    shim = type(sys)("slimtoken.artifacts")
    shim.__dict__.update(mods)
    sys.modules["slimtoken.artifacts"] = shim
    # keep the package attribute in sync: ``import slimtoken.artifacts`` plus
    # attribute access resolves the parent's attr even when sys.modules points
    # at the shim, and the native .so already set it on first import.
    _pkg = sys.modules.get("slimtoken")
    if _pkg is not None:
        _pkg.artifacts = shim