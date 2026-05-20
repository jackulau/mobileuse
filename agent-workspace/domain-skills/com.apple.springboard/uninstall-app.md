# SpringBoard — uninstall an app from the home screen

Bundle id: `com.apple.springboard`. Field-tested on iOS 18.3.

There are three uninstall paths on iOS. Pick by which one matches the app's
current state:

1. **Home-screen long-press** — app icon is visible on a home screen page.
2. **App Library long-press** — app was removed from the home screen but
   still installed (`app-library.md`).
3. **Settings → General → iPhone Storage** — works for everything, including
   apps you can't find visually. See
   `domain-skills/com.apple.Preferences/iphone-storage.md`.

This file covers path 1. The cross-platform `uninstall_app(...)` helper in
`agent-workspace/agent_helpers.py` uses path 3 because it's the most reliable —
fall back to this one only when the app isn't reachable through Settings (e.g.
some VPN profile-shipped apps).

## Long-press uninstall

```python
from time import sleep

# 1. Make sure we're on the home screen, not inside an app.
appium("mobile: pressButton", name="home")
wait(0.8)
# A second tap of Home pulls us to page 1 if we're on a later page.
appium("mobile: pressButton", name="home")
wait(0.6)

LABEL = "Chess"  # display name as it appears on the home screen

icon = find(label=LABEL, type="XCUIElementTypeIcon")
if icon is None:
    # The icon might be on another page; swipe through pages until found.
    for _ in range(8):
        swipe(window_size()["width"] * 0.8, window_size()["height"] * 0.5,
              window_size()["width"] * 0.2, window_size()["height"] * 0.5,
              duration=0.25)
        wait(0.4)
        icon = find(label=LABEL, type="XCUIElementTypeIcon")
        if icon: break

if icon is None:
    raise RuntimeError(f"icon {LABEL!r} not on home screen — try App Library")

# 2. Long-press the icon. iOS uses a touch-and-hold ≥ 0.6s to open the
#    contextual menu; the W3C 'mobile: touchAndHold' is the cleanest call.
appium("mobile: touchAndHold", elementId=icon["id"], duration=0.9)
wait(0.7)

# 3. The menu shows: Edit Home Screen / Share App / Remove App / (etc).
remove = find(label="Remove App", type="XCUIElementTypeButton")
if remove is None:
    # On older iOS, the menu item is "Delete App" directly. Dismiss and retry.
    tap_at_xy(40, 40); wait(0.3)
    raise RuntimeError("Remove App menu item not present — verify iOS version")
tap(remove); wait(0.6)

# 4. The alert presents Remove from Home Screen / Delete App / Cancel.
#    "Delete App" is destructive (red) — that's the one we want.
delete = find(label="Delete App", type="XCUIElementTypeButton")
if delete is None:
    # Some app types short-circuit the alert (clips, App Clips). Treat as failure.
    raise RuntimeError("Delete App alert button not present — possibly an App Clip")
tap(delete); wait(0.6)

# 5. Final confirmation: "Delete <App>? Deleting this app will also delete its data."
confirm = find(label="Delete", type="XCUIElementTypeButton")
if confirm is None:
    raise RuntimeError("Delete confirmation not present")
tap(confirm); wait(1.0)
```

## Edge cases

- **Screen Time PIN block.** If a Screen Time restriction disables app
  removal, the long-press menu will not contain "Remove App" at all (greyed
  out / hidden). Detect by absence and surface a clean error — do not retry,
  do not attempt PIN entry.
- **Stock apps that cannot be deleted** (Settings, Phone, Safari on iOS 18
  this is per-app, not per-category). The "Remove App" menu item is hidden.
  Same detection as the PIN case.
- **Jiggle mode side-effect.** If the long-press is too long (>1.5s) iOS
  enters jiggle mode instead of opening the context menu. Use the X marker
  on the icon: `tap(find(label="Remove " + LABEL, type="XCUIElementTypeButton"))`
  → same alert. Exit jiggle mode with `appium("mobile: pressButton", name="home")`.
- **App in a folder.** Open the folder first (`tap(find(label=FOLDER_NAME,
  type="XCUIElementTypeIcon"))`) then long-press the icon inside.

## Verification

After the delete confirmation, the icon should vanish from the home screen:

```python
wait(1.5)
assert find(label=LABEL, type="XCUIElementTypeIcon") is None, \
    "icon still present — uninstall failed"
```

A second verification confirms the bundle is gone from iPhone Storage:

```python
from agent_helpers import list_installed_apps
assert all(app["label"] != LABEL for app in list_installed_apps())
```

## Bundle ids vs labels

The home screen exposes the *label*, not the bundle id. To map between them:

- `appium("mobile: activeAppInfo")` returns the foreground bundle id and
  display name.
- `appium("mobile: queryAppState", bundleId="...")` returns 0 if not
  installed, 4 if running in foreground.

Cache a `bundle_id -> label` map when you first launch the app you want to
delete; do not assume the label matches the bundle id.
