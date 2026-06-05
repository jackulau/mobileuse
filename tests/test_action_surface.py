"""D5 — action-verb surface drift guard.

Three independent verb lists used to drift apart with nothing keeping them in sync:
``agent_loop.ACTION_VERBS`` (the LLM-facing whitelist), ``record_replay.RECORDED_HELPERS``
(the journaled set), and the REAL platform helper functions. test_api_parity guards only
the helpers; this guards the other two against the helpers (no phantom verbs) AND against
test_api_parity's platform classification (a platform-only verb must be allowlisted, else
it silently vanishes from the other platform's prompt).
"""
import android_harness.helpers as ah
import iphone_harness.helpers as ih
from mobile_use.agent_loop import ACTION_VERBS
from mobile_use.record_replay import RECORDED_HELPERS
from tests.test_api_parity import ANDROID_ONLY, IOS_ONLY, _public_callables

IOS = _public_callables(ih)
ANH = _public_callables(ah)


def test_no_phantom_action_verbs():
    """Every ACTION_VERBS entry must exist on at least one platform helper."""
    phantom = [v for v in ACTION_VERBS if v not in IOS and v not in ANH]
    assert not phantom, f"ACTION_VERBS lists verbs that exist on no platform: {phantom}"


def test_no_phantom_recorded_helpers():
    """Every RECORDED_HELPERS entry must exist on at least one platform helper."""
    phantom = [v for v in RECORDED_HELPERS if v not in IOS and v not in ANH]
    assert not phantom, f"RECORDED_HELPERS lists verbs that exist on no platform: {phantom}"


def test_action_verbs_have_no_duplicates():
    assert len(ACTION_VERBS) == len(set(ACTION_VERBS)), "ACTION_VERBS has duplicate entries"


def test_platform_only_action_verbs_are_allowlisted():
    """A platform-only ACTION_VERBS verb must be classified in test_api_parity's
    allowlists — the single source of truth for intended cross-platform asymmetry."""
    for v in ACTION_VERBS:
        ios_has, anh_has = v in IOS, v in ANH
        if ios_has and not anh_has:
            assert v in IOS_ONLY, f"{v!r} is iOS-only but missing from the IOS_ONLY allowlist"
        elif anh_has and not ios_has:
            assert v in ANDROID_ONLY, (
                f"{v!r} is Android-only but missing from the ANDROID_ONLY allowlist")


def test_removed_phantom_verbs_stay_gone():
    # Regression: clear_text / scroll_into_view existed on no platform and were removed.
    assert "clear_text" not in ACTION_VERBS
    assert "scroll_into_view" not in ACTION_VERBS
