# Task-specific helpers, auto-loaded into iphone-harness / android-harness / mobile-use
# globals at import time. Defined here, they appear in `-c` scripts without an import.
#
# Cross-platform cleanup + organization helpers. Each public function detects which
# platform's helpers are present in `sys.modules` and dispatches to that harness's
# helper API. They are importable as a plain module too (no device required) — the
# platform dispatch raises a clean RuntimeError only when an action that actually
# needs a device is invoked outside of a harness.
#
# Public surface (used by docs/demos/clean-and-organize-*.py and by domain skills):
#   list_installed_apps()    -> list[dict]
#   uninstall_app(id_or_label) -> dict
#   storage_summary()        -> dict
#   bulk_select(items, *, deletion_button="Delete", finder=None) -> int
#   confirm_destructive(label="Delete", timeout=4.0) -> bool
#
# Underscore-prefixed names are private — the harness loader skips them.

import os
import sys
import time


# ---- platform detection ----------------------------------------------------
#
# The harness imports this file and copies its public attributes into the
# harness's own globals. Function objects still have `__globals__` pointing to
# *this* module, so we can't see the harness's `appium` / `find` / `tap` from
# inside our functions by reading `globals()`. Instead we resolve the platform
# helper module via `sys.modules` at call time (after the harness has finished
# importing) and call its functions directly.

def _host_module():
    """Return the host harness helpers module, or None if running standalone.

    Cached on first call. Tries iphone_harness first because the iOS daemon
    is the more common single-platform target on macOS.
    """
    h = sys.modules.get("iphone_harness.helpers")
    if h is not None:
        return h
    return sys.modules.get("android_harness.helpers")


def _platform():
    """Return 'ios', 'android', or None."""
    h = _host_module()
    if h is None:
        return None
    return "ios" if h.__name__.startswith("iphone_harness") else "android"


def _h():
    """Return the host helpers module, raising a clear error if absent.

    All real-device actions go through this — the resulting object exposes
    `appium`, `find`, `tap`, etc. depending on which harness is loaded.
    """
    h = _host_module()
    if h is None:
        raise RuntimeError(
            "agent_helpers needs an active mobile-use harness "
            "(iphone-harness or android-harness). Run via the CLI, "
            "not as a plain `python` script."
        )
    return h


# ---- list installed apps ---------------------------------------------------

def list_installed_apps():
    """Return a list of installed user apps on the connected device.

    iOS: scrapes Settings -> General -> iPhone Storage. Each row carries
         the app name and (when populated) a size string.
         Returns [{label: str, size: str | None}].

    Android: uses `mobile: shell` to run `pm list packages -3` for third-party
         packages. If shell access is denied (Appium not started with
         --relaxed-security), falls back to scraping Settings -> Apps ->
         See all apps.
         Returns [{label: str, package: str | None, size: str | None}].
    """
    p = _platform()
    if p == "ios":
        return _ios_list_installed_apps()
    if p == "android":
        return _android_list_installed_apps()
    raise RuntimeError("list_installed_apps() requires iOS or Android harness loaded.")


def _ios_list_installed_apps():
    h = _h()
    h.appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    h.wait(0.6)
    h.appium("mobile: launchApp", bundleId="com.apple.Preferences")
    h.wait(2.0)

    def _tap_label(label):
        el = h.find(label=label, type="XCUIElementTypeCell") or h.find(
            label=label, type="XCUIElementTypeButton"
        )
        if el is None:
            h.scroll_by(dy=-300, velocity=400)
            h.wait(0.4)
            el = h.find(label=label, type="XCUIElementTypeCell") or h.find(
                label=label, type="XCUIElementTypeButton"
            )
        if el is None:
            raise RuntimeError(f"Couldn't find '{label}' row in Settings.")
        h.tap(el)

    _tap_label("General")
    h.wait(1.4)
    _tap_label("iPhone Storage")
    h.wait(3.0)  # iPhone Storage takes a beat to populate sizes.

    seen = {}
    for _ in range(40):
        try:
            h.invalidate_tree_cache()
        except AttributeError:
            pass
        for el in h.ui_tree(visible_only=True):
            if el.get("type") != "XCUIElementTypeCell":
                continue
            label = el.get("label", "")
            if not label:
                continue
            if any(skip in label for skip in (
                "Recommendations", "Used", "iPhone Storage", "iCloud"
            )):
                continue
            parts = [s.strip() for s in label.split(",")]
            if len(parts) >= 2 and any(unit in parts[-1] for unit in ("KB", "MB", "GB", "B")):
                seen.setdefault(parts[0], parts[-1])
            else:
                seen.setdefault(parts[0], None)
        before = len(seen)
        h.scroll_by(dy=-500, velocity=500)
        h.wait(0.4)
        if len(seen) == before:
            break

    return [{"label": k, "size": v} for k, v in seen.items()]


