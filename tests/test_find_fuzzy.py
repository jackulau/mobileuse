"""Tests for find_fuzzy() in both platforms."""


def _ios_tree():
    return [
        {"type": "XCUIElementTypeButton", "label": "Send", "name": "sendButton", "value": "", "visible": True},
        {"type": "XCUIElementTypeButton", "label": "Cancel", "name": "cancelButton", "value": "", "visible": True},
        {"type": "XCUIElementTypeStaticText", "label": "Send Message", "name": "", "value": "", "visible": True},
        {"type": "XCUIElementTypeCell", "label": "Settings", "name": "settingsCell", "value": "", "visible": True},
        {"type": "XCUIElementTypeTextField", "label": "", "name": "searchField", "value": "send query", "visible": True},
        {"type": "XCUIElementTypeButton", "label": "Hidden", "name": "hidden", "value": "", "visible": False},
    ]


def _android_tree():
    return [
        {"type": "android.widget.Button", "text": "Send", "resource_id": "com.app:id/send_btn", "content_desc": "", "visible": True},
        {"type": "android.widget.Button", "text": "Cancel", "resource_id": "com.app:id/cancel_btn", "content_desc": "", "visible": True},
        {"type": "android.widget.TextView", "text": "Send Message", "resource_id": "", "content_desc": "", "visible": True},
        {"type": "android.widget.LinearLayout", "text": "", "resource_id": "com.app:id/settings_row", "content_desc": "Settings", "visible": True},
        {"type": "android.widget.EditText", "text": "send query", "resource_id": "com.app:id/search", "content_desc": "", "visible": True},
        {"type": "android.widget.Button", "text": "Hidden", "resource_id": "", "content_desc": "", "visible": False},
    ]


def test_ios_find_fuzzy_exact():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_ios_tree())
    assert len(results) >= 1
    assert results[0]["label"] == "Send"


def test_ios_find_fuzzy_substring():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_ios_tree())
    labels = [r["label"] for r in results]
    assert "Send Message" in labels


def test_ios_find_fuzzy_name_match():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_ios_tree())
    names = [r["name"] for r in results]
    assert "sendButton" in names


def test_ios_find_fuzzy_value_match():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_ios_tree())
    values = [r.get("value", "") for r in results]
    assert "send query" in values


def test_ios_find_fuzzy_case_insensitive():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("SEND", _tree=_ios_tree())
    assert len(results) >= 1
    assert results[0]["label"] == "Send"


def test_ios_find_fuzzy_type_filter():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", type="XCUIElementTypeButton", _tree=_ios_tree())
    for r in results:
        assert r["type"] == "XCUIElementTypeButton"


def test_ios_find_fuzzy_no_match():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("nonexistent", _tree=_ios_tree())
    assert results == []


def test_ios_find_fuzzy_skips_invisible():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("hidden", _tree=_ios_tree())
    assert len(results) == 0


def test_ios_find_fuzzy_ordering():
    from iphone_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_ios_tree())
    assert results[0]["label"] == "Send"


def test_android_find_fuzzy_exact():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_android_tree())
    assert len(results) >= 1
    assert results[0]["text"] == "Send"


def test_android_find_fuzzy_substring():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_android_tree())
    texts = [r["text"] for r in results]
    assert "Send Message" in texts


def test_android_find_fuzzy_content_desc():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("settings", _tree=_android_tree())
    descs = [r.get("content_desc", "") for r in results]
    assert "Settings" in descs


def test_android_find_fuzzy_resource_id():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("send", _tree=_android_tree())
    ids = [r.get("resource_id", "") for r in results]
    assert any("send" in rid for rid in ids)


def test_android_find_fuzzy_case_insensitive():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("SEND", _tree=_android_tree())
    assert len(results) >= 1


def test_android_find_fuzzy_type_filter():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("send", type="android.widget.Button", _tree=_android_tree())
    for r in results:
        assert r["type"] == "android.widget.Button"


def test_android_find_fuzzy_no_match():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("nonexistent", _tree=_android_tree())
    assert results == []


def test_android_find_fuzzy_skips_invisible():
    from android_harness.helpers import find_fuzzy
    results = find_fuzzy("hidden", _tree=_android_tree())
    assert len(results) == 0
