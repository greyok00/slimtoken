"""output_filter — enforce max_tokens / stop / filler-strip on the streamed response.

Gated by ``SLIMTOKEN_MAX_TOKENS`` (integer), ``SLIMTOKEN_STOP`` (a comma-joined
list of stop strings), and/or ``SLIMTOKEN_FILLER`` (bool). When ALL are unset,
the filter is inert and the proxy streams raw bytes untouched (zero overhead).
When any is set, the filter wraps the response stream and:

  - ``max_tokens``: counts emitted text tokens incrementally with the bundled
    cl100k tokenizer, and closes the stream once the cap is reached (best-effort
    — it truncates at the first chunk boundary that crosses the cap).
  - ``stop``: keeps a rolling buffer, and when a stop string is matched, the
    stream is truncated to end exactly at the match (the stop string itself is
    NOT emitted, matching the Anthropic API stop semantics).
  - ``filler``: strips model-generated lead-in filler ("Sure!", "Here is the
    code:", "Let me know if you need anything else.") from the START of the
    response. Streaming-safe: a small pending buffer holds the response head
    until it is either consumed as filler or confirmed as real content, so a
    filler phrase split across chunks is still caught. Backported from
    CortexAgent's ``minify_response`` (R4 output-side minify).

The filter operates on decoded SSE text. To stay robust against arbitrary
upstream formats, it only touches ``data:`` payloads that decode as JSON and
contain a ``delta`` with ``text``; everything else (event frames, non-text
deltas, binary) is passed through verbatim. This keeps the common-case
Anthropic-style streaming response correct without a heavy full-fledged SSE
parser.
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

# Lead-in filler phrases stripped when SLIMTOKEN_FILLER=1. Same set as
# CortexAgent's lib/grammar_proxy.py minify_response(). Stripped only from the
# very start of the response, never mid-stream.
_FILLER_PATTERNS = (
    "Sure!\n", "Sure!\n\n", "Sure, ", "Sure.\n",
    "Here is the code:\n", "Here is the code:\n\n",
    "Here is your code:\n", "Here is your code:\n\n",
    "Let me know if you need anything else.\n",
    "Let me know if you have any questions.\n",
    "I hope this helps!\n", "I hope this helps.\n",
    "Feel free to ask if you have any questions.\n",
)
_MAX_FILLER_LEN = max(len(p) for p in _FILLER_PATTERNS)


def _env_filler() -> bool:
    return os.environ.get("SLIMTOKEN_FILLER", "").strip().lower() in ("1", "true", "yes", "on")


def _env_max_tokens() -> Optional[int]:
    v = os.environ.get("SLIMTOKEN_MAX_TOKENS")
    if not v:
        return None
    try:
        n = int(v)
        return n if n > 0 else None
    except ValueError:
        return None


def _env_stops() -> List[str]:
    v = os.environ.get("SLIMTOKEN_STOP")
    if not v:
        return []
    return [s for s in v.split("\x00") if s] if "\x00" in v else [s for s in v.split(",") if s]


def is_active() -> bool:
    return bool(_env_max_tokens() or _env_stops() or _env_filler())


class OutputFilter:
    """Incremental filter over a stream of response bytes.

    feed(chunk) -> bytes to emit (already processed). Call finish() at stream
    end to flush any buffered raw passthrough.

    The filter is a lightweight state machine. It splits the byte stream on
    SSE frame boundaries (``\\n\\n``), and for each complete ``data:`` frame
    decides: pass through, or rewrite the text delta to enforce caps.
    """

    def __init__(self, max_tokens: Optional[int] = None, stops: Optional[List[str]] = None,
                 filler: bool = False):
        from .tokencount import get_encoder
        self.max_tokens = max_tokens
        self.stops = stops or []
        self.filler = filler
        self._enc = get_encoder()
        self._emitted_tokens = 0
        self._closed = False
        self._buf = b""          # incomplete SSE frame buffer
        self._stop_rolling = ""  # rolling window for stop scanning
        self._stop_window = max((len(s) for s in self.stops), default=0) if self.stops else 0
        self._filler_buf = ""    # pending start-of-response buffer (filler mode)
        self._filler_done = False  # True once real content has been emitted

    # ── public API ───────────────────────────────────────────────────────────
    def feed(self, chunk: bytes) -> bytes:
        if self._closed or not chunk:
            return b"" if self._closed else chunk
        # If no lever is active, this object shouldn't exist — but guard.
        if not self.max_tokens and not self.stops and not self.filler:
            return chunk
        self._buf += chunk
        out = bytearray()
        while True:
            # SSE frames are separated by a blank line. Split on \n\n.
            idx = self._buf.find(b"\n\n")
            if idx < 0:
                break  # incomplete frame; keep buffering
            frame = self._buf[:idx]
            self._buf = self._buf[idx + 2:]
            out += self._process_frame(frame) + b"\n\n"
            if self._closed:
                # drop any remaining buffered bytes once closed
                self._buf = b""
                break
        return bytes(out)

    def finish(self) -> bytes:
        """Flush whatever remains. Called once at stream end."""
        if self._closed:
            return b""
        if not self._buf:
            # Response ended while still buffering a filler prefix (e.g. the
            # whole reply was "Sure") — emit the pending text verbatim.
            if self._filler_buf and not self._filler_done:
                self._filler_done = True
                pending = self._filler_buf
                self._filler_buf = ""
                return pending.encode("utf-8")
            return b""
        out = self._process_frame(self._buf) + b"\n\n"
        self._buf = b""
        return out

    # ── internals ────────────────────────────────────────────────────────────
    def _process_frame(self, frame: bytes) -> bytes:
        """Process one complete SSE frame. Returns bytes to emit."""
        text = frame.decode("utf-8", errors="replace")
        # find the data: line(s)
        data_lines = [l[5:].lstrip() for l in text.split("\n") if l.strip().startswith("data:")]
        if not data_lines:
            return frame  # not a data frame — pass through (event: lines, etc.)
        # we only rewrite if there's exactly one data line we can parse as JSON
        # with a text delta. Multi-data frames are passed through untouched.
        if len(data_lines) != 1:
            return frame
        payload = data_lines[0]
        if payload == "[DONE]":
            return frame
        try:
            obj = json.loads(payload)
        except Exception:
            return frame
        if not isinstance(obj, dict):
            return frame
        delta = obj.get("delta")
        if not isinstance(delta, dict) or not isinstance(delta.get("text"), str):
            return frame  # only text deltas are filtered
        text_chunk = delta["text"]
        new_text, stop_hit = self._filter_text(text_chunk)
        if new_text == text_chunk and not stop_hit:
            return frame  # unchanged
        if not new_text and not stop_hit:
            return b""  # drop the frame entirely
        # rebuild the frame with the filtered text
        delta["text"] = new_text
        new_obj = json.dumps(obj, separators=(",", ":"))
        # preserve any non-data lines, replace the data line
        lines = text.split("\n")
        rebuilt = []
        replaced = False
        for l in lines:
            if l.strip().startswith("data:") and not replaced:
                rebuilt.append(f"data: {new_obj}")
                replaced = True
            else:
                rebuilt.append(l)
        out = "\n".join(rebuilt)
        if stop_hit:
            self._closed = True
        return out.encode("utf-8")

    def _strip_filler(self, text: str) -> Optional[str]:
        """Strip leading filler from the response head. Returns the text to
        emit, or None when still buffering (the head may be a partial filler
        phrase spanning chunks)."""
        combined = self._filler_buf + text
        # Strip any/all consecutive leading filler phrases.
        while True:
            stripped = combined
            for pat in _FILLER_PATTERNS:
                if stripped.startswith(pat):
                    stripped = stripped[len(pat):]
                    break
            if stripped == combined:
                break
            combined = stripped
        if not combined:
            # Everything so far was filler — keep buffering.
            self._filler_buf = ""
            return None
        if len(combined) < _MAX_FILLER_LEN and any(
                pat.startswith(combined) for pat in _FILLER_PATTERNS):
            # Combined is a prefix of a filler phrase — it may span chunks.
            self._filler_buf = combined
            return None
        # Real content reached.
        self._filler_done = True
        self._filler_buf = ""
        return combined

    def _filter_text(self, text: str) -> tuple:
        """Returns (filtered_text, stop_hit). Enforces filler, then stop, then max_tokens."""
        if self._closed:
            return ("", True)
        # 0. filler-strip (response head only)
        if self.filler and not self._filler_done:
            text = self._strip_filler(text)
            if text is None:
                return ("", False)  # still buffering filler; emit nothing
        # 1. stop-sequence scanning (rolling buffer)
        if self.stops:
            combined = self._stop_rolling + text
            hit_idx = -1
            for s in self.stops:
                i = combined.find(s)
                if i >= 0 and (hit_idx < 0 or i < hit_idx):
                    hit_idx = i
            if hit_idx >= 0:
                # truncate at the stop; the rolling buffer's already-emitted
                # portion is gone, so we only return the portion of `text` that
                # precedes the stop within the combined window.
                keep = hit_idx - len(self._stop_rolling)
                kept = text[:max(0, keep)]
                # try to flush any pending buffer first? we just return kept
                self._closed = True
                return (kept, True)
            # update rolling window (keep enough to catch a stop spanning chunks)
            if self._stop_window:
                self._stop_rolling = combined[-(self._stop_window - 1):] if self._stop_window > 1 else ""
            return (text, False)

        # 2. max_tokens enforcement
        if self.max_tokens and self._enc:
            remaining = self.max_tokens - self._emitted_tokens
            if remaining <= 0:
                self._closed = True
                return ("", True)
            ids = self._enc.encode(text)
            if len(ids) <= remaining:
                self._emitted_tokens += len(ids)
                return (text, False)
            # truncate at the token boundary
            trunc_ids = ids[:remaining]
            trunc_text = self._enc.decode(trunc_ids)
            self._emitted_tokens = self.max_tokens
            self._closed = True
            return (trunc_text, True)
        # max_tokens set but no encoder — can't count, pass through
        return (text, False)


def from_env() -> Optional["OutputFilter"]:
    """Build an OutputFilter from env, or None when inactive (raw passthrough)."""
    mt = _env_max_tokens()
    stops = _env_stops()
    filler = _env_filler()
    if not mt and not stops and not filler:
        return None
    return OutputFilter(max_tokens=mt, stops=stops, filler=filler)