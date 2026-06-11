"""D17 — computed cross-platform API parity guard.

Parity was only checked for 3 nav helpers (test_nav_parity), so a future helper
added to one platform but not the other would ship silently — undercutting the
"broad compatibility" bar. This computes each module's public callable set and
asserts:
  1. a CORE set of cross-platform verbs/queries exists on BOTH platforms, and
  2. every platform-only helper is in an explicit allowlist — so any NEW
     asymmetry fails the test until it's intentionally allowlisted or mirrored.
"""
import android_harness.helpers as ah
import iphone_harness.helpers as ih


def _public_callables(mod):
    return {n for n in dir(mod) if not n.startswith("_") and callable(getattr(mod, n))}


# Cross-platform helpers that MUST exist on both — the shared action/query API.
CORE = {
    # touch / gestures
    "tap", "tap_safe", "tap_at_xy", "long_press", "double_tap",
    "swipe", "scroll", "scroll_by",
    # text input
    "type_text", "set_value", "send_keys", "press_enter", "hide_keyboard",
    # navigation / lifecycle
    "press_home", "press_back", "press_recents",
    "launch_app", "activate_app", "terminate_app", "is_app_installed", "app_state",
    "install_app", "uninstall_app", "push_file", "pull_file",
    # device control
    "open_url", "get_clipboard", "set_clipboard", "set_location",
    "get_orientation", "set_orientation",
    # perception / dialogs / waits
    "screenshot", "ui_tree", "find", "find_all", "active_app", "window_size",
    "alert", "alert_accept", "alert_dismiss", "auto_dismiss_dialog",
    "wait", "wait_for", "wait_for_element", "wait_for_app", "snapshot",
}

# Intentionally platform-specific helpers. Any public callable on exactly one
# platform must appear here, else the parity test fails (catches silent drift).
IOS_ONLY = {
    "open_control_center", "close_control_center", "ensure_cc_tile",
    "set_assistive_touch", "native_screenshot", "pick_wheel",
    "press_return", "swipe_back", "open_app_switcher",
}
ANDROID_ONLY = {
    "open_notifications", "close_notifications", "grant_permission",
    "key_event", "press_search",
}


def test_core_api_present_on_both_platforms():
    ios = _public_callables(ih)
    anh = _public_callables(ah)
    assert CORE <= ios, f"iOS helpers missing CORE verbs: {sorted(CORE - ios)}"
    assert CORE <= anh, f"Android helpers missing CORE verbs: {sorted(CORE - anh)}"


def test_no_unexpected_platform_asymmetry():
    ios = _public_callables(ih)
    anh = _public_callables(ah)
    ios_only = ios - anh
    anh_only = anh - ios
    unexpected_ios = ios_only - IOS_ONLY
    unexpected_anh = anh_only - ANDROID_ONLY
    assert not unexpected_ios, (
        f"iOS-only helpers not in the allowlist (add to both platforms or to IOS_ONLY): "
        f"{sorted(unexpected_ios)}"
    )
    assert not unexpected_anh, (
        f"Android-only helpers not in the allowlist (add to both platforms or to ANDROID_ONLY): "
        f"{sorted(unexpected_anh)}"
    )


def test_allowlists_are_accurate():
    """The allowlists must not claim a helper that is actually shared/absent."""
    ios = _public_callables(ih)
    anh = _public_callables(ah)
    assert IOS_ONLY <= ios and not (IOS_ONLY & anh), "IOS_ONLY allowlist is stale"
    assert ANDROID_ONLY <= anh and not (ANDROID_ONLY & ios), "ANDROID_ONLY allowlist is stale"
