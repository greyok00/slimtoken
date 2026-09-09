#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional



_DEFAULT_STATE_DIR = Path.home() / ".config" / "cortexllm" / "loop_guard"


def _state_dir() -> Path:

    raw = os.environ.get("SLIMTOKEN_MEMORY_LOOP_GUARD_DIR")
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_STATE_DIR


def _max_attempts() -> int:
    try:
        return int(os.environ.get("SLIMTOKEN_MEMORY_LOOP_GUARD_MAX_ATTEMPTS", "3"))
    except ValueError:
        return 3


def _window_minutes() -> int:
    try:
        return int(os.environ.get("SLIMTOKEN_MEMORY_LOOP_GUARD_WINDOW_MIN", "10"))
    except ValueError:
        return 10


def _default_profile() -> str:
    return os.environ.get("SLIMTOKEN_MEMORY_DEFAULT_PROFILE", "default")


def _known_approaches_file() -> Path:
    raw = os.environ.get("SLIMTOKEN_MEMORY_KNOWN_APPROACHES_FILE")
    if raw:
        return Path(raw).expanduser()
    return _state_dir() / "known_approaches.json"


def _state_path(profile: str) -> Path:
    return _state_dir() / f"{profile}.json"


class LoopGuard:


    def __init__(self, max_attempts: Optional[int] = None,
                 window_minutes: Optional[int] = None,
                 profile: Optional[str] = None):
        self.max_attempts = max_attempts if max_attempts is not None else _max_attempts()
        self.window = timedelta(minutes=window_minutes if window_minutes is not None else _window_minutes())
        self.profile = profile or _default_profile()
        self.state_file = _state_path(self.profile)
        self.attempts = self._load_state()




    def _load_state(self) -> Dict:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text())
                for key, entries in data.items():
                    for entry in entries:
                        if "timestamp" in entry:
                            entry["timestamp"] = float(entry["timestamp"])
                return data
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            serializable = {}
            for key, entries in self.attempts.items():
                serializable[key] = []
                for entry in entries:
                    entry_copy = entry.copy()
                    if "timestamp" in entry_copy:
                        entry_copy["timestamp"] = str(entry_copy["timestamp"])
                    serializable[key].append(entry_copy)
            self.state_file.write_text(json.dumps(serializable, indent=2))
        except Exception as e:
            print(f"LoopGuard: failed to save state: {e}", file=sys.stderr)




    def record_attempt(self, task_key: str, approach: str,
                       success: bool, error: Optional[str] = None) -> None:
        if task_key not in self.attempts:
            self.attempts[task_key] = []
        self._cleanup_old_entries(task_key)
        self.attempts[task_key].append({
            "timestamp": time.time(),
            "approach": approach,
            "success": success,
            "error": (error[:200] if error else None),
        })
        self._save_state()

    def check_loop(self, task_key: str, approach: str) -> Dict:
        if task_key not in self.attempts:
            return {"in_loop": False, "attempt_count": 0, "failure_count": 0,
                    "recommendation": ""}
        self._cleanup_old_entries(task_key)
        entries = self.attempts[task_key]
        attempt_count = len(entries)
        failure_count = sum(1 for e in entries if not e["success"])

        in_loop = False
        recommendation = ""


        same_approach_failures = [e for e in entries
                                  if e["approach"] == approach and not e["success"]]
        if len(same_approach_failures) >= 2:
            in_loop = True
            recommendation = (
                f"STOP: Same approach '{approach}' has failed "
                f"{len(same_approach_failures)} times. "
                f"Try a fundamentally different approach."
            )

        elif failure_count >= self.max_attempts:
            in_loop = True
            recommendation = (
                f"STOP: {failure_count} failures in "
                f"{int(self.window.total_seconds() // 60)} minutes. "
                f"Step back and diagnose root cause before continuing."
            )

        recent = [e for e in entries if time.time() - e["timestamp"] < 120]
        if len(recent) >= 3:
            in_loop = True
            recommendation = "STOP: Too many rapid attempts. Take a break and reassess."

        return {
            "in_loop": in_loop,
            "attempt_count": attempt_count,
            "failure_count": failure_count,
            "recommendation": recommendation,
            "recent_approaches": sorted({e["approach"] for e in entries}),
        }

    def get_alternative_approaches(self, task_key: str, current_approach: str) -> List[str]:

        known = _load_known_approaches()
        if task_key not in known:
            return []
        tried = {e["approach"] for e in self.attempts.get(task_key, [])}
        return [a for a in known[task_key] if a not in tried]

    def reset_task(self, task_key: str) -> None:
        if task_key in self.attempts:
            del self.attempts[task_key]
            self._save_state()

    def get_status(self, task_key: Optional[str] = None) -> Dict:
        if task_key:
            return {task_key: self.check_loop(task_key, "current")}
        return {key: self.check_loop(key, "unknown") for key in self.attempts}




    def _cleanup_old_entries(self, task_key: str) -> None:
        if task_key not in self.attempts:
            return
        cutoff = time.time() - self.window.total_seconds()
        self.attempts[task_key] = [
            e for e in self.attempts[task_key]
            if e.get("timestamp", 0) > cutoff
        ]


