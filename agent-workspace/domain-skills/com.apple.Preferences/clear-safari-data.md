# Settings → Safari → Clear History and Website Data

Bundle id: `com.apple.Preferences`. Field-tested on iOS 18.3.

Clears browsing history, cookies, and cached site data for Safari. On iOS 18
the dialog also lets you scope the clear to a time range and to "All
Profiles" (otherwise it only affects the active profile).

```python
appium("mobile: terminateApp", bundleId="com.apple.Preferences")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.Preferences")
wait(1.8)

# Settings root → Apps → Safari (iOS 18 moved app-specific settings under
# the "Apps" submenu).
apps = find(label="Apps", type="XCUIElementTypeCell")
if apps is None:
    # Older iOS — Safari is at root level.
    safari_root = find(label="Safari", type="XCUIElementTypeCell")
else:
    tap(apps); wait(1.0)
    safari_root = find(label="Safari", type="XCUIElementTypeCell")
    if safari_root is None:
        # Scroll the apps list.
        for _ in range(20):
            scroll_by(dy=-400, velocity=400); wait(0.3)
            safari_root = find(label="Safari", type="XCUIElementTypeCell")
            if safari_root: break
if safari_root is None:
    raise RuntimeError("Safari row not found in Settings")
tap(safari_root); wait(1.5)

# Scroll down to "Clear History and Website Data" — it's near the bottom.
for _ in range(15):
    clear = find(label="Clear History and Website Data", type="XCUIElementTypeCell") or \
            find(label="Clear History and Website Data", type="XCUIElementTypeButton")
    if clear is not None:
        break
    scroll_by(dy=-400, velocity=400); wait(0.3)

if clear is None:
    raise RuntimeError("Clear History row not found")
tap(clear); wait(1.0)

# Sheet appears: time range picker + All Profiles toggle + Clear History button.
# Set time range = "All history" (default may be "Last hour").
range_btn = find(label="Last hour", type="XCUIElementTypeButton") or \
            find(label="Today", type="XCUIElementTypeButton") or \
            find(label="All history", type="XCUIElementTypeButton")
if range_btn and range_btn["label"] != "All history":
    tap(range_btn); wait(0.4)
    tap(find(label="All history", type="XCUIElementTypeButton")); wait(0.4)

clear_btn = find(label="Clear History", type="XCUIElementTypeButton")
if clear_btn is None:
    raise RuntimeError("Clear History confirm button not present")
tap(clear_btn); wait(2.0)

# Some accounts trigger a "Sign Out" follow-up; dismiss if it appears.
no = find(label="Cancel", type="XCUIElementTypeButton")
if no:
    tap(no); wait(0.3)
```

## Related

- **Advanced → Website Data** (same screen, scroll up): Remove All Website
  Data lets you nuke cookies without touching history.
- **Downloads (Files → On My iPhone → Downloads)** is Safari's download
  folder by default — see `domain-skills/com.apple.DocumentsApp/empty-downloads.md`.

## Verification

```python
# Re-enter the screen; the "Clear" row should be greyed out / "Clear History
# and Website Data" should be disabled while there is no data.
wait(1.0)
btn = find(label="Clear History and Website Data", type="XCUIElementTypeCell")
assert btn is None or btn.get("enabled") is False, "Clear button still active"
```
