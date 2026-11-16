"""Content-addressed binary artifact storage for tool outputs.

Large tool results are stored once as opaque blobs addressed by the sha256
digest of their content, so duplicate payloads and later recall requests both
resolve to the same stored bytes. Blobs are owner-only (0600) because they may
carry private tool output, and the artifact directory is created 0700.

The storage dir defaults to ``~/.slimtoken/artifacts`` and can be redirected
with the ``SLIMTOKEN_ARTIFACT_DIR`` environment variable (re-read per call, so
tests and multiple instances can run side by side).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

_DIGEST_HEX_LEN = 64

def artifact_dir() -> Path:
    """Return the artifact storage directory, creating it owner-only if absent."""
    base = os.environ.get("SLIMTOKEN_ARTIFACT_DIR")
    p = Path(base).expanduser() if base else Path.home() / ".slimtoken" / "artifacts"
    try:
        p.mkdir(parents=True, exist_ok=True)
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p

def _blob_path(digest: str) -> Path:
    return artifact_dir() / (digest + ".blob")

def _index_path(digest: str) -> Path:
    return artifact_dir() / (digest + ".json")

def _write_owner_only(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with owner-only permissions."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".art-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)

def store(content, kind: str = "unknown", origin: str = "") -> Optional[str]:
    """Store ``content`` (bytes) and return its digest, or ``None`` on refusal.

    Empty or non-bytes payloads are rejected. Storing is idempotent: the same
    content always resolves to the same digest and rewrites nothing. Each
    store appends a record to the append-only ``index.jsonl`` log so the
    artifact history can be listed later.
    """
    if content is None:
        return None
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content or len(content) == 0:
        return None
    digest = hashlib.sha256(content).hexdigest()
    try:
        _write_owner_only(_blob_path(digest), content)
        meta = {
            "digest": digest,
            "kind": kind,
            "origin": origin,
            "bytes": len(content),
        }
        _write_owner_only(_index_path(digest), json.dumps(meta).encode("utf-8"))
        rec = {
            "digest": digest,
            "kind": kind,
            "origin": origin,
            "bytes": len(content),
        }
        idx = artifact_dir() / "index.jsonl"
        line = json.dumps(rec).encode("utf-8")
        idx.parent.mkdir(parents=True, exist_ok=True)
        with open(idx, "a") as f:
            f.write(line.decode("utf-8") + "\n")
        os.chmod(idx, 0o600)
    except OSError:
        return None
    return digest

def get_bytes(digest: str) -> Optional[bytes]:
    """Return the stored bytes for ``digest``, or ``None`` if unavailable."""
    if not digest or len(digest) != _DIGEST_HEX_LEN:
        return None
    blob = _blob_path(digest)
    try:
        data = blob.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != digest:
        return None
    return data

def get(digest: str) -> Optional[str]:
    """Return the stored bytes for ``digest`` decoded as text, else ``None``."""
    data = get_bytes(digest)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")

def recall(digest: str, offset: int = 0, limit: Optional[int] = None) -> Optional[str]:
    """Return recalled text, optionally sliced by ``offset``/``limit`` chars."""
    text = get(digest)
    if text is None:
        return None
    if limit is None:
        return text[offset:] if offset else text
    return text[offset:offset + limit]
