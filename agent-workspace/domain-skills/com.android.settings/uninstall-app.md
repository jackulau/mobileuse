# Android Settings — uninstall an app

Package: `com.android.settings`. Field-tested on Pixel + Android 14 / AOSP
launcher.

Two reliable paths on Android:

1. **Direct Appium command** — `appium("mobile: removeApp", appId="com.example")`.
   Fast, no UI, works for all third-party apps. Doesn't work for system apps.
2. **Settings → Apps → app → Uninstall** — works for everything Uninstall is
   permitted on; for system apps the button silently becomes Disable.

The cross-platform helper `agent_helpers.uninstall_app(...)` tries path 1
first and falls back to path 2.

## Path 1 — Appium removeApp

```python
package = "com.example.junkapp"
ok = appium("mobile: removeApp", appId=package)
if ok:
    print(f"removed {package}")
```

Failure modes:

- `Not allowed for system app` — call path 2 with the Disable branch.
- `No such package` — already uninstalled. Treat as success.
- `WAIT_FOR_USER_CONFIRMATION` (rare, OEM-specific Appium behavior) — a UI
  dialog appears, fall through to path 2 to handle the tap.

## Path 2 — UI flow

```python
# Open Settings -> Apps -> See all apps.
appium("mobile: startActivity",
       package="com.android.settings",
       activity="com.android.settings.applications.ManageApplications")
wait(2.0)

# Search for the app. The search bar lives in the action bar.
search_btn = find(content_desc="Search") or find(resource_id="android:id/search_button")
if search_btn:
    tap(search_btn); wait(0.4)
    type_text("Junk App")
    wait(0.6)
else:
    # Older Android lists with no search — scroll instead.
    pass

# Find the row by visible label.
target = None
for _ in range(50):
    invalidate_tree_cache()
    for el in ui_tree(visible_only=True):
        if el.get("resource_id", "").endswith("title") and el.get("text") == "Junk App":
            target = el; break
    if target: break
    scroll(direction="down"); wait(0.3)
if target is None:
    raise RuntimeError("app row not found")
tap(target); wait(1.5)

# App info page → Uninstall.
btn = find(text="Uninstall") or find(content_desc="Uninstall")
if btn is None:
    # System app — only Disable is available.
    dis = find(text="Disable") or find(content_desc="Disable")
    if dis is None:
        raise RuntimeError("neither Uninstall nor Disable available")
    raise RuntimeError("system app — Disable only")
tap(btn); wait(0.8)

# Confirmation alert: "Uninstall this app?" → OK / Cancel (label set varies).
for label in ("OK", "Uninstall", "Yes"):
    confirm = find(text=label) or find(content_desc=label)
    if confirm:
        tap(confirm); break
wait(2.0)
```

## Disable a system app

When `Uninstall` is unavailable, `Disable` is the next-best option (the app
becomes inert and disappears from the launcher).

```python
dis = find(text="Disable") or find(content_desc="Disable")
if dis:
    tap(dis); wait(0.6)
    # Confirm: "Disable built-in app?" → Disable app
    confirm = find(text="Disable app") or find(text="Disable")
    if confirm: tap(confirm); wait(1.0)
```

`Disable` is reversible (Settings → Apps → Disabled apps → Enable). It
doesn't free much storage (data and APK stay) but it removes the launcher
icon and stops background services.

## Edge cases

- **Device admin app.** If the app is registered as a device admin, the
  Uninstall button is greyed. Detect: `enabled == False`. Remove admin
  privileges first via Settings → Security → Device admin apps → toggle off
  → return to uninstall.
- **Work profile apps** are managed by the work-profile policy; they ignore
  the personal-profile Uninstall flow. Skill caller should detect via the
  work badge (Appium element `content_desc` ends with " work app").
- **Auto-rename / OEM skins.** Samsung One UI labels "Uninstall" as
  "Uninstall" too but in MIUI the equivalent is "Uninstall". For OEMs that
  rename it (rare), call `find_fuzzy("Uninstall")`.
