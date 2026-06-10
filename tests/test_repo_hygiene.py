"""Fresh-clone hygiene: no tracked path may have a component starting with '-'.

A file literally named `--help` once landed in the repo root (collector output
captured under the wrong argv). Such names break naive shell globs and look
broken in listings, so the guard pins them out of the tree for good.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_tracked_path_component_starts_with_dash():
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    offenders = [
        p for p in out.stdout.split("\0")
        if p and any(part.startswith("-") for part in p.split("/"))
    ]
    assert offenders == [], f"dash-prefixed tracked paths: {offenders}"
