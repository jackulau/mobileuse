# Screen Time blocks cleanup actions — detection and handling

Bundle id: `com.apple.Preferences`. Field-tested on iOS 18.3.

Screen Time + Content & Privacy Restrictions can silently break every flow
in this skill folder. The failure modes:

1. **Deleting apps disabled** — the "Delete App" affordance is hidden from
   the SpringBoard long-press menu and from Settings → iPhone Storage →
   app → (the button is absent, not greyed).
2. **Installing apps disabled** — App Store may not even open.
3. **Privacy → Photos / Files** disabled — the Photos / Files apps refuse
   to enter Select mode.
4. **Account changes disabled** — clearing Safari data may prompt for a PIN.

If any of these triggers, a PIN dialog will appear titled either
**"Screen Time Passcode"** or **"Restrictions"**. Do not attempt to enter
it. Surface the block instead.

## Detector

```python
def screen_time_blocked():
    """Return True if a Screen Time / Restrictions PIN modal is showing."""
    # System alerts are scoped to SpringBoard; both labels appear as alert
    # titles. Check via `alert()` (iOS helper) and via direct find().
    info = None
    try:
        info = alert()
    except Exception:
        pass
    if info and any(t in (info.get("label") or "") for t in (
        "Screen Time", "Restrictions", "Enter Passcode"
    )):
        return True
    title = find(label="Screen Time Passcode") or find(label="Enter Passcode")
    return title is not None
```

Call it whenever a destructive flow fails to produce its expected next
screen — that's the most common signature of a Screen Time block.

## Error handling pattern

```python
from agent_helpers import uninstall_app

result = uninstall_app("Instagram")
if not result["ok"] and screen_time_blocked():
    raise RuntimeError("Cleanup blocked by Screen Time / Restrictions PIN — "
                       "user must disable the restriction in Settings → "
                       "Screen Time → Content & Privacy Restrictions.")
```

## Where the restriction lives

- Settings → Screen Time → Content & Privacy Restrictions → iTunes & App
  Store Purchases → **Deleting Apps** → set to *Allow*.
- Settings → Screen Time → Content & Privacy Restrictions → Allow Changes
  to → various sub-categories.

`mobile_use` does NOT attempt to flip these toggles on the user's behalf
because doing so usually requires a passcode that only the device owner
knows.

## Verification on a clean device

The simplest pre-flight check before any cleanup demo:

```python
appium("mobile: launchApp", bundleId="com.apple.Preferences")
wait(1.5)
st = find(label="Screen Time", type="XCUIElementTypeCell")
if st:
    tap(st); wait(1.5)
    enabled = find(label="Screen Time", type="XCUIElementTypeStaticText") and \
              find(label="Turn Off Screen Time", type="XCUIElementTypeButton")
    if enabled:
        # Screen Time is on. Check if restrictions are configured.
        scroll_by(dy=-400); wait(0.3)
        cpr = find(label="Content & Privacy Restrictions", type="XCUIElementTypeCell")
        if cpr:
            tap(cpr); wait(1.0)
            on = find(label="On", type="XCUIElementTypeStaticText") or \
                 find(name="ContentAndPrivacyEnabledSwitch", value="1")
            if on:
                print("WARNING: Content & Privacy Restrictions are ON — cleanup may be blocked")
```
