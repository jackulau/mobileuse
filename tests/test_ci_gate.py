"""D20 — guard the CI lint gate and honest error messages.

The ruff CI step used to run with `|| true` (advisory), so a real lint
regression — including an F-category undefined-name bug — passed silently. The
tree is ruff-clean now, so the gate is blocking. cli.py also used to print a
false 'Agent loop not yet implemented' on ImportError even though run_agent is
implemented. These asserts keep both from regressing.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_ci_ruff_gate_is_blocking():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    # The ruff step must not be neutered with `|| true`.
    ruff_lines = [ln for ln in ci.splitlines() if "ruff check" in ln]
    assert ruff_lines, "ci.yml should run ruff check"
    for ln in ruff_lines:
        assert "|| true" not in ln, f"ruff gate must be blocking, not advisory: {ln!r}"


def test_cli_has_no_false_not_implemented_message():
    cli = (REPO / "mobile_use" / "cli.py").read_text()
    assert "not yet implemented" not in cli, (
        "the agent loop IS implemented — the false 'not yet implemented' message must be gone"
    )


def test_ci_matrix_includes_all_three_oses():
    # windows-latest is the ground truth that the daemon transport/routing/
    # liveness/teardown work on Windows; it must not be silently dropped.
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert os_name in ci, f"CI matrix must include {os_name}"


def test_ci_adb_step_stays_linux_gated():
    # The apt-based adb install is Linux-only — it must stay gated so it never
    # runs on the Windows or macOS legs (no apt-get there).
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "android-tools-adb" in ci, "the Linux adb smoke step should still exist"
    assert "runner.os == 'Linux'" in ci, (
        "the adb install step must stay gated on `runner.os == 'Linux'`"
    )


# ---- real out-of-box install exercised (not just mocked) ----------------------

def test_ci_has_docker_fresh_container_job():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "Dockerfile.linux-test" in ci, "CI must build the fresh-container image"
    assert "docker build" in ci
    assert "docker run --rm mobile-use-linux-test" in ci


def test_ci_has_non_editable_install_smoke():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "mu-fresh-venv" in ci, "CI must do a fresh-venv NON-editable install"
    assert "pip -q install ." in ci
    assert "test -x /tmp/mu-fresh-venv/bin/mobile-use" in ci


def test_ci_has_xdist_dev_extras_lane():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "pip install -e .[dev]" in ci, "CI must exercise the [dev] extras install"
    assert "-n auto" in ci, "CI must run the documented pytest -n auto lane"
