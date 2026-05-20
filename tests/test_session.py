"""Unit tests for mobile_use.session — uses tmp dirs to avoid side effects."""
import pytest


def test_session_create(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="test1", platform="ios")
    assert s.name == "test1"
    assert s.platform == "ios"
    assert s.current_app is None
    assert s.action_history == []
    assert s.navigation_stack == []
    assert s.active_goals == []


def test_session_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="persist", platform="android")
    s.current_app = {"package": "com.example"}
    s.save()

    s2 = Session(name="persist", platform="android")
    assert s2.current_app == {"package": "com.example"}


def test_session_record_action(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="actions", platform="ios")
    s.record_action("tap(find(text='Send'))", result="OK")
    s.record_action("screenshot()", error="timeout")
    assert len(s.action_history) == 2
    assert s.action_history[0]["result"] == "OK"
    assert s.action_history[1]["error"] == "timeout"


def test_session_navigation_stack(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="nav", platform="ios")
    s.push_screen("home")
    s.push_screen("inbox")
    assert len(s.navigation_stack) == 2
    popped = s.pop_screen()
    assert popped["screen"] == "inbox"


def test_session_learn_element(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="learn", platform="ios")
    s.learn_element("send_btn", "name == 'sendButton'")
    assert s.get_element("send_btn") == "name == 'sendButton'"
    assert s.get_element("nonexistent") is None


def test_session_goals(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="goals", platform="ios")
    s.set_goal("Send a text message")
    assert len(s.active_goals) == 1
    s.complete_goal()
    assert len(s.active_goals) == 0


def test_session_summary_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="fresh", platform="ios")
    assert "Fresh session" in s.summary()


def test_session_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session

    s = Session(name="reset", platform="ios")
    s.record_action("tap()")
    s.set_goal("do thing")
    s.reset()
    assert s.action_history == []
    assert s.active_goals == []


def test_list_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("mobile_use.session.SESSION_DIR", tmp_path)
    from mobile_use.session import Session, list_sessions

    Session(name="a", platform="ios").save()
    Session(name="b", platform="android").save()
    names = list_sessions()
    assert "a" in names
    assert "b" in names
