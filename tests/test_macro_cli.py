"""Tests for the `mobile-use macro` CLI subcommand."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(*args, env=None):
    """Invoke the cli module as a subprocess. Returns (rc, stdout, stderr)."""
    cmd = [sys.executable, "-m", "mobile_use.cli", "macro", *args]
    real_env = None
    if env:
        import os
        real_env = {**os.environ, **env}
    proc = subprocess.run(cmd, capture_output=True, text=True, env=real_env)
    return proc.returncode, proc.stdout, proc.stderr


# ---------- help / dispatch ----------

def test_macro_help_returns_zero():
    rc, out, _ = _run("--help")
    assert rc == 0
    assert "record" in out and "replay" in out and "list" in out and "show" in out


def test_macro_no_subcommand_prints_help():
    rc, out, _ = _run()
    assert rc == 0
    assert "Subcommands" in out


def test_macro_show_requires_name(tmp_path):
    rc, _, err = _run("show", "--dir", str(tmp_path))
    assert rc == 2
    assert "requires <name>" in err


def test_macro_show_missing_file(tmp_path):
    rc, _, err = _run("show", "nope", "--dir", str(tmp_path))
    assert rc == 2
    assert "not found" in err.lower()


# ---------- list ----------

def test_macro_list_empty_dir_does_not_create(tmp_path):
    empty = tmp_path / "nope"
    rc, out, _ = _run("list", "--dir", str(empty))
    assert rc == 0
    assert "No macros directory" in out
    assert not empty.exists()  # list doesn't create


def test_macro_list_shows_saved_macros(tmp_path):
    """Macros recorded into a dir should be listed with step counts."""
    # Synthesize a macro file directly (don't need a device)
    out = tmp_path / "flow_a.py"
    out.write_text(
        '"""recorded"""\n'
        'import fake as h\n'
        'h.tap_at_xy(1, 2)\n'
        'h.type_text("hi")\n'
    )
    out2 = tmp_path / "flow_b.py"
    out2.write_text('"""x"""\nimport fake as h\nh.swipe(0, 0, 0, 100)\n')

    rc, std, _ = _run("list", "--dir", str(tmp_path))
    assert rc == 0
    assert "flow_a" in std
    assert "flow_b" in std
    assert "2 steps" in std
    assert "1 steps" in std


def test_macro_list_annotated_marker(tmp_path):
    """Macros with a .py.jsonl sidecar should show '(annotated)' marker."""
    p = tmp_path / "ann.py"
    p.write_text("import fake as h\nh.tap_at_xy(1, 2)\n")
    p.with_suffix(".py.jsonl").write_text(json.dumps({
        "t": 0.0, "fn": "tap_at_xy", "args": [1, 2], "kwargs": {},
        "intent": "test",
    }) + "\n")

    rc, std, _ = _run("list", "--dir", str(tmp_path))
    assert rc == 0
    assert "(annotated)" in std


# ---------- show ----------

def test_macro_show_prints_script(tmp_path):
    p = tmp_path / "x.py"
    body = "import fake as h\nh.tap_at_xy(10, 20)\n"
    p.write_text(body)
    rc, std, _ = _run("show", "x", "--dir", str(tmp_path))
    assert rc == 0
    assert "tap_at_xy(10, 20)" in std


def test_macro_show_appends_py_extension(tmp_path):
    """`macro show flow` should find flow.py."""
    (tmp_path / "flow.py").write_text("# ok\nimport fake as h\nh.tap_at_xy(1, 2)\n")
    rc, std, _ = _run("show", "flow", "--dir", str(tmp_path))
    assert rc == 0
    assert "tap_at_xy" in std


# ---------- replay (literal, in-process) ----------

def test_replay_literal_missing_file(tmp_path):
    rc, _, err = _run("replay", "missing", "--literal", "--dir", str(tmp_path), "--ios")
    assert rc == 2
    assert "not found" in err.lower()


def test_replay_smart_and_literal_mutually_exclusive(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("import fake as h\nh.tap_at_xy(1, 2)\n")
    rc, _, err = _run("replay", "x", "--smart", "--literal",
                      "--dir", str(tmp_path), "--ios")
    # Note: --ios pins platform so cmd_replay runs; the smart+literal check happens
    # before any helpers import.
    assert rc == 2
    assert "either --smart or --literal" in err.lower()


# ---------- dispatch via top-level cli ----------

def test_cli_dispatches_macro_subcommand():
    """`mobile-use macro --help` via the top-level cli should not fail."""
    proc = subprocess.run(
        [sys.executable, "-m", "mobile_use.cli", "macro", "--help"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "record" in proc.stdout
