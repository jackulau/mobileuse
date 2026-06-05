"""D8 — `mobile-use selfcheck`: device-free harness self-validation.

Covers the dep-rung matrix shape, the runtime action-surface check (agrees with the D5
drift guard), the device-free training smoke, and the healthy/unhealthy exit codes.
"""
import mobile_use.selfcheck as sc


def test_dep_rung_matrix_shape():
    rungs = sc.dep_rung_matrix()
    names = [r[0] for r in rungs]
    assert names == ["yolo_detector", "template_matcher",
                     "accessibility_tree", "vlm_fallback"]
    for _name, ok, detail in rungs:
        assert isinstance(ok, bool) and isinstance(detail, str) and detail
    # tree + VLM are structurally always-on
    assert rungs[2][1] is True and rungs[3][1] is True


def test_action_surface_is_clean():
    # After D5 there are no phantom verbs — the runtime check agrees with the tests.
    assert sc.action_surface_issues() == []


def test_training_smoke_builds_nonempty_dataset():
    ok, detail = sc.training_smoke()
    assert ok is True
    assert "dataset" in detail and "imgs" in detail


def test_selfcheck_main_healthy_exit_zero(capsys):
    rc = sc.selfcheck_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "healthy" in out and "selfcheck" in out


def test_selfcheck_help_exits_zero(capsys):
    assert sc.selfcheck_main(["--help"]) == 0
    assert "selfcheck" in capsys.readouterr().out


def test_selfcheck_flags_phantom_verb(monkeypatch):
    # If a phantom verb sneaks back into ACTION_VERBS, selfcheck must FAIL (exit 1).
    from mobile_use import agent_loop
    monkeypatch.setattr(agent_loop, "ACTION_VERBS",
                        [*agent_loop.ACTION_VERBS, "totally_fake_verb"])
    assert sc.action_surface_issues()          # the inconsistency is detected
    assert sc.selfcheck_main([]) == 1          # and it fails the overall check
