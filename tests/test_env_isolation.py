"""Secrets isolation — the mechanical half of the v2 security model.

v1 kept `cat .env` impossible with a Bash prefix allowlist. v2 gives the agent
general Bash, so that lever is gone and the boundary moves into the process
environment: if `SYNAPSE_CONN_STR` is never in `os.environ`, no subprocess the
agent spawns can inherit it. A scratch script that reaches for it gets a
KeyError rather than a connection string.

These tests are the proof of that claim. Per the v2 spec, test 9 is the single
most important new test in the redesign.

Nothing here prints, logs or asserts on a secret VALUE — only on presence and
absence. A test that leaks the thing it guards is worse than no test.
"""
from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path

import pytest


def calls_named(source: str) -> set[str]:
    """Every function name CALLED in `source`.

    Parsed, not grepped. These modules explain in comments why they must not
    call load_dotenv, and a substring check fails on the explanation — which
    would push the next person to delete the comment to make the test pass.
    """
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(getattr(f, "id", None) or getattr(f, "attr", None))
    return {n for n in names if n}

SENTINEL = "Driver={ODBC};Server=test-only-not-a-real-secret;"
KEY_SENTINEL = "sk-ant-test-only-not-real"


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """A .env carrying both variables, and a clean os.environ around the test."""
    p = tmp_path / ".env"
    p.write_text(
        f"SYNAPSE_CONN_STR={SENTINEL}\n"
        f"ANTHROPIC_API_KEY={KEY_SENTINEL}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SYNAPSE_CONN_STR", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return p


# --- 9. the one that matters --------------------------------------------------

def test_startup_never_puts_the_connection_string_in_the_environment(fake_env):
    """Spec test 9. Every other control in v2 is procedural; this one is
    mechanical, and it is what makes general Bash acceptable."""
    import main

    exported = main.load_secrets(fake_env)

    assert "SYNAPSE_CONN_STR" not in os.environ
    assert "SYNAPSE_CONN_STR" not in exported
    # ...and the value has not leaked in under some other name.
    assert SENTINEL not in os.environ.values()


def test_the_api_key_is_exported_because_the_spawned_cli_needs_it(fake_env):
    """Selective, not blanket. The CLI is a child process and reads this from
    the environment; without it the SDK silently falls back to a claude.ai
    login, which is a different billing path and a confusing failure."""
    import main

    exported = main.load_secrets(fake_env)

    assert os.environ.get("ANTHROPIC_API_KEY") == KEY_SENTINEL
    assert exported == ["ANTHROPIC_API_KEY"]


def test_load_secrets_reports_names_never_values(fake_env):
    """The return value ends up in logs and error paths. It must be safe to
    print — so it carries which variables were exported, not what they are."""
    import main

    exported = main.load_secrets(fake_env)
    assert all(isinstance(n, str) for n in exported)
    for name in exported:
        assert SENTINEL not in name and KEY_SENTINEL not in name


def test_a_missing_env_file_is_not_an_error(tmp_path, monkeypatch):
    """A fresh clone has no .env. Startup must degrade to preflight warnings,
    not a traceback."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import main
    assert main.load_secrets(tmp_path / "does-not-exist.env") == []


def test_an_empty_value_is_not_exported(tmp_path, monkeypatch):
    """`ANTHROPIC_API_KEY=` in a .env must not export an empty string. An empty
    key does not fall back to the claude.ai login — it authenticates as an
    empty key and fails with an opaque 401."""
    p = tmp_path / ".env"
    p.write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import main
    assert main.load_secrets(p) == []
    assert "ANTHROPIC_API_KEY" not in os.environ


# --- the trap: config.py is imported by everything ----------------------------

def test_importing_config_does_not_export_the_connection_string():
    """pipeline/config.py used to call load_dotenv() at import.

    Fixing main.py alone would NOT have delivered the guarantee: config is
    imported by every scratch script, every pipeline module and every test, so
    its import-time load_dotenv() re-exported SYNAPSE_CONN_STR into the
    environment by a side door. Whoever removes this test should first check
    that the side door is still shut.
    """
    from pipeline import config

    src = inspect.getsource(config)
    assert "load_dotenv" not in calls_named(src), (
        "pipeline/config.py must not load .env into os.environ at import — "
        "every scratch script imports it")
    assert not hasattr(config, "SYNAPSE_CONN_STR"), (
        "config must not hold the connection string as module state; "
        "pull.synapse_conn_str() reads it at call time")


# --- 10. pull reads at call time ----------------------------------------------

def test_pull_reads_the_connection_string_at_call_time(fake_env, monkeypatch):
    """Spec test 10. Read inside the function, from the file, never assigned to
    os.environ — so the value exists only for the life of the call."""
    from pipeline import pull

    monkeypatch.setattr(pull, "ENV_PATH", fake_env)
    got = pull.synapse_conn_str()

    assert got == SENTINEL                       # it can still connect
    assert "SYNAPSE_CONN_STR" not in os.environ  # but nothing inherited it


def test_pull_uses_dotenv_values_not_load_dotenv():
    """load_dotenv() mutates os.environ as a side effect; dotenv_values() does
    not. The distinction IS the control here, so it is asserted directly."""
    from pipeline import pull

    called = calls_named(inspect.getsource(pull.synapse_conn_str))
    assert "dotenv_values" in called
    assert "load_dotenv" not in called
    src = inspect.getsource(pull.synapse_conn_str)
    assert "os.environ[" not in src and "environ.setdefault" not in src


def test_a_missing_connection_string_fails_with_a_pointer_to_the_env_file(tmp_path, monkeypatch):
    """The failure a new operator hits first. It must name the file to edit."""
    from pipeline import pull

    monkeypatch.setattr(pull, "ENV_PATH", tmp_path / "absent.env")
    with pytest.raises(RuntimeError, match=r"\.env"):
        pull.synapse_conn_str()


def test_no_module_leaks_the_connection_string_into_the_environment():
    """A sweep, so a NEW module cannot quietly reintroduce the side door that
    config.py had. Anything that needs the string calls pull.synapse_conn_str().
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for py in list((root / "pipeline").rglob("*.py")) + list((root / "agent").rglob("*.py")):
        text = py.read_text(encoding="utf-8", errors="replace")
        try:
            called = calls_named(text)
        except SyntaxError:                       # not ours to police
            continue
        if "load_dotenv" in called:
            offenders.append(f"{py.relative_to(root)}: calls load_dotenv")
        if "environ[\"SYNAPSE_CONN_STR\"]" in text or "environ['SYNAPSE_CONN_STR']" in text:
            offenders.append(f"{py.relative_to(root)}: assigns SYNAPSE_CONN_STR")
    assert not offenders, "; ".join(offenders)
