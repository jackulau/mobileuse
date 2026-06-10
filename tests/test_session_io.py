"""goal/022 D3 — session save dedupe.

perceive() assigns session.current_app on EVERY step; the setter used to rewrite
the whole session JSON each time even when the foreground app hadn't changed —
2 full-file writes per step in steady state. Unchanged assignment is now a no-op;
record_action still persists every action (crash durability unchanged).
"""
import pytest

import mobile_use.session as session_mod
from mobile_use.session import Session


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSION_DIR", tmp_path / "sessions")
    counts = {"saves": 0}
    orig_save = Session.save

    def counting_save(self):
        counts["saves"] += 1
        return orig_save(self)

    monkeypatch.setattr(Session, "save", counting_save)
    return Session(name="io-test", platform="ios"), counts


def test_unchanged_current_app_skips_save(sess):
    s, counts = sess
    app = {"bundleId": "com.example"}
    s.current_app = app
    assert counts["saves"] == 1
    s.current_app = {"bundleId": "com.example"}   # equal value, fresh dict
    s.current_app = app
    assert counts["saves"] == 1, "unchanged assignment must not rewrite the file"
    assert s.current_app == app


def test_changed_current_app_still_saves(sess):
    s, counts = sess
    s.current_app = {"bundleId": "com.a"}
    s.current_app = {"bundleId": "com.b"}
    assert counts["saves"] == 2
    assert s.current_app == {"bundleId": "com.b"}


def test_none_transition_saves_once(sess):
    s, counts = sess
    s.current_app = None        # already None at init — no-op
    assert counts["saves"] == 0
    s.current_app = {"bundleId": "com.a"}
    s.current_app = None        # real transition back
    assert counts["saves"] == 2


def test_record_action_always_persists(sess):
    s, counts = sess
    s.record_action("tap(x=1, y=2)")
    s.record_action("tap(x=1, y=2)")  # identical action still appends + saves
    assert counts["saves"] == 2
    assert len(s.action_history) == 2
    # State survives a reload from disk.
    fresh = Session(name="io-test", platform="ios")
    assert len(fresh.action_history) == 2
