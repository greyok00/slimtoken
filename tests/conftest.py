"""Test isolation for environment-sensitive presets.

The user's interactive shell may export SLIMTOKEN_DEDUP_MIN_CHARS=999999.
That is a valid runtime override, but it intentionally disables dedup and
makes the default context-preset assertions see effective_ctx below
nominal_ctx. Tests must exercise the defaults, not whatever the parent shell
happened to export, so the override is removed for the test process and for
MCP subprocesses (which inherit os.environ).
"""
import pytest


@pytest.fixture(autouse=True)
def _clear_dedup_override(monkeypatch):
    monkeypatch.delenv("SLIMTOKEN_DEDUP_MIN_CHARS", raising=False)