def _android_list_installed_apps():
    h = _h()
    # Try `mobile: shell` first — fast and exhaustive.
    try:
        out = h.appium("mobile: shell", command="pm", args=["list", "packages", "-3"])
        text = out.get("stdout", "") if isinstance(out, dict) else str(out)
        pkgs = [line.replace("package:", "").strip()
                for line in text.splitlines() if "package:" in line]
        if pkgs:
            return [{"label": pkg.split(".")[-1], "package": pkg, "size": None}
                    for pkg in pkgs]
    except Exception:
        pass

    # Fallback: scrape Settings -> Apps -> See all apps.
    h.appium("mobile: startActivity",
             package="com.android.settings",
             activity="com.android.settings.applications.ManageApplications")
    h.wait(2.0)

    seen = {}
    for _ in range(50):
        try:
            h.invalidate_tree_cache()
        except AttributeError:
            pass
        for el in h.ui_tree(visible_only=True):
            text = el.get("text", "")
            rid = el.get("resource_id", "")
            if rid.endswith(":id/app_name") and text:
                seen.setdefault(text, {"label": text, "package": None, "size": None})
        before = len(seen)
        h.scroll(direction="down")
        h.wait(0.4)
        if len(seen) == before:
            break

    return list(seen.values())


# ---- uninstall app ---------------------------------------------------------

def uninstall_app(id_or_label):
    """Uninstall an app by bundle id (iOS) or package / display label.

    Returns {ok: bool, action: 'uninstalled' | 'offloaded' | 'disabled' | 'blocked',
             reason: str | None}.
    """
    p = _platform()
    if p == "ios":
        return _ios_uninstall_app(id_or_label)
    if p == "android":
        return _android_uninstall_app(id_or_label)
    raise RuntimeError("uninstall_app() requires iOS or Android harness loaded.")


def _ios_uninstall_app(label):
    h = _h()
    h.appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    h.wait(0.5)
    h.appium("mobile: launchApp", bundleId="com.apple.Preferences")
    h.wait(1.8)

    for step in ("General", "iPhone Storage"):
        cell = h.find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            h.scroll_by(dy=-300, velocity=400); h.wait(0.4)
            cell = h.find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            return {"ok": False, "action": "blocked", "reason": f"missing '{step}' row"}
        h.tap(cell); h.wait(2.0)

    target = None
    for _ in range(40):
        try:
            h.invalidate_tree_cache()
        except AttributeError:
            pass
        for el in h.ui_tree(visible_only=True):
            if el.get("type") != "XCUIElementTypeCell":
                continue
            if el.get("label", "").startswith(label):
                target = el
                break
        if target is not None:
            break
        h.scroll_by(dy=-500, velocity=500); h.wait(0.3)
    if target is None:
        return {"ok": False, "action": "blocked",
                "reason": f"{label!r} not found in iPhone Storage"}

    h.tap(target); h.wait(1.5)

    delete = h.find(label="Delete App", type="XCUIElementTypeButton") or h.find(
        label="Delete App", type="XCUIElementTypeStaticText"
    )
    if delete is None:
        offload = h.find(label="Offload App", type="XCUIElementTypeButton")
        if offload is None:
            return {"ok": False, "action": "blocked",
                    "reason": "no Delete/Offload affordance — likely a system app"}
        return {"ok": False, "action": "blocked",
                "reason": "system app — only Offload available"}

    h.tap(delete); h.wait(0.8)
    confirmed = confirm_destructive("Delete App")
    return {
        "ok": confirmed,
        "action": "uninstalled" if confirmed else "blocked",
        "reason": None if confirmed else "confirmation dialog not found",
    }


def _android_uninstall_app(id_or_label):
    h = _h()
    if "." in id_or_label:
        try:
            ok = h.appium("mobile: removeApp", appId=id_or_label)
            if ok:
                return {"ok": True, "action": "uninstalled", "reason": None}
        except Exception:
            pass

    h.appium("mobile: startActivity",
             package="com.android.settings",
             activity="com.android.settings.applications.ManageApplications")
    h.wait(2.0)

    target = None
    for _ in range(50):
        try:
            h.invalidate_tree_cache()
        except AttributeError:
            pass
        for el in h.ui_tree(visible_only=True):
            if el.get("text") == id_or_label or id_or_label in el.get("text", ""):
                target = el
                break
        if target is not None:
            break
        h.scroll(direction="down"); h.wait(0.3)

    if target is None:
        return {"ok": False, "action": "blocked",
                "reason": f"{id_or_label!r} not found in Settings -> Apps"}

    h.tap(target); h.wait(1.5)
    uninstall = h.find(text="Uninstall") or h.find(content_desc="Uninstall")
    if uninstall is None:
        disable = h.find(text="Disable") or h.find(content_desc="Disable")
        if disable is None:
            return {"ok": False, "action": "blocked",
                    "reason": "neither Uninstall nor Disable button present"}
        return {"ok": False, "action": "blocked",
                "reason": "only Disable available (system app)"}
    h.tap(uninstall); h.wait(0.8)
    confirmed = confirm_destructive("OK") or confirm_destructive("Uninstall")
    return {
        "ok": confirmed,
        "action": "uninstalled" if confirmed else "blocked",
        "reason": None if confirmed else "confirmation dialog not found",
    }


# ---- storage summary -------------------------------------------------------

