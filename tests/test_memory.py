
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestAtomicAppend(unittest.TestCase):
    def test_basic_write(self):
        from slimtoken.memory import atomic_append, PIPE_BUF
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            atomic_append(p, "hello\n")
            atomic_append(p, "world\n")
            self.assertEqual(p.read_text(), "hello\nworld\n")
            self.assertEqual(PIPE_BUF, 4096)

    def test_creates_parents(self):
        from slimtoken.memory import atomic_append
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "deep" / "nested" / "x.jsonl"
            atomic_append(p, "z\n")
            self.assertTrue(p.exists())

    def test_bytes_input(self):
        from slimtoken.memory import atomic_append_bytes
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.jsonl"
            atomic_append_bytes(p, b"\x00\x01\x02")
            self.assertEqual(p.read_bytes(), b"\x00\x01\x02")


class TestHot(unittest.TestCase):
    def setUp(self):



        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._patches = [
            mock.patch("slimtoken.memory.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.COLD_DIR", self.tmpdir / "cold"),
            mock.patch("slimtoken.memory.engine.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.engine.COLD_DIR", self.tmpdir / "cold"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_append_writes_hot_only(self):
        from slimtoken.memory import append, HOT_DIR
        append("user", "hello", platform="t1")
        hot = HOT_DIR / "t1.jsonl"
        self.assertTrue(hot.exists())

        self.assertFalse((self.tmpdir / "warm" / "t1.warm.jsonl").exists())

    def test_append_with_explicit_warm_tier_drops_warm(self):


        from slimtoken.memory import append, HOT_DIR
        append("user", "hello", platform="t1-tiers", tiers=("hot", "warm"))
        hot = HOT_DIR / "t1-tiers.jsonl"
        self.assertTrue(hot.exists())
        self.assertFalse((self.tmpdir / "warm" / "t1-tiers.warm.jsonl").exists())

    def test_hot_to_warm_sync_is_no_op(self):

        from slimtoken.memory import hot_to_warm_sync
        result = hot_to_warm_sync()
        self.assertEqual(result, 0)

    def test_read_last_tail(self):
        from slimtoken.memory import append, read_last
        for i in range(20):
            append("user", f"msg-{i}", platform="t2")
        last = read_last(5, platform="t2")
        self.assertEqual(len(last), 5)
        self.assertEqual(last[-1]["content"], "msg-19")

    def test_no_cap_unbounded(self):
        from slimtoken.memory import append
        for i in range(500):
            append("user", "x" * 100, platform="t3")
        from slimtoken.memory import read_last
        last = read_last(1, platform="t3")
        self.assertEqual(last[-1]["content"], "x" * 100)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._patches = [
            mock.patch("slimtoken.memory.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.COLD_DIR", self.tmpdir / "cold"),
            mock.patch("slimtoken.memory.engine.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.engine.COLD_DIR", self.tmpdir / "cold"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_keyword_match(self):
        from slimtoken.memory import append, search
        append("user", "the quick brown fox", platform="t4")
        append("user", "lazy dog sleeping", platform="t4")
        hits = search("fox", platform="t4", tier="hot")
        self.assertEqual(len(hits), 1)
        self.assertIn("fox", hits[0]["content"])

    def test_empty_query(self):
        from slimtoken.memory import search
        self.assertEqual(search("", platform="x"), [])


class TestCold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._patches = [
            mock.patch("slimtoken.memory.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.COLD_DIR", self.tmpdir / "cold"),
            mock.patch("slimtoken.memory.engine.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.engine.COLD_DIR", self.tmpdir / "cold"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_write_and_get(self):
        from slimtoken.memory import write_cold, cold_get, cold_list
        write_cold("cat-a", {"fact": "f1", "confidence": 0.9})
        write_cold("cat-a", {"fact": "f2", "confidence": 0.7})
        write_cold("cat-b", {"fact": "f3", "confidence": 0.5})
        self.assertIn("cat-a", cold_list())
        self.assertIn("cat-b", cold_list())
        cat_a = cold_get("cat-a")
        self.assertEqual(len(cat_a["entries"]), 2)

    def test_atomic_rename(self):
        from slimtoken.memory import write_cold, COLD_DIR
        write_cold("atomic-test", {"fact": "x"})

        tmps = list(COLD_DIR.glob(".cold-*.tmp"))
        self.assertEqual(tmps, [])


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        import slimtoken.memory as cortexllm
        self.assertEqual(cortexllm.TASK_ORDER, "queued")
        self.assertFalse(cortexllm.ALLOW_CAP)
        self.assertEqual(cortexllm.HOOK_WRITE_TIERS, ("hot",))

    def test_describe(self):
        from slimtoken.memory.config import describe
        d = describe()
        self.assertIn("TASK_ORDER", d)
        self.assertIn("HOT_LIMIT_MB", d)

    def test_env_override(self):
        from slimtoken.memory import config
        with mock.patch.dict(os.environ, {"SLIMTOKEN_MEMORY_TASK_ORDER": "preempt"}):
            self.assertEqual(config._env("SLIMTOKEN_MEMORY_TASK_ORDER", "queued"), "preempt")


class TestHoistedSymbols(unittest.TestCase):
    def test_engine_functions(self):
        from slimtoken.memory import (
            atomic_append, atomic_append_bytes, append, read_last,
            search, write_cold, cold_list, cold_get,
        )
        for fn in (atomic_append, atomic_append_bytes, append, read_last,
                   search, write_cold, cold_list, cold_get):
            self.assertTrue(callable(fn))

    def test_lifecycle_classes(self):
        from slimtoken.memory import (
            LoopGuard, PreFlightGate, PreFlightResult,
            PostResponseVerifier, PostVerifyResult,
            ColdDistiller, CircuitBreaker, retry,
        )

        self.assertTrue(isinstance(LoopGuard(max_attempts=3), LoopGuard))
        self.assertTrue(isinstance(PreFlightGate(), PreFlightGate))
        self.assertTrue(isinstance(PostResponseVerifier(), PostResponseVerifier))
        self.assertTrue(isinstance(ColdDistiller(), ColdDistiller))
        self.assertTrue(isinstance(CircuitBreaker("t", threshold=3), CircuitBreaker))
        self.assertTrue(callable(retry))


class TestResponseModel(unittest.TestCase):
    def test_parse_text_only(self):
        from slimtoken.memory import parse_response
        blocks = parse_response("just text")
        self.assertGreaterEqual(len(blocks), 1)

    def test_parse_with_code(self):
        from slimtoken.memory import parse_response
        text = "intro\n```python\nprint(1)\n```\noutro"
        blocks = parse_response(text)
        self.assertGreaterEqual(len(blocks), 2)

    def test_format_visual_table(self):
        from slimtoken.memory import format_visual
        out = format_visual("| a | b |\n| - | - |\n| 1 | 2 |")
        self.assertGreater(len(out), 0)


class TestDistiller(unittest.TestCase):
    def test_smoke(self):
        from slimtoken.memory.distiller import _smoke

        rc = _smoke()
        self.assertEqual(rc, 0)






class TestLifecycle(unittest.TestCase):
    def test_single_instance_acquires_and_releases(self):
        from slimtoken.memory.lifecycle import SingleInstance
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "test.pid"
            with SingleInstance(pidfile):

                self.assertTrue(pidfile.exists())
                pid = int(pidfile.read_text().strip())
                self.assertEqual(pid, os.getpid())

            self.assertFalse(pidfile.exists())

    def test_single_instance_blocking_already_held(self):
        from slimtoken.memory.lifecycle import SingleInstance, SingleInstanceError
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "test.pid"
            with SingleInstance(pidfile):
                with self.assertRaises(SingleInstanceError):
                    with SingleInstance(pidfile, blocking=False):
                        pass

    def test_read_pid_stale(self):
        from slimtoken.memory.lifecycle import read_pid
        with tempfile.TemporaryDirectory() as d:
            pidfile = Path(d) / "stale.pid"
            pidfile.write_text("999999999")
            result = read_pid(pidfile)
            self.assertIsNone(result)
            self.assertFalse(pidfile.exists())


class TestStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)
        self._patches = [
            mock.patch("slimtoken.memory.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.COLD_DIR", self.tmpdir / "cold"),
            mock.patch("slimtoken.memory.engine.HOT_DIR", self.tmpdir / "hot"),
            mock.patch("slimtoken.memory.engine.COLD_DIR", self.tmpdir / "cold"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)

    def test_empty(self):
        from slimtoken.memory.stats import stats
        s = stats()
        self.assertEqual(s["hot"]["bytes"], 0)
        self.assertEqual(s["hot"]["entries"], 0)
        self.assertEqual(s["cold"]["categories"], [])
        self.assertEqual(s["estimated_tokens"], 0)

    def test_with_data(self):
        from slimtoken.memory.stats import stats
        from slimtoken.memory import append, write_cold
        append(role="user", content="hello", platform="alpha")
        append(role="assistant", content="hi", platform="alpha")
        append(role="user", content="there", platform="beta")
        write_cold("preference", {"fact": "I like tea"})
        s = stats()
        self.assertEqual(s["hot"]["entries"], 3)
        self.assertIn("alpha", s["hot"]["by_platform"])
        self.assertIn("beta", s["hot"]["by_platform"])
        self.assertEqual(s["hot"]["by_platform"]["alpha"]["entries"], 2)
        self.assertEqual(s["hot"]["by_platform"]["beta"]["entries"], 1)
        self.assertEqual(s["cold"]["categories"], ["preference"])

    def test_estimate_tokens(self):
        from slimtoken.memory.stats import estimate_tokens
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("a" * 400), 100)
        self.assertGreater(estimate_tokens("hello world"), 0)


class TestIntegrity(unittest.TestCase):
    def test_missing_file(self):
        from slimtoken.memory.integrity import check
        with tempfile.TemporaryDirectory() as d:
            r = check(Path(d) / "missing.jsonl")
            self.assertFalse(r["exists"])
            self.assertTrue(r["ok"])

    def test_empty_file(self):
        from slimtoken.memory.integrity import check
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.jsonl"
            p.write_text("")
            r = check(p)
            self.assertTrue(r["exists"])
            self.assertTrue(r["ok"])
            self.assertEqual(r["line_count"], 0)

    def test_valid_ndjson(self):
        from slimtoken.memory.integrity import check
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ok.jsonl"
            p.write_text(
                json.dumps({"role": "user", "content": "hi", "ts": "2026-01-01T00:00:00"}) + "\n"
                + json.dumps({"role": "assistant", "content": "hello", "ts": "2026-01-01T00:00:01"}) + "\n"
            )
            r = check(p)
            self.assertTrue(r["ok"])
            self.assertEqual(r["line_count"], 2)
            self.assertEqual(r["valid_lines"], 2)
            self.assertEqual(r["bad_lines"], 0)
            self.assertFalse(r["truncated"])
            self.assertEqual(r["last_role"], "assistant")
            self.assertEqual(r["last_ts"], "2026-01-01T00:00:01")

    def test_bad_line(self):
        from slimtoken.memory.integrity import check
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.jsonl"
            p.write_text(
                json.dumps({"role": "user"}) + "\n"
                + "this is not json {\n"
                + json.dumps({"role": "assistant"}) + "\n"
            )
            r = check(p)
            self.assertFalse(r["ok"])
            self.assertEqual(r["valid_lines"], 2)
            self.assertEqual(r["bad_lines"], 1)
            self.assertIsNotNone(r["first_error"])
            self.assertEqual(r["first_error"]["line_no"], 2)

    def test_truncated(self):
        from slimtoken.memory.integrity import check
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "trunc.jsonl"

            p.write_text(json.dumps({"role": "user"}) + "\n" + json.dumps({"role": "assistant"}))
            r = check(p)
            self.assertTrue(r["truncated"])
            self.assertEqual(r["valid_lines"], 2)

    def test_quick(self):
        from slimtoken.memory.integrity import quick
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.jsonl"
            self.assertTrue(quick(p))
            p.write_text(json.dumps({"a": 1}) + "\n")
            self.assertTrue(quick(p))
            p.write_text("not json\n")
            self.assertFalse(quick(p))


class TestDrain(unittest.TestCase):
    def test_basic_single_line(self):
        from slimtoken.memory.drain import drain_lines

        class FakeConn:
            def __init__(self, chunks):
                self.chunks = list(chunks)
                self.timeout = None

            def recv(self, n):
                if not self.chunks:
                    return b""
                return self.chunks.pop(0)

            def settimeout(self, t):
                self.timeout = t

        c = FakeConn([b'{"a":1}\n'])
        lines = list(drain_lines(c))
        self.assertEqual(lines, ['{"a":1}'])

    def test_split_across_chunks(self):
        from slimtoken.memory.drain import drain_lines

        class FakeConn:
            def __init__(self, chunks):
                self.chunks = list(chunks)
                self.timeout = None

            def recv(self, n):
                if not self.chunks:
                    return b""
                return self.chunks.pop(0)

            def settimeout(self, t):
                self.timeout = t



        c = FakeConn([b'{"a":1}\n{"b', b'":2}\n{"c":3}\n'])
        lines = list(drain_lines(c))
        self.assertEqual(lines, ['{"a":1}', '{"b":2}', '{"c":3}'])

    def test_trailing_partial_line_yielded(self):
        from slimtoken.memory.drain import drain_lines

        class FakeConn:
            def __init__(self, chunks):
                self.chunks = list(chunks)
                self.timeout = None

            def recv(self, n):
                if not self.chunks:
                    return b""
                return self.chunks.pop(0)

            def settimeout(self, t):
                self.timeout = t

        c = FakeConn([b'{"a":1}\n{"b"'])
        lines = list(drain_lines(c))
        self.assertEqual(lines, ['{"a":1}', '{"b"'])

    def test_50kb_payload(self):
        from slimtoken.memory.drain import drain_lines

        class FakeConn:
            def __init__(self, chunks):
                self.chunks = list(chunks)
                self.timeout = None

            def recv(self, n):
                if not self.chunks:
                    return b""
                return self.chunks.pop(0)

            def settimeout(self, t):
                self.timeout = t


        big_line = (json.dumps({"data": "x" * 50_000}) + "\n").encode("utf-8")
        chunks = [big_line[i : i + 1024] for i in range(0, len(big_line), 1024)]
        c = FakeConn(chunks)
        lines = list(drain_lines(c))
        self.assertEqual(len(lines), 1)
        obj = json.loads(lines[0])
        self.assertEqual(len(obj["data"]), 50_000)

    def test_max_bytes_cap(self):
        from slimtoken.memory.drain import drain_lines

        class FakeConn:
            def __init__(self, data):
                self.data = data
                self.timeout = None

            def recv(self, n):
                chunk, self.data = self.data[:n], self.data[n:]
                return chunk

            def settimeout(self, t):
                self.timeout = t

        c = FakeConn(b"x" * 100_000)
        with self.assertRaises(ValueError):
            list(drain_lines(c, max_bytes=1000))






class TestSchedulerParser(unittest.TestCase):
    def test_wildcard(self):
        from slimtoken.memory.scheduler import parse
        self.assertTrue(parse("* * * * *", datetime(2026, 8, 10, 14, 30)))

    def test_exact(self):
        from slimtoken.memory.scheduler import parse
        self.assertTrue(parse("30 14 10 8 *", datetime(2026, 8, 10, 14, 30)))
        self.assertFalse(parse("30 14 10 8 *", datetime(2026, 8, 10, 14, 31)))

    def test_list(self):
        from slimtoken.memory.scheduler import parse
        self.assertTrue(parse("0,15,30,45 * * * *", datetime(2026, 8, 10, 14, 15)))
        self.assertFalse(parse("0,15,30,45 * * * *", datetime(2026, 8, 10, 14, 16)))

    def test_range(self):
        from slimtoken.memory.scheduler import parse
        self.assertTrue(parse("0 9-17 * * *", datetime(2026, 8, 10, 12, 0)))
        self.assertFalse(parse("0 9-17 * * *", datetime(2026, 8, 10, 18, 0)))

    def test_step(self):
        from slimtoken.memory.scheduler import parse
        self.assertTrue(parse("*/15 * * * *", datetime(2026, 8, 10, 14, 0)))
        self.assertTrue(parse("*/15 * * * *", datetime(2026, 8, 10, 14, 15)))
        self.assertTrue(parse("*/15 * * * *", datetime(2026, 8, 10, 14, 30)))
        self.assertFalse(parse("*/15 * * * *", datetime(2026, 8, 10, 14, 7)))

    def test_aliases(self):
        from slimtoken.memory.scheduler import parse

        self.assertTrue(parse("@hourly", datetime(2026, 8, 10, 14, 0)))
        self.assertFalse(parse("@hourly", datetime(2026, 8, 10, 14, 1)))

        self.assertTrue(parse("@daily", datetime(2026, 8, 10, 0, 0)))
        self.assertFalse(parse("@daily", datetime(2026, 8, 10, 0, 1)))

    def test_day_of_week_python_vs_cron(self):
        from slimtoken.memory.scheduler import parse


        self.assertTrue(parse("0 0 * * 1", datetime(2026, 8, 10, 0, 0)))

        self.assertTrue(parse("0 0 * * 0", datetime(2026, 8, 9, 0, 0)))

    def test_invalid(self):
        from slimtoken.memory.scheduler import parse
        self.assertFalse(parse("not a cron", datetime(2026, 8, 10)))
        self.assertFalse(parse("* * *", datetime(2026, 8, 10)))


class TestSchedule(unittest.TestCase):
    def test_add_list_remove(self):
        from slimtoken.memory.scheduler import Schedule
        with tempfile.TemporaryDirectory() as d:
            s = Schedule(d)
            self.assertEqual(s.list(), [])
            s.add("nightly", "command", "0 2 * * *", {"command": "backup"})
            entries = s.list()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["name"], "nightly")
            self.assertEqual(entries[0]["expr"], "0 2 * * *")
            self.assertTrue(s.remove("nightly"))
            self.assertEqual(s.list(), [])
            self.assertFalse(s.remove("does-not-exist"))

    def test_persistence_across_instances(self):
        from slimtoken.memory.scheduler import Schedule
        with tempfile.TemporaryDirectory() as d:
            s1 = Schedule(d)
            s1.add("a", "command", "0 * * * *", {})
            s2 = Schedule(d)
            self.assertEqual(len(s2.list()), 1)
            self.assertEqual(s2.list()[0]["name"], "a")

    def test_run_due_callback(self):
        from slimtoken.memory.scheduler import Schedule
        with tempfile.TemporaryDirectory() as d:
            s = Schedule(d)

            s.add("tick", "command", "* * * * *", {"x": 1})
            fired = []
            n = s.run_due(datetime(2026, 8, 10, 14, 30), callback=lambda e: fired.append(e["name"]) or True)
            self.assertEqual(n, 1)
            self.assertEqual(fired, ["tick"])

            fired2 = []
            n2 = s.run_due(datetime(2026, 8, 10, 14, 30), callback=lambda e: fired2.append(e["name"]) or True)
            self.assertEqual(n2, 0)
            self.assertEqual(fired2, [])

            fired3 = []
            n3 = s.run_due(datetime(2026, 8, 10, 14, 31), callback=lambda e: fired3.append(e["name"]) or True)
            self.assertEqual(n3, 1)
            self.assertEqual(fired3, ["tick"])

    def test_run_due_no_callback_counts(self):
        from slimtoken.memory.scheduler import Schedule
        with tempfile.TemporaryDirectory() as d:
            s = Schedule(d)
            s.add("a", "command", "* * * * *", {})
            s.add("b", "command", "0 2 * * *", {})
            n = s.run_due(datetime(2026, 8, 10, 14, 30))
            self.assertEqual(n, 1)

    def test_disabled_skipped(self):
        from slimtoken.memory.scheduler import Schedule
        with tempfile.TemporaryDirectory() as d:
            s = Schedule(d)
            s.add("off", "command", "* * * * *", {})
            s.enable("off", False)
            fired = []
            n = s.run_due(datetime(2026, 8, 10, 14, 30), callback=lambda e: fired.append(e["name"]) or True)
            self.assertEqual(n, 0)


class TestQueue(unittest.TestCase):
    def test_add_list_clear(self):
        from slimtoken.memory.queue import Queue
        with tempfile.TemporaryDirectory() as d:
            q = Queue(d)
            self.assertEqual(q.list(), [])
            self.assertEqual(q.pending(), 0)
            e = q.add({"kind": "command", "command": "echo hi"})
            self.assertIn("id", e)
            self.assertEqual(e["status"], "queued")
            self.assertEqual(len(q.list()), 1)
            self.assertEqual(q.pending(), 1)
            self.assertEqual(q.clear(), 1)
            self.assertEqual(q.list(), [])

    def test_remove(self):
        from slimtoken.memory.queue import Queue
        with tempfile.TemporaryDirectory() as d:
            q = Queue(d)
            e1 = q.add({"kind": "command"})
            e2 = q.add({"kind": "command"})
            self.assertTrue(q.remove(e1["id"]))
            self.assertFalse(q.remove("nonexistent"))
            self.assertEqual(len(q.list()), 1)
            self.assertEqual(q.list()[0]["id"], e2["id"])

    def test_status_transitions(self):
        from slimtoken.memory.queue import Queue
        with tempfile.TemporaryDirectory() as d:
            q = Queue(d)
            e = q.add({"kind": "command"})
            self.assertTrue(q.mark_in_progress(e["id"]))
            self.assertEqual(q.get(e["id"])["status"], "in_progress")
            self.assertTrue(q.mark_completed(e["id"], result="ok"))
            self.assertEqual(q.get(e["id"])["status"], "completed")
            self.assertEqual(q.get(e["id"])["result"], "ok")

            self.assertEqual(q.pending(), 0)

    def test_persistence(self):
        from slimtoken.memory.queue import Queue
        with tempfile.TemporaryDirectory() as d:
            q1 = Queue(d)
            e = q1.add({"kind": "command", "payload": 42})
            q2 = Queue(d)
            self.assertEqual(len(q2.list()), 1)
            self.assertEqual(q2.list()[0]["payload"], 42)
            self.assertEqual(q2.list()[0]["id"], e["id"])


class TestPlan(unittest.TestCase):
    def test_set_advance_complete(self):
        from slimtoken.memory.plan import Plan
        with tempfile.TemporaryDirectory() as d:
            p = Plan(d)
            self.assertIn("error", p.status())
            p.set("build a thing", 3, steps=["design", "code", "test"])
            s = p.status()
            self.assertEqual(s["current_step"], 0)
            self.assertFalse(s["completed"])
            self.assertEqual(s["steps"], ["design", "code", "test"])
            p.advance()
            self.assertEqual(p.status()["current_step"], 1)
            self.assertEqual(p.status()["step_status"][0], "in_progress")
            p.advance(3)
            self.assertEqual(p.status()["current_step"], 3)
            self.assertEqual(p.status()["step_status"], ["done", "done", "in_progress"])
            p.advance()
            s = p.status()
            self.assertTrue(s["completed"])
            self.assertEqual(s["current_step"], 3)
            self.assertEqual(s["step_status"], ["done", "done", "done"])

    def test_invalid_total_steps(self):
        from slimtoken.memory.plan import Plan
        with tempfile.TemporaryDirectory() as d:
            p = Plan(d)
            with self.assertRaises(ValueError):
                p.set("bad", 0)

    def test_steps_length_mismatch(self):
        from slimtoken.memory.plan import Plan
        with tempfile.TemporaryDirectory() as d:
            p = Plan(d)
            with self.assertRaises(ValueError):
                p.set("bad", 3, steps=["only-one"])

    def test_advance_no_plan(self):
        from slimtoken.memory.plan import Plan
        with tempfile.TemporaryDirectory() as d:
            p = Plan(d)
            r = p.advance()
            self.assertIn("error", r)

    def test_clear(self):
        from slimtoken.memory.plan import Plan
        with tempfile.TemporaryDirectory() as d:
            p = Plan(d)
            self.assertFalse(p.clear())
            p.set("x", 2)
            self.assertTrue(p.clear())
            self.assertFalse(p.clear())


class TestDAG(unittest.TestCase):


    def test_empty_dag_sort(self):
        from slimtoken.memory.dag import DAGScheduler
        self.assertEqual(DAGScheduler().topological_sort(), [])

    def test_linear_chain(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="c", name="c", kind=StepKind.COMMAND, prompt="", depends_on=["b"]))
        d.add_task(Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]))
        d.add_task(Task(id="a", name="a", kind=StepKind.COMMAND, prompt=""))
        self.assertEqual(d.topological_sort(), ["a", "b", "c"])

    def test_diamond(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="root", name="r", kind=StepKind.COMMAND, prompt=""))
        d.add_task(Task(id="l", name="l", kind=StepKind.COMMAND, prompt="", depends_on=["root"]))
        d.add_task(Task(id="r2", name="r2", kind=StepKind.COMMAND, prompt="", depends_on=["root"]))
        d.add_task(Task(id="tip", name="t", kind=StepKind.COMMAND, prompt="", depends_on=["l", "r2"]))
        order = d.topological_sort()
        self.assertEqual(order[0], "root")
        self.assertEqual(order[-1], "tip")
        self.assertEqual(set(order), {"root", "l", "r2", "tip"})

        self.assertLess(order.index("l"), order.index("tip"))
        self.assertLess(order.index("r2"), order.index("tip"))

    def test_cycle_detected(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="a", name="a", kind=StepKind.COMMAND, prompt="", depends_on=["c"]))
        d.add_task(Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]))
        d.add_task(Task(id="c", name="c", kind=StepKind.COMMAND, prompt="", depends_on=["b"]))
        cycle = d.detect_cycles()
        self.assertIsNotNone(cycle)
        self.assertEqual(set(cycle), {"a", "b", "c"})

    def test_no_cycle_returns_none(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="a", name="a", kind=StepKind.COMMAND, prompt=""))
        d.add_task(Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]))
        self.assertIsNone(d.detect_cycles())

    def test_optimize_schedule_groups_consecutive_same_kind(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()



        d.add_task(Task(id="cmd-a", name="a", kind=StepKind.COMMAND, prompt=""))
        d.add_task(Task(id="cmd-b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["cmd-a"]))
        d.add_task(Task(id="llm-c", name="c", kind=StepKind.LLM, prompt="", depends_on=["cmd-b"]))
        d.add_task(Task(id="cmd-d", name="d", kind=StepKind.COMMAND, prompt="", depends_on=["llm-c"]))
        d.add_task(Task(id="llm-e", name="e", kind=StepKind.LLM, prompt="", depends_on=["cmd-d"]))
        res = d.optimize_schedule()
        self.assertFalse(res.has_cycles)
        self.assertEqual(res.order, ["cmd-a", "cmd-b", "llm-c", "cmd-d", "llm-e"])

        self.assertEqual(len(res.batches), 4)
        self.assertEqual([t.id for t in res.batches[0].tasks], ["cmd-a", "cmd-b"])
        self.assertEqual(res.batches[0].kind, StepKind.COMMAND)
        self.assertEqual([t.id for t in res.batches[1].tasks], ["llm-c"])
        self.assertEqual([t.id for t in res.batches[2].tasks], ["cmd-d"])
        self.assertEqual([t.id for t in res.batches[3].tasks], ["llm-e"])

    def test_optimize_schedule_cycle_returns_empty(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="a", name="a", kind=StepKind.COMMAND, prompt="", depends_on=["b"]))
        d.add_task(Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]))
        res = d.optimize_schedule()
        self.assertTrue(res.has_cycles)
        self.assertEqual(res.batches, [])
        self.assertEqual(set(res.cycle_nodes or []), {"a", "b"})

    def test_get_ready_tasks_only_when_deps_complete(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind, StepStatus
        d = DAGScheduler()
        d.add_task(Task(id="a", name="a", kind=StepKind.COMMAND, prompt=""))
        d.add_task(Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]))
        d.add_task(Task(id="c", name="c", kind=StepKind.COMMAND, prompt="", depends_on=["a", "b"]))

        ready = d.get_ready_tasks(set())
        self.assertEqual([t.id for t in ready], ["a"])

        d.tasks["a"].status = StepStatus.COMPLETED
        ready = d.get_ready_tasks({"a"})
        self.assertEqual([t.id for t in ready], ["b"])

        d.tasks["b"].status = StepStatus.COMPLETED
        ready = d.get_ready_tasks({"a", "b"})
        self.assertEqual([t.id for t in ready], ["c"])

        d.tasks["a"].status = StepStatus.RUNNING
        self.assertEqual(d.get_ready_tasks(set()), [])

    def test_batch_by_kind_groups_all_of_same_kind(self):
        from slimtoken.memory.dag import DAGScheduler, Task, StepKind
        d = DAGScheduler()
        d.add_task(Task(id="x", name="x", kind=StepKind.COMMAND, prompt=""))
        d.add_task(Task(id="y", name="y", kind=StepKind.LLM, prompt=""))
        d.add_task(Task(id="z", name="z", kind=StepKind.COMMAND, prompt=""))
        groups = d.batch_by_kind([d.tasks["x"], d.tasks["y"], d.tasks["z"]])

        self.assertEqual(len(groups), 2)
        cmd_group = next(g for g in groups if g.kind == StepKind.COMMAND)
        llm_group = next(g for g in groups if g.kind == StepKind.LLM)
        self.assertEqual(sorted(t.id for t in cmd_group.tasks), ["x", "z"])
        self.assertEqual([t.id for t in llm_group.tasks], ["y"])


