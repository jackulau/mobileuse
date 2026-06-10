"""goal/022 D1 — deterministic step-overhead bench (the evidence spine).

Counts the per-step side-effect costs of the autonomous loop through a fully
mocked harness: session-file writes, collector file operations, and pre-act
dismiss round-trips. Counts are asserted as exact regression guards; wall-clock
ms is REPORTED only, never asserted (deterministic under any load).

The baselines tighten as goal/022 optimization deliverables land:
  session_saves     D3 — skip the unchanged current_app save
  collector_copy2   D2 — hash-first, content-addressed screenshot dedupe
  collector_hashes  D2 — one streaming hash, reused
  preact_dismiss    D4 — MU_PREACT_DISMISS=snapshot gating
"""
import json
import sys
import time
import types

STEPS = 3

# Expected side-effect counts for one 3-step run() (exact, deterministic).
# Each later goal/022 deliverable tightens its line — see module docstring.
EXPECTED = {
    "session_saves": 6,      # 2/step: current_app setter save + record_action save
    "collector_copy2": 1,    # D2: content-addressed — identical screen copied once
    "collector_hashes": 3,   # 1/step: streaming hash decides the dedupe (kept)
    "preact_dismiss": 3,     # 1/act: auto_dismiss_dialog before every action
    "action_calls": 3,       # the actual tap per step (floor — never goes below)
    "collector_rows": 3,     # 1 JSONL row per perceive (shape-stable across D2)
}


def writes_per_step(counts, steps=STEPS):
    """Tracked file-writes per step (session saves + collector copies)."""
    return (counts["session_saves"] + counts["collector_copy2"]) / steps


def _fake_helpers(shot_path):
    m = types.ModuleType("fake_helpers")
    calls = []
    dismissals = []

    tree = [
        # cx/cy only (no x/y/w/h boxes) so _maybe_capture_detection stays out of
        # the counts — detection capture has its own tests.
        {"type": "Button", "label": "Search", "cx": 100, "cy": 50,
         "clickable": True, "visible": True},
    ]

    def snapshot(visible_only=True):
        return {"screenshot_path": str(shot_path), "ui_tree": list(tree),
                "active_app": {"bundleId": "com.example"},
                "window_size": {"width": 390, "height": 844}, "alert": None}

    def auto_dismiss_dialog():
        dismissals.append(1)
        return False

    def tap_at_xy(x, y):
        calls.append(("tap_at_xy", x, y))
        return True

    for fn in (snapshot, auto_dismiss_dialog, tap_at_xy):
        setattr(m, fn.__name__, fn)
    m._calls = calls
    m._dismissals = dismissals
    return m


def _install(monkeypatch, shot_path, platform="ios"):
    h = _fake_helpers(shot_path)
    a = types.ModuleType("fake_admin")
    a.ensure_daemon = lambda *args, **kw: True
    pkg = "iphone_harness" if platform == "ios" else "android_harness"
    import importlib
    parent = importlib.import_module(pkg)
    monkeypatch.setitem(sys.modules, f"{pkg}.helpers", h)
    monkeypatch.setitem(sys.modules, f"{pkg}.admin", a)
    monkeypatch.setattr(parent, "helpers", h, raising=False)
    monkeypatch.setattr(parent, "admin", a, raising=False)
    return h


def _run_counted(monkeypatch, tmp_path):
    """Drive a STEPS-step run() with every side-effect counter attached."""
    monkeypatch.setenv("MU_PERCEPTION_CACHE", "0")   # deterministic: LLM every step
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("mobile_use.collector.DATA_DIR", tmp_path / "training")

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG fake-but-stable-bytes")
    h = _install(monkeypatch, shot)

    import mobile_use.collector as collector_mod
    from mobile_use.agent_loop import AgentLoop
    from mobile_use.session import Session

    counts = {"session_saves": 0, "collector_copy2": 0, "collector_hashes": 0}

    orig_save = Session.save

    def counting_save(self):
        counts["session_saves"] += 1
        return orig_save(self)

    monkeypatch.setattr(Session, "save", counting_save)

    orig_copy2 = collector_mod.shutil.copy2

    def counting_copy2(src, dst, **kw):
        counts["collector_copy2"] += 1
        return orig_copy2(src, dst, **kw)

    monkeypatch.setattr(collector_mod.shutil, "copy2", counting_copy2)

    orig_hash = collector_mod._file_hash

    def counting_hash(path, *a, **kw):
        counts["collector_hashes"] += 1
        return orig_hash(path, *a, **kw)

    monkeypatch.setattr(collector_mod, "_file_hash", counting_hash)

    loop = AgentLoop(platform="ios", session_name="bench", collect=True)

    def llm(prompt):
        return '{"fn": "tap_at_xy", "kwargs": {"x": 100, "y": 50}}'

    t0 = time.perf_counter()
    res = loop.run("tap the search button", llm, max_steps=STEPS)
    run_ms = (time.perf_counter() - t0) * 1e3

    counts["preact_dismiss"] = len(h._dismissals)
    counts["action_calls"] = len(h._calls)
    rows = [json.loads(line) for line in
            open(loop.collector.data_path, encoding="utf-8").read().splitlines()]
    counts["collector_rows"] = len(rows)

    # ms is REPORTED for humans, never asserted.
    print(f"\n[step-overhead] steps={STEPS} run_ms={run_ms:.1f} "
          f"writes_per_step={writes_per_step(counts):.2f} counts={counts}")
    return res, counts, rows


def test_step_overhead_counts(monkeypatch, tmp_path):
    res, counts, _rows = _run_counted(monkeypatch, tmp_path)
    assert res["status"] == "max_steps" and res["timings"]["steps"] == STEPS
    for key, expected in EXPECTED.items():
        assert counts[key] == expected, (
            f"{key}: expected {expected}, got {counts[key]} — a perf regression "
            f"(or an optimization landed without tightening EXPECTED)")
    assert writes_per_step(counts) == (
        (EXPECTED["session_saves"] + EXPECTED["collector_copy2"]) / STEPS)


def test_collector_row_shape_stable(monkeypatch, tmp_path):
    """D2 backward-compat guard: perception rows keep their consumer-facing keys."""
    _res, _counts, rows = _run_counted(monkeypatch, tmp_path)
    for row in rows:
        assert {"timestamp", "session", "screenshot", "ui_tree",
                "success"} <= set(row)
        assert row["screenshot_hash"]
