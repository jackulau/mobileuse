"""`python3 -m mobile_use` must work wherever the package is importable.

Console scripts land in bin dirs login shells often lack (framework/user pip
installs — e.g. /Library/Frameworks/Python.framework/Versions/3.X/bin), so the
module form is the PATH-proof invocation doctor points users at.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_module(*args):
    return subprocess.run(
        [sys.executable, "-m", "mobile_use", *args],
        capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
    )


def test_module_help_exits_zero():
    out = _run_module("--help")
    assert out.returncode == 0, out.stderr
    assert "mobile-use" in out.stdout


def test_module_no_args_prints_help():
    out = _run_module()
    assert out.returncode == 0, out.stderr
    assert "mobile-use" in out.stdout


def test_module_version():
    out = _run_module("--version")
    assert out.returncode == 0, out.stderr
    assert "mobile-use" in out.stdout