def _load_known_approaches() -> Dict:
    f = _known_approaches_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}



_default_guard: Optional[LoopGuard] = None


def _guard(profile: Optional[str] = None) -> LoopGuard:
    global _default_guard
    if profile is None and _default_guard is None:
        _default_guard = LoopGuard()
    if profile is not None:
        return LoopGuard(profile=profile)
    return _default_guard  # type: ignore


def record(task: str, approach: str, success: bool,
           error: Optional[str] = None, profile: Optional[str] = None) -> None:
    _guard(profile).record_attempt(task, approach, success, error)


def check(task: str, approach: str, profile: Optional[str] = None) -> Dict:
    return _guard(profile).check_loop(task, approach)



def _cli(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    args = argv[1:]


    kwargs: Dict[str, str] = {}
    positional: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and i + 1 < len(args):
            kwargs[a[2:]] = args[i + 1]
            i += 2
        else:
            positional.append(a)
            i += 1

    profile = kwargs.get("profile")
    g = LoopGuard(profile=profile) if profile else LoopGuard()

    if cmd == "record":
        task = kwargs.get("task") or (positional[0] if positional else None)
        approach = kwargs.get("approach") or (positional[1] if len(positional) > 1 else None)
        if not task or not approach:
            print("usage: loop_guard.py record --task T --approach A [--success|--error MSG]", file=sys.stderr)
            return 2
        success = "success" in kwargs
        error = kwargs.get("error")
        g.record_attempt(task, approach, success, error)
        print(f"recorded: task={task} approach={approach} success={success}")
        return 0
    if cmd == "check":
        task = kwargs.get("task") or (positional[0] if positional else None)
        approach = kwargs.get("approach") or (positional[1] if len(positional) > 1 else None)
        if not task or not approach:
            print("usage: loop_guard.py check --task T --approach A", file=sys.stderr)
            return 2
        result = g.check_loop(task, approach)
        print(json.dumps(result, indent=2))
        return 0 if not result["in_loop"] else 1
    if cmd == "status":
        task = kwargs.get("task") or (positional[0] if positional else None)
        print(json.dumps(g.get_status(task), indent=2))
        return 0
    if cmd == "alternatives":
        task = kwargs.get("task") or (positional[0] if positional else None)
        approach = kwargs.get("approach") or (positional[1] if len(positional) > 1 else None)
        if not task:
            print("usage: loop_guard.py alternatives --task T [--approach A]", file=sys.stderr)
            return 2
        if not approach:
            print(json.dumps(g.get_alternative_approaches(task, ""), indent=2))
        else:
            print(json.dumps(g.get_alternative_approaches(task, approach), indent=2))
        return 0
    if cmd == "reset":
        task = kwargs.get("task") or (positional[0] if positional else None)
        if not task:
            print("usage: loop_guard.py reset --task T", file=sys.stderr)
            return 2
        g.reset_task(task)
        print(f"reset: {task}")
        return 0
    if cmd == "smoke":
        return _smoke()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.environ["SLIMTOKEN_MEMORY_LOOP_GUARD_DIR"] = td

        g = LoopGuard(max_attempts=3, window_minutes=10)


        g.record_attempt("smoke_a", "approach_x", False, "err1")
        g.record_attempt("smoke_a", "approach_x", False, "err2")
        r = g.check_loop("smoke_a", "approach_x")
        assert r["in_loop"], f"expected in_loop, got {r}"
        print(f"  cond1 same-approach: in_loop={r['in_loop']}  rec={r['recommendation'][:50]}…")


        g2 = LoopGuard(max_attempts=3, window_minutes=10)
        for i in range(3):
            g2.record_attempt("smoke_b", f"approach_{i}", False, f"err{i}")
        r = g2.check_loop("smoke_b", "approach_x")
        assert r["in_loop"]
        print(f"  cond2 total-failure: in_loop={r['in_loop']}  rec={r['recommendation'][:50]}…")


        g3 = LoopGuard(max_attempts=99, window_minutes=60)
        for i in range(3):
            g3.record_attempt("smoke_c", "rapid", False, "x")
        r = g3.check_loop("smoke_c", "rapid")
        assert r["in_loop"]
        print(f"  cond3 rapid-retry:  in_loop={r['in_loop']}  rec={r['recommendation'][:50]}…")


        g4 = LoopGuard(max_attempts=3, window_minutes=10)
        g4.record_attempt("smoke_d", "ok", True)
        r = g4.check_loop("smoke_d", "ok")
        assert not r["in_loop"]
        print(f"  no-loop on success: in_loop={r['in_loop']}")


        g4.reset_task("smoke_d")
        r = g4.check_loop("smoke_d", "ok")
        assert not r["in_loop"] and r["attempt_count"] == 0
        print(f"  reset clears state: attempts={r['attempt_count']}")

    print("loop_guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
