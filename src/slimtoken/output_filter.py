
from __future__ import annotations

import json
import os
import re
from typing import List, Optional





# Token-guard: strip strings that a downstream tokenizer treats as special
# tokens. GLM (and friends) emit these literally in model output; once one
# lands in a client's conversation history it poisons every later request
# (tiktoken raises ValueError -> proxy kills the connection -> timeout loop).
# We rewrite them to a bracketed plain-text form BEFORE they reach the client.
_SPECIAL_TOKEN_RE = re.compile(r"<\|([a-zA-Z0-9_.\-]{1,48})\|>")
_SPECIAL_TOKEN_NAME_RE = re.compile(r"^<\|([a-zA-Z0-9_.\-]{1,48})\|>$")

# Known cl100k specials, matched exactly even without <||> delimiters
# (defense in depth — some models emit them bare).
_KNOWN_SPECIALS = (
    "endoftext", "endofprompt", "fim_prefix", "fim_middle", "fim_suffix",
)


def _block_special_tokens(text: str) -> str:
    if "<|" not in text:
        # still check bare known specials (cheap: short strings only)
        for sp in _KNOWN_SPECIALS:
            if sp in text:
                return re.sub(
                    r"(?<![a-zA-Z0-9_])" + re.escape(sp) + r"(?![a-zA-Z0-9_])",
                    "[" + sp + "]", text)
        return text
    def _sub(m: "re.Match") -> str:
        return "[" + m.group(1) + "]"
    return _SPECIAL_TOKEN_RE.sub(_sub, text)


# A trailing "<|endo" style fragment could be the start of a special token
# completed in the NEXT streamed delta — hold it back until then.
_PARTIAL_TOKEN_RE = re.compile(r"<\|[a-zA-Z0-9_.\-]{0,48}\|?$")


def _holdback_partial(text: str, carry: str) -> tuple:
    combined = carry + text
    m = _PARTIAL_TOKEN_RE.search(combined)
    if m and m.end() == len(combined):
        return combined[:m.start()], m.group(0)
    return combined, ""


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


    return bool(_env_max_tokens() or _env_stops() or _env_filler()
                or os.environ.get("SLIMTOKEN_BLOCK_TOKENS", "1") not in ("0", "false", "no", "off"))


class OutputFilter:


    def __init__(self, max_tokens: Optional[int] = None, stops: Optional[List[str]] = None,
                 filler: bool = True, block: bool = True):
        from .tokencount import get_encoder
        self.max_tokens = max_tokens
        self.stops = stops or []
        self.filler = filler
        self.block = block
        self._enc = get_encoder()
        self._emitted_tokens = 0
        self._closed = False
        self._buf = b""
        self._stop_rolling = ""
        self._stop_window = max((len(s) for s in self.stops), default=0) if self.stops else 0
        self._filler_buf = ""
        self._filler_done = False
        self._block_carry = ""      # held-back partial "<|..."" fragment
        self._pending_out = None    # one-frame delay so carry can be re-injected


    def feed(self, chunk: bytes) -> bytes:
        if self._closed or not chunk:
            return b"" if self._closed else chunk

        if not self.max_tokens and not self.stops and not self.filler and not self.block:
            return chunk
        self._buf += chunk
        out = bytearray()
        while True:

            idx = self._buf.find(b"\n\n")
            if idx < 0:
                break
            frame = self._buf[:idx]
            self._buf = self._buf[idx + 2:]
            # flush the previously-held frame, now that we know whether the
            # held-back "<|..." partial completed into a token in this frame
            if self._pending_out is not None:
                out += self._inject_carry(self._pending_out, self._block_carry)
                self._pending_out = None
            frame_out = self._process_frame(frame) + b"\n\n"
            if self._closed:
                # stream truncated (stop/max_tokens): emit this frame with its
                # own held-back tail, then stop
                out += self._inject_carry(frame_out, self._block_carry)
                self._pending_out = None
                self._block_carry = ""
                self._buf = b""
                break
            self._pending_out = frame_out
        return bytes(out)

    def finish(self) -> bytes:

        if self._closed:
            return b""
        out = bytearray()
        if self._pending_out is not None:
            out += self._inject_carry(self._pending_out, self._block_carry)
            self._pending_out = None
            self._block_carry = ""
        if self._buf:
            frame_out = self._process_frame(self._buf) + b"\n\n"
            out += self._inject_carry(frame_out, self._block_carry)
            self._block_carry = ""
            self._buf = b""
        elif self._filler_buf and not self._filler_done:
            self._filler_done = True
            pending = self._filler_buf
            self._filler_buf = ""
            out += pending.encode("utf-8")
        self._pending_out = None
        return bytes(out)

    def _inject_carry(self, frame: bytes, carry: str) -> bytes:
        """Prepend held-back text into a frame's text delta (SSE-safe)."""
        if not carry:
            return frame
        text = frame.decode("utf-8", errors="replace")
        data_lines = [l[5:].lstrip() for l in text.split("\n")
                      if l.strip().startswith("data:")]
        if len(data_lines) != 1:
            return frame + carry.encode("utf-8")
        try:
            obj = json.loads(data_lines[0])
        except Exception:
            return frame + carry.encode("utf-8")
        refs = self._text_refs(obj)
        if not refs:
            return frame + carry.encode("utf-8")
        container, key = refs[0]
        container[key] = carry + (container[key] or "")
        new_obj = json.dumps(obj, separators=(",", ":"))
        rebuilt = []
        replaced = False
        for l in text.split("\n"):
            if l.strip().startswith("data:") and not replaced:
                rebuilt.append(f"data: {new_obj}")
                replaced = True
            else:
                rebuilt.append(l)
        return "\n".join(rebuilt).encode("utf-8")


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
        # prepend any partial-token text held back from the previous frame
        # (applied to the first text ref only; SSE deltas carry one text field)
        if self.block and self._block_carry:
            container, key = refs[0]
            container[key] = self._block_carry + (container[key] or "")
            self._block_carry = ""
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

        if self.block:
            text = _block_special_tokens(text)
            text, hold = _holdback_partial(text, "")
            self._block_carry = hold

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
    # Token-guard is ALWAYS on (SLIMTOKEN_BLOCK_TOKENS=0 to disable): a special
    # token string escaping into client history wedges every future request.
    mt = _env_max_tokens()
    stops = _env_stops()
    filler = _env_filler()
    block = _bool_env_block()
    if not mt and not stops and not filler and not block:
        return None
    return OutputFilter(max_tokens=mt, stops=stops, filler=filler, block=block)


def _bool_env_block() -> bool:
    v = os.environ.get("SLIMTOKEN_BLOCK_TOKENS")
    if v is None:
        return True
    return v.strip().lower() in ("1", "true", "yes", "on")