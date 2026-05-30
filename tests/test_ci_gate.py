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