def storage_summary():
    """Return {used, free, total, raw} as display strings (not bytes)."""
    p = _platform()
    if p == "ios":
        return _ios_storage_summary()
    if p == "android":
        return _android_storage_summary()
    raise RuntimeError("storage_summary() requires iOS or Android harness loaded.")


def _ios_storage_summary():
    h = _h()
    h.appium("mobile: terminateApp", bundleId="com.apple.Preferences")
    h.wait(0.5)
    h.appium("mobile: launchApp", bundleId="com.apple.Preferences")
    h.wait(1.8)

    for step in ("General", "iPhone Storage"):
        cell = h.find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            h.scroll_by(dy=-300, velocity=400); h.wait(0.4)
            cell = h.find(label=step, type="XCUIElementTypeCell")
        if cell is None:
            raise RuntimeError(f"missing '{step}' row")
        h.tap(cell); h.wait(2.0)

    try:
        h.invalidate_tree_cache()
    except AttributeError:
        pass
    used = free = total = None
    raw_bits = []
    for el in h.ui_tree(visible_only=True):
        text = (el.get("value") or el.get("label") or "").strip()
        if not text:
            continue
        raw_bits.append(text)
        if "Used" in text and "of" in text and "GB" in text:
            parts = text.split()
            try:
                used_idx = parts.index("Used")
                of_idx = parts.index("of", used_idx)
                used = parts[used_idx + 1] + " " + parts[used_idx + 2]
                total = parts[of_idx + 1] + " " + parts[of_idx + 2]
            except (ValueError, IndexError):
                pass
    return {"used": used, "free": free, "total": total, "raw": " | ".join(raw_bits[:20])}


def _android_storage_summary():
    h = _h()
    h.appium("mobile: startActivity",
             package="com.android.settings",
             activity="com.android.settings.Settings$StorageDashboardActivity")
    h.wait(2.0)
    try:
        h.invalidate_tree_cache()
    except AttributeError:
        pass

    used = free = total = None
    raw_bits = []
    for el in h.ui_tree(visible_only=True):
        text = (el.get("text") or el.get("content_desc") or "").strip()
        if not text:
            continue
        raw_bits.append(text)
        low = text.lower()
        if "used" in low and "of" in low and "GB" in text:
            parts = text.split()
            for i, w in enumerate(parts):
                if w.lower() == "used" and i >= 2:
                    used = parts[i - 2] + " " + parts[i - 1]
                if w.lower() == "of" and i + 2 < len(parts):
                    total = parts[i + 1] + " " + parts[i + 2]
        if low.startswith("free"):
            parts = text.split()
            if len(parts) >= 3:
                free = parts[1] + " " + parts[2]
    return {"used": used, "free": free, "total": total, "raw": " | ".join(raw_bits[:20])}


# ---- bulk select -----------------------------------------------------------

def bulk_select(items, *, deletion_button="Delete", finder=None):
    """Enter Select mode, tap each item, hit Delete.

    `items`: iterable of finder-arguments (labels / texts).
    `finder`: optional callable(item) -> element dict; defaults to platform-
              appropriate `find(label=...)` or `find(text=...)`.
    `deletion_button`: bottom-bar / alert button label to tap when finished.

    Returns count of items successfully tapped.
    """
    p = _platform()
    h = _h()

    if p == "ios":
        candidates = [
            lambda: h.find(label="Select", type="XCUIElementTypeButton"),
            lambda: h.find(label="Edit", type="XCUIElementTypeButton"),
        ]
    else:  # android
        candidates = [
            lambda: h.find(text="Select"),
            lambda: h.find(content_desc="Select"),
            lambda: h.find(content_desc="More options"),
        ]
    select_btn = None
    for c in candidates:
        select_btn = c()
        if select_btn is not None:
            break
    if select_btn is not None:
        h.tap(select_btn); h.wait(0.4)

    n = 0
    for item in items:
        if finder:
            el = finder(item)
        elif p == "ios":
            el = h.find(label=item)
        else:
            el = h.find(text=item)
        if el is None:
            continue
        h.tap(el); h.wait(0.2); n += 1

    if n == 0:
        return 0

    if p == "ios":
        btn = h.find(label=deletion_button, type="XCUIElementTypeButton") or h.find(
            label=deletion_button, type="XCUIElementTypeStaticText"
        )
    else:
        btn = h.find(text=deletion_button) or h.find(content_desc=deletion_button)
    if btn is not None:
        h.tap(btn); h.wait(0.6)
        confirm_destructive(deletion_button)
    return n


# ---- destructive confirmation ---------------------------------------------

def confirm_destructive(label="Delete", timeout=4.0):
    """Wait for a confirmation dialog and tap the button matching `label`.
    Returns True if tapped, False on timeout. No-ops cleanly without a host
    harness (returns False)."""
    h = _host_module()
    if h is None:
        return False

    p = "ios" if h.__name__.startswith("iphone_harness") else "android"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p == "ios":
            btn = h.find(label=label, type="XCUIElementTypeButton")
        else:
            btn = h.find(text=label) or h.find(content_desc=label)
        if btn is not None:
            h.tap(btn)
            return True
        time.sleep(0.2)
    return False
