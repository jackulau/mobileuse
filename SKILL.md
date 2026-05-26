---
name: mobile-use
description: Direct mobile device control via Appium. Use when the user wants to automate, inspect, or interact with a real iPhone or Android device tethered via USB.
---

# mobile-use

Direct mobile device control via Appium. iOS via XCUITest, Android via UIAutomator2.

For task-specific edits, use `agent-workspace/agent_helpers.py`. For setup/connection issues, run `<harness> --doctor`.

Domain skills (per-app playbooks under `agent-workspace/domain-skills/<id>/`) are off by default. Set `IPH_DOMAIN_SKILLS=1` (iOS) or `ANH_DOMAIN_SKILLS=1` (Android) to enable. After launching an app, call `domain_skills(id)` to get matching `.md` filenames — read every one before inventing an approach.

## Usage

```bash
# iOS:
iphone-harness -c '
appium("mobile: launchApp", bundleId="com.apple.MobileSMS")
wait_for_app("com.apple.MobileSMS")
print(active_app())
'

# Android:
android-harness -c '
appium("mobile: startActivity", package="com.google.android.apps.messaging", activity=".ui.ConversationListActivity")
wait_for_app("com.google.android.apps.messaging")
print(active_app())
'
```

- Invoke as `iphone-harness` or `android-harness` — they're on $PATH. Helpers pre-imported. Daemon auto-starts.
- Each daemon owns one Appium session per namespace (IPH_NAME / ANH_NAME). Use distinct names for multiple devices.

## Tool call shape

```bash
iphone-harness -c '...'    # iOS
android-harness -c '...'   # Android
```

`run.py` calls `ensure_daemon()` before `exec` — never start/stop manually unless you want to.

## What actually works (both platforms)

- **Tree first, screenshots second.** Both platforms expose accessibility trees via `ui_tree()`. Use `find(...)` for action targeting. Use `screenshot()` to verify visual state.
- **Coordinate taps default.** `tap_at_xy(x, y)` works through alerts, modals. Pair with `find()`: `tap(find(text='Send'))`.
- **App lifecycle goes through `appium(...)`.** No dedicated wrappers:
  - iOS: `appium("mobile: launchApp", bundleId="com.example.app")`
  - Android: `appium("mobile: startActivity", package="com.example.app", activity=".MainActivity")`
- **Verification:** `screenshot()` after every meaningful action.
- **Raw escape:** `appium("mobile: anything", **params)` — anything the driver supports.

## iOS-specific

- **System alerts:** `alert()` / `alert_accept()` / `alert_dismiss()` for SpringBoard alerts. In-app modals → `find()`.
- **Home-bar zone (bottom ~80px):** taps eaten by home gesture. Use `tap_safe(el, refind=...)`.
- **Control Center:** `open_control_center()`, `ensure_cc_tile(label)`, `start_screen_recording()`.
- **Picker wheels:** `pick_wheel(predicate, target, direction=...)`.
- **Element locators:** `find(label=, name=, type=, value=)` — XCUITest element attributes.

## Android-specific

- **Navigation bar (bottom ~48dp):** taps there may trigger Back/Home/Recents.
- **System buttons:** `press_back()`, `press_home()`, `press_recents()`.
- **Notifications:** `open_notifications()`, `close_notifications()`.
- **Permissions:** `grant_permission(package, permission)`.
- **Element locators:** `find(text=, resource_id=, type=, content_desc=)` — UIAutomator2 attributes.
- **click() strategies:** `click(selector, by='uiautomator'|'xpath'|'accessibility_id'|'id')`.

## Interaction skills

If you struggle with a generic mechanic, look in the skills directories:

### iOS (`interaction-skills/`)
- `home-bar-tap-zone.md` — bottom ~80px eaten by home gesture
- `alerts.md` — system vs. in-app alerts
- `picker-wheels.md` — date/time/value pickers
- `scroll-into-tappable-zone.md` — auto-scroll above home-bar zone
- `ocr-fallback.md` — when accessibility tree fails
- `wait-for-animations.md` — let iOS settle before reading tree

### Android (`android-interaction-skills/`)
- `navigation-bar.md` — Back/Home/Recents button zone
- `permissions.md` — runtime permission dialogs
- `notifications.md` — notification shade interaction
- `toasts.md` — transient messages not in accessibility tree
- `webview.md` — switching native/webview contexts

## Design constraints (both platforms)

- Tree-first interaction; screenshots for verification only.
- Connect to a manually-started Appium server.
- `appium(...)` is the public escape hatch — prefer raw scripts over typed wrappers.
- `run.py` stays tiny. No argparse, no subcommands.
- Core helpers stay short. Task-specific helpers go in `agent-workspace/agent_helpers.py`.
- No retries framework, session manager, daemon supervisor, config system, or logging framework.

## Gotchas

### iOS
- **Home-bar tap zone (bottom ~80px):** taps eaten by system gesture. `tap_safe()` or `long_press()`.
- **Status bar (top ~50px):** taps can trigger system overlays.
- **Screen locked:** `tap_at_xy()` fails silently. Always `unlock()` first.
- **Long messages:** `type_text()` is slow/flaky on Unicode. Use `set_value()` for long text.
- **Picker wheels:** use `pick_wheel()`, never raw swipes.
- **FaceID/passcode prompts:** STOP. Surface to user.

### Android
- **Navigation bar (bottom ~48dp):** taps may trigger system navigation. Use `tap_safe()`.
- **Toast messages:** not in accessibility tree. Use `screenshot()` + OCR.
- **WebView contexts:** `appium("mobile: getContexts")` to switch between native/web.
- **Permission dialogs:** block the app. Use `grant_permission()` or `alert_accept()`.
- **Screen locked:** `unlock()` first. PIN/pattern → surface to user.

## Macros — record once, replay many

Use macros for repetitive flows the agent runs verbatim (smoke tests, demos,
deterministic setup). Two tiers:

- **Literal** — `record_replay.replay("flow.py", helpers=h)` re-executes the exact
  recorded calls. Fastest, cheapest, brittle to any UI change.
- **Smart** — `record_replay.replay_smart("flow.py", helpers=h, llm=client)` checks
  the UI fingerprint before each annotated block; if it diverged from record
  time, asks the LLM to re-target the action. Costs one LLM call per shifted
  step (zero when UI is unchanged).

Record with intent so smart replay knows what each segment was for:

```python
with record_replay.recording("compose.py", helpers=h):
    with record_replay.annotate("open compose screen"):
        h.tap(h.find(label="Compose"))
    with record_replay.annotate("send message body"):
        h.type_text("hi")
        h.tap(h.find(label="Send"))
```

CLI: `mobile-use macro record <name>` opens a REPL with helpers + recording
active; `mobile-use macro replay <name> --smart` runs intent-aware replay.

**When to prefer macros over the agent loop:** flow is known, deterministic, and
will be re-run many times. **Stick with the agent loop** when you're exploring,
the screen state is unpredictable, or you need branching logic per response.

## Domain skills (opt-in)

When enabled (`IPH_DOMAIN_SKILLS=1` or `ANH_DOMAIN_SKILLS=1`), call `domain_skills(id)` after launching:

```python
# iOS
appium("mobile: launchApp", bundleId="com.apple.MobileSMS")
wait_for_app("com.apple.MobileSMS")
for f in domain_skills("com.apple.MobileSMS"):
    print(f)

# Android
appium("mobile: startActivity", package="com.google.android.apps.messaging", activity=".ui.ConversationListActivity")
wait_for_app("com.google.android.apps.messaging")
for f in domain_skills("com.google.android.apps.messaging"):
    print(f)
```