class TestWorkflow(unittest.TestCase):


    def test_empty_plan_returns_empty(self):
        from slimtoken.memory.workflow import Workflow
        with tempfile.TemporaryDirectory() as d:
            called = []
            wf = Workflow(d, executor=lambda t: called.append(t.id) or True)
            res = wf.run([])
            self.assertEqual(res["status"], "empty")
            self.assertEqual(called, [])

    def test_requires_executor(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            wf = Workflow(d)
            plan = [Task(id="a", name="a", kind=StepKind.COMMAND, prompt="")]
            with self.assertRaises(RuntimeError):
                wf.run(plan)

    def test_runs_linear_plan_and_persists(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            calls = []
            wf = Workflow(d, executor=lambda t: calls.append(t.id) or True)
            plan = [
                Task(id="a", name="alpha", kind=StepKind.COMMAND, prompt="do a"),
                Task(id="b", name="beta",  kind=StepKind.COMMAND, prompt="do b", depends_on=["a"]),
            ]
            res = wf.run(plan)
            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["completed"], 2)
            self.assertEqual(res["failed"], 0)
            self.assertEqual(calls, ["a", "b"])

            self.assertTrue((Path(d) / "workflow.json").exists())
            status = wf.get_status()
            self.assertEqual(status["status"], "in_progress")
            self.assertEqual(status["total_tasks"], 2)
            self.assertEqual(status["completed"], 2)

    def test_failure_skips_dependents(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            def executor(t):
                if t.id == "b":
                    t.error = "boom"
                    return False
                return True
            wf = Workflow(d, executor=executor)
            plan = [
                Task(id="a", name="a", kind=StepKind.COMMAND, prompt=""),
                Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]),
                Task(id="c", name="c", kind=StepKind.COMMAND, prompt="", depends_on=["b"]),
                Task(id="d", name="d", kind=StepKind.COMMAND, prompt="", depends_on=["a"]),
            ]
            res = wf.run(plan)
            self.assertEqual(res["status"], "completed_with_failures")
            self.assertEqual(res["completed"], 2)
            self.assertEqual(res["failed"], 2)
            self.assertEqual(res["results"]["c"]["status"], "skipped")

    def test_progress_callback_fires_for_each_event(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        events = []
        with tempfile.TemporaryDirectory() as d:
            wf = Workflow(d, executor=lambda t: True)
            plan = [
                Task(id="a", name="a", kind=StepKind.COMMAND, prompt=""),
                Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]),
            ]
            wf.run(plan, on_progress=lambda e: events.append(e))
            phases = [e.get("phase") for e in events]
            self.assertIn("execution", phases)
            statuses = [e.get("status") for e in events if e.get("phase") == "execution"]
            self.assertIn("running", statuses)
            self.assertIn("completed", statuses)

    def test_cycle_returns_failed(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            wf = Workflow(d, executor=lambda t: True)
            plan = [
                Task(id="a", name="a", kind=StepKind.COMMAND, prompt="", depends_on=["b"]),
                Task(id="b", name="b", kind=StepKind.COMMAND, prompt="", depends_on=["a"]),
            ]
            res = wf.run(plan)
            self.assertEqual(res["status"], "failed")
            self.assertEqual(res["reason"], "cycle_detected")

    def test_clear_wipes_state(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            wf = Workflow(d, executor=lambda t: True)
            wf.run([Task(id="a", name="a", kind=StepKind.COMMAND, prompt="")])
            self.assertTrue((Path(d) / "workflow.json").exists())
            self.assertTrue(wf.clear())
            self.assertFalse((Path(d) / "workflow.json").exists())
            self.assertFalse(wf.clear())

    def test_status_no_workflow(self):
        from slimtoken.memory.workflow import Workflow
        with tempfile.TemporaryDirectory() as d:
            wf = Workflow(d, executor=lambda t: True)
            self.assertEqual(wf.get_status(), {"status": "no_workflow"})

    def test_executor_can_set_result_on_task(self):
        from slimtoken.memory.workflow import Workflow
        from slimtoken.memory.dag import Task, StepKind
        with tempfile.TemporaryDirectory() as d:
            def executor(t):
                t.result = f"result-for-{t.id}"
                return True
            wf = Workflow(d, executor=executor)
            plan = [Task(id="a", name="a", kind=StepKind.COMMAND, prompt="")]
            res = wf.run(plan)
            self.assertEqual(res["results"]["a"]["result"], "result-for-a")


class TestEnvVarRename(unittest.TestCase):


    def setUp(self):

        self._saved = {}
        for k in (
            "CORTEXAGENT_LOOP_GUARD_MAX_ATTEMPTS",
            "CORTEXAGENT_LOOP_GUARD_WINDOW_MIN",
            "CORTEXAGENT_DEFAULT_PROFILE",
            "CORTEXAGENT_KNOWN_APPROACHES_FILE",
            "CORTEXAGENT_LOOP_GUARD_DIR",
            "CORTEXAGENT_RETRY_MAX",
            "CORTEXAGENT_RETRY_DELAY",
            "CORTEXAGENT_CB_THRESHOLD",
            "CORTEXAGENT_CB_COOLDOWN",
            "SLIMTOKEN_MEMORY_LOOP_GUARD_MAX_ATTEMPTS",
            "SLIMTOKEN_MEMORY_LOOP_GUARD_WINDOW_MIN",
            "SLIMTOKEN_MEMORY_DEFAULT_PROFILE",
            "SLIMTOKEN_MEMORY_KNOWN_APPROACHES_FILE",
            "SLIMTOKEN_MEMORY_LOOP_GUARD_DIR",
            "SLIMTOKEN_MEMORY_RETRY_MAX",
            "SLIMTOKEN_MEMORY_RETRY_DELAY",
            "SLIMTOKEN_MEMORY_CB_THRESHOLD",
            "SLIMTOKEN_MEMORY_CB_COOLDOWN",
        ):
            self._saved[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_loop_guard_honors_env_vars(self):

        os.environ["SLIMTOKEN_MEMORY_LOOP_GUARD_DIR"] = tempfile.mkdtemp()
        os.environ["SLIMTOKEN_MEMORY_LOOP_GUARD_MAX_ATTEMPTS"] = "7"
        from slimtoken.memory.loop_guard import LoopGuard, _max_attempts
        self.assertEqual(_max_attempts(), 7)
        g = LoopGuard()
        self.assertEqual(g.max_attempts, 7)

    def test_reliability_honors_env_vars(self):

        os.environ["SLIMTOKEN_MEMORY_RETRY_MAX"] = "9"
        os.environ["SLIMTOKEN_MEMORY_CB_THRESHOLD"] = "11"

        import importlib
        from slimtoken.memory import reliability
        importlib.reload(reliability)
        self.assertEqual(reliability._retry_max(), 9)
        self.assertEqual(reliability._cb_threshold(), 11)

    def test_legacy_cortexagent_env_vars_are_ignored(self):

        os.environ["SLIMTOKEN_MEMORY_LOOP_GUARD_DIR"] = tempfile.mkdtemp()
        os.environ["CORTEXAGENT_LOOP_GUARD_MAX_ATTEMPTS"] = "99"


        from slimtoken.memory.loop_guard import _max_attempts
        self.assertEqual(_max_attempts(), 3)

        os.environ["CORTEXAGENT_RETRY_MAX"] = "42"
        import importlib
        from slimtoken.memory import reliability
        importlib.reload(reliability)

        self.assertEqual(reliability._retry_max(), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)