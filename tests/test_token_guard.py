

import json

import pytest

from slimtoken import tokencount
from slimtoken.output_filter import OutputFilter, _block_special_tokens, from_env


SPECIAL = "<|" + "endof" + "text" + "|>"




def test_count_with_special_token_does_not_raise():
    n = tokencount.count(f"hello {SPECIAL} world")
    assert n > 0


def test_count_bytes_with_special_token_does_not_raise():
    n = tokencount.count_bytes(f"hello {SPECIAL} world".encode())
    assert n > 0




def test_block_special_tokens_rewrites_angle_form():
    out = _block_special_tokens(f"thinking done {SPECIAL} answer")
    assert "<|" not in out
    assert SPECIAL not in out
    assert "[endoftext]" in out


def test_block_special_tokens_bare_known_specials():
    out = _block_special_tokens("a endofprompt b")
    assert out == "a [endofprompt] b"


def test_block_leaves_normal_text_alone():
    text = "plain response with <|not a match because spaces|> ok"
    assert _block_special_tokens(text) == text


def _sse_frame(delta_text: str) -> bytes:
    payload = json.dumps({"delta": {"text": delta_text}})
    return f"data: {payload}\n\n".encode()


def test_output_filter_blocks_special_token_in_stream():
    f = OutputFilter()
    out = f.feed(_sse_frame(f"part one {SPECIAL} part two")) + f.finish()
    assert SPECIAL not in out.decode("utf-8", "replace")
    assert b"part one" in out


def test_output_filter_split_across_frames_is_caught():


    f = OutputFilter()
    half = len(SPECIAL) // 2
    out = (f.feed(_sse_frame("xx" + SPECIAL[:half]))
           + f.feed(_sse_frame(SPECIAL[half:] + " yy"))
           + f.finish())
    stream = out.decode("utf-8", "replace")
    assert SPECIAL not in stream
    assert "[endoftext]" in stream




def test_filters_are_independent_instances(monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_STOP", "STOP")
    a = from_env()
    b = from_env()
    assert a is not b
    a.feed(_sse_frame("close me STOP"))
    assert a._closed
    assert not b._closed
    out = b.feed(_sse_frame("still open")) + b.finish()
    assert b"still open" in out


def test_block_env_disable(monkeypatch):
    monkeypatch.setenv("SLIMTOKEN_BLOCK_TOKENS", "0")
    monkeypatch.delenv("SLIMTOKEN_MAX_TOKENS", raising=False)
    monkeypatch.delenv("SLIMTOKEN_STOP", raising=False)
    f = from_env()
    raw = f.feed(_sse_frame(f"raw {SPECIAL} passthrough")) + f.finish()
    assert SPECIAL in raw.decode()