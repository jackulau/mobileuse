"""D1 — version normalization + support matrix (mobile_use/versions.py).

Device-free: pure functions over version strings. No Appium, no device.
"""
import pytest

from mobile_use import versions as V


@pytest.mark.parametrize("raw, expected", [
    ("18.3.2", (18, 3, 2)),
    ("14", (14,)),
    ("26.0", (26, 0)),
    ("iOS 17.1", (17, 1)),
    ("Android 14", (14,)),
    ("", ()),
    (None, ()),
    ("latest", ()),
    ("  9.0.1  ", (9, 0, 1)),
])
def test_normalize_version(raw, expected):
    assert V.normalize_version(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("18.3.2", 18),
    ("14", 14),
    ("", None),
    (None, None),
    ("nonsense", None),
])
def test_major(raw, expected):
    assert V.major(raw) == expected


@pytest.mark.parametrize("value, needs", [
    ("17.0", True),
    ("18.3.2", True),
    ("26.0", True),
    ("16.7.1", False),
    ("15.0", False),
    (17, True),       # bare major int
    (16, False),
    ((18, 1), True),  # tuple
    ("", False),      # unparseable -> don't claim a tunnel is needed
    (None, False),
])
def test_ios_needs_tunnel(value, needs):
    assert V.ios_needs_tunnel(value) is needs


def test_ios_tunnel_boundary_is_17():
    # The cliff is exactly iOS 17 (RemoteXPC). 16 = classic lockdownd, no tunnel.
    assert V.ios_needs_tunnel(V.IOS_TUNNEL_MIN_MAJOR) is True
    assert V.ios_needs_tunnel(V.IOS_TUNNEL_MIN_MAJOR - 1) is False


def test_version_support_status_ios_supported_carries_tunnel_note():
    level, detail = V.version_support_status("ios", "18.3.2")
    assert level == V.SUPPORTED
    assert "tunnel" in detail.lower()


def test_version_support_status_ios_old_no_tunnel_note():
    level, detail = V.version_support_status("ios", "14.0")
    assert level == V.TOO_OLD
    assert "tunnel" not in detail.lower()  # iOS 14 is pre-RemoteXPC


def test_version_support_status_ios_newer_is_untested_not_failure():
    level, detail = V.version_support_status("ios", str(V.IOS_MAX_MAJOR + 1))
    assert level == V.UNTESTED_NEWER
    assert "untested" in detail.lower()


def test_version_support_status_android_range():
    assert V.version_support_status("android", "14")[0] == V.SUPPORTED
    assert V.version_support_status("android", str(V.ANDROID_MIN_MAJOR - 1))[0] == V.TOO_OLD
    assert V.version_support_status("android", str(V.ANDROID_MAX_MAJOR + 1))[0] == V.UNTESTED_NEWER


def test_version_support_status_unknown_platform_and_version():
    assert V.version_support_status("windowsphone", "8")[0] == V.UNKNOWN
    assert V.version_support_status("ios", "garbage")[0] == V.UNKNOWN


@pytest.mark.parametrize("value, level", [
    ("3.0.0", V.SUPPORTED),
    ("2.11.5", V.SUPPORTED),       # Appium 2 works, just not recommended
    ("1.22.0", V.TOO_OLD),         # Appium 1 unsupported
    ("garbage", V.UNKNOWN),
])
def test_appium_support_status(value, level):
    assert V.appium_support_status(value)[0] == level


def test_appium_2_detail_recommends_3():
    _level, detail = V.appium_support_status("2.11.5")
    assert "recommended" in detail.lower()
    assert "3" in detail


def test_support_matrix_rows_cover_all_components():
    rows = V.support_matrix_rows()
    comps = {r[0] for r in rows}
    assert {"iOS", "Android", "Appium server", "xcuitest-driver", "uiautomator2-driver"} <= comps
    # Every row is a 3-tuple of non-empty strings.
    for comp, supported, notes in rows:
        assert comp and supported and notes


def test_support_matrix_text_mentions_tunnel_and_ranges():
    text = V.support_matrix_text()
    assert "Supported versions" in text
    assert str(V.IOS_MAX_MAJOR) in text
    assert "tunnel" in text.lower()
