
from __future__ import annotations

import json
import os
from typing import List, Optional




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


    v = os.environ.get("SLIMTOKEN_FILLER")
    if v is None:
        return True
    return v.strip().lower() in ("1", "true", "yes", "on")


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


    def __init__(self, max_tokens: Optional[int] = None, stops: Optional[List[str]] = None,
                 filler: bool = True):
        from .tokencount import get_encoder
        self.max_tokens = max_tokens
        self.stops = stops or []
        self.filler = filler
        self._enc = get_encoder()
        self._emitted_tokens = 0
        self._closed = False
        self._buf = b""
        self._stop_rolling = ""
        self._stop_window = max((len(s) for s in self.stops), default=0) if self.stops else 0
        self._filler_buf = ""
        self._filler_done = False


    def feed(self, chunk: bytes) -> bytes:
        if self._closed or not chunk:
            return b"" if self._closed else chunk

        if not self.max_tokens and not self.stops and not self.filler:
            return chunk
        self._buf += chunk
        out = bytearray()
        while True:

            idx = self._buf.find(b"\n\n")
            if idx < 0:
                break
            frame = self._buf[:idx]
            self._buf = self._buf[idx + 2:]
            out += self._process_frame(frame) + b"\n\n"
            if self._closed:

                self._buf = b""
                break
        return bytes(out)

    def finish(self) -> bytes:

        if self._closed:
            return b""
        if not self._buf:


            if self._filler_buf and not self._filler_done:
                self._filler_done = True
                pending = self._filler_buf
                self._filler_buf = ""
                return pending.encode("utf-8")
            return b""
        out = self._process_frame(self._buf) + b"\n\n"
        self._buf = b""
        return out


    @staticmethod
    def _text_refs(obj: dict):

        refs = []
        delta = obj.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("text"), str):
            refs.append((delta, "text"))
        choices = obj.get("choices")
        if isinstance(choices, list):
            for ch in choices:
                if not isinstance(ch, dict):
                    continue
                d = ch.get("delta")
                if not isinstance(d, dict):
                    continue
                content = d.get("content")
                if isinstance(content, str):
                    refs.append((d, "content"))
                elif isinstance(content, list):
                    for block in content:
                        if (isinstance(block, dict) and block.get("type") == "text"
                                and isinstance(block.get("text"), str)):
                            refs.append((block, "text"))
        return refs

    def _process_frame(self, frame: bytes) -> bytes:

        text = frame.decode("utf-8", errors="replace")

        data_lines = [l[5:].lstrip() for l in text.split("\n") if l.strip().startswith("data:")]
        if not data_lines:
            return frame


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
        refs = self._text_refs(obj)
        if not refs:
            return frame
        changed = False
        stop_hit = False
        for container, key in refs:
            cur = container[key]
            new_text, hit = self._filter_text(cur)
            if new_text != cur:
                container[key] = new_text
                changed = True
            if hit:
                stop_hit = True
                break
        if not changed and not stop_hit:
            return frame
        if changed and not stop_hit and all(container[key] == "" for container, key in refs):
            return b""
        new_obj = json.dumps(obj, separators=(",", ":"))

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

        combined = self._filler_buf + text

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

            self._filler_buf = ""
            return None
        if len(combined) < _MAX_FILLER_LEN and any(
                pat.startswith(combined) for pat in _FILLER_PATTERNS):

            self._filler_buf = combined
            return None

        self._filler_done = True
        self._filler_buf = ""
        return combined

    def _filter_text(self, text: str) -> tuple:

        if self._closed:
            return ("", True)

        if self.filler and not self._filler_done:
            text = self._strip_filler(text)
            if text is None:
                return ("", False)

        if self.stops:
            combined = self._stop_rolling + text
            hit_idx = -1
            for s in self.stops:
                i = combined.find(s)
                if i >= 0 and (hit_idx < 0 or i < hit_idx):
                    hit_idx = i
            if hit_idx >= 0:



                keep = hit_idx - len(self._stop_rolling)
                kept = text[:max(0, keep)]

                self._closed = True
                return (kept, True)

            if self._stop_window:
                self._stop_rolling = combined[-(self._stop_window - 1):] if self._stop_window > 1 else ""
            return (text, False)


        if self.max_tokens and self._enc:
            remaining = self.max_tokens - self._emitted_tokens
            if remaining <= 0:
                self._closed = True
                return ("", True)
            ids = self._enc.encode(text)
            if len(ids) <= remaining:
                self._emitted_tokens += len(ids)
                return (text, False)

            trunc_ids = ids[:remaining]
            trunc_text = self._enc.decode(trunc_ids)
            self._emitted_tokens = self.max_tokens
            self._closed = True
            return (trunc_text, True)

        return (text, False)


def from_env() -> Optional["OutputFilter"]:

    mt = _env_max_tokens()
    stops = _env_stops()
    filler = _env_filler()
    if not mt and not stops and not filler:
        return None
    return OutputFilter(max_tokens=mt, stops=stops, filler=filler)