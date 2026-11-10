# 2026-11-09: loop-guard for served history. Owner-reported failure: an
# answered supervisor/steering user message re-served as fresh each turn,
# re-answered in a loop. Signs: byte-identical user OR assistant turns in
# one request; fix: an answered marker on the user turn.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slimtoken._loopguard_fix import (  # noqa: E402
    MARKER, apply_loopguard,
)


def _user(text):
    return {"role": "user", "content": text}


def _asst(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _steer():
    return _user("[supervisor] Your bash commands keep being refused. "
                 "ONE plain command per call. Do not retry the same call.")


def test_identical_assistant_reply_stamps_the_user_turn():
    msgs = [_steer(), _asst("done — the app builds and installs."),
            _steer(), _asst("done — the app builds and installs.")]
    n = apply_loopguard(msgs)
    assert n >= 1, "the re-fed steering turn must be stamped"
    assert MARKER in _extract(msgs, 0) or MARKER in _extract(msgs, 2), msgs


def _extract(msgs, i):
    c = msgs[i]["content"]
    return c if isinstance(c, str) else c[0]["text"]


def test_identical_user_refeed_stamps_the_later_copy():
    msgs = [_steer(), _asst("working on it"), _steer()]
    n = apply_loopguard(msgs)
    assert n >= 1
    assert MARKER not in _extract(msgs, 0), "first copy stays clean"
    assert MARKER in _extract(msgs, -1), "the re-feed copy carries the stamp"


def test_plain_conversation_never_stamped():
    msgs = [_user("what is port 7091?"), _asst("the slimtoken lane."),
            _user("and 7080?"), _asst("the model health relay.")]
    assert apply_loopguard(msgs) == 0


def test_marker_is_idempotent_never_re_arm():
    msgs = [_steer(), _asst("done"), _asst("done")]
    apply_loopguard(msgs)
    stamped = [_extract(msgs, 0)]
    n2 = apply_loopguard(msgs)
    after = [_extract(msgs, 0)]
    assert after == stamped, "a second pass must not change a stamped history"
    assert n2 == 0, "second pass reports nothing new"


def test_tool_results_and_code_never_written():
    block = {"type": "tool_result",
             "content": [{"type": "text", "text": "[supervisor] identical"}]}
    msgs = [
        {"role": "user", "content": [
            {"type": "text", "text": "run the build"},
            block,
            block,  # identical tool_result blocks: dedup's job, never ours
        ]},
        _asst("built"),
    ]
    n = apply_loopguard(msgs)
    assert n == 0
    assert msgs[0]["content"][1] is block or \
        msgs[0]["content"][1]["content"] == block["content"], msgs


def test_disabled_env_clean():
    import importlib, os
    os.environ["SLIMTOKEN_LOOPGUARD"] = "0"
    try:
        import slimtoken._loopguard_fix as lg
        importlib.reload(lg)
        msgs = [_steer(), _asst("done"), _asst("done")]
        assert lg.apply_loopguard(msgs) == 0
        assert MARKER not in _extract(msgs, 0)
    finally:
        os.environ.pop("SLIMTOKEN_LOOPGUARD", None)
        importlib.reload(lg)


def test_rides_the_pipeline_proxy_seam():
    # the guard must run through the same minify_request every entry point
    # rides (proxy, MCP, CLI) — dedup ON or OFF
    from slimtoken.pipeline import MinifyConfig
    import slimtoken
    cfg = MinifyConfig()
    body = {"messages": [_steer(), _asst("first answer"), _asst("first answer")],
            "system": "you are a coding agent"}
    out, stats = slimtoken.minify_request(body, cfg)
    tail_user = out["messages"][0]
    c = tail_user["content"]
    text = c if isinstance(c, str) else c[0]["text"]
    assert MARKER in text, out["messages"]
