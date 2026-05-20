# Mobile Use

Direct mobile device control via Appium. **iOS** (XCUITest) and **Android** (UIAutomator2).

A thin, editable harness for putting LLM agents on real phones. The agent perceives the device via UI tree + screenshots, acts via low-level taps and swipes, and writes its own per-app skills as it learns.

> Connect an LLM directly to a real phone with a thin, editable harness. The agent perceives the screen, reasons about what to do, and acts — no app-specific APIs needed.

```
  agent: wants to send a text
  │
  ui_tree() → finds compose field, send button
  │
  tap(field) → type_text(...) → tap(send)
  │
  message sent — works on iPhone and Android
```

## Setup

See [`SETUP.md`](SETUP.md) for full setup. Quick start:

### iOS

```bash
brew install libimobiledevice
npm i -g appium && appium driver install xcuitest
pip install -e .
cp .env.example .env  # fill in IPH_UDID, IPH_XCODE_ORG_ID, IPH_WDA_BUNDLE_ID
```

Plug in iPhone, unlock, trust the computer, trust the WDA developer profile.

### Android

```bash
brew install android-platform-tools
npm i -g appium && appium driver install uiautomator2
pip install -e .
cp .env.example .env  # fill in ANH_UDID
```

Connect Android device, enable USB debugging, authorize the computer.

### PATH note

`pip install -e .` installs the CLI commands (`mobile-use`, `iphone-harness`, `android-harness`) into your Python scripts directory. If they're not on your PATH after install, either:

```bash
# Option 1: find and add the scripts directory to PATH
python3 -m site --user-base   # shows e.g. /Users/you/.local
export PATH="$(python3 -m site --user-base)/bin:$PATH"

# Option 2: run via Python directly
python3 -m mobile_use.cli --version
python3 -m iphone_harness.run --doctor
python3 -m android_harness.run --doctor
```

### Verify install

```bash
python3 -c "import mobile_use; print(mobile_use.__version__)"  # should print 0.1.0
iphone-harness --version   # or: python3 -m iphone_harness.run --version
android-harness --version  # or: python3 -m android_harness.run --version
```

## Usage

Three CLI entry points — platform-specific or unified:

```bash
# Start Appium (shared server for both platforms):
appium --base-path /

# Platform-specific:
iphone-harness --doctor
iphone-harness -c 'print(active_app())'
android-harness --doctor
android-harness -c 'print(active_app())'

# Unified CLI (auto-detects platform when one device connected):
mobile-use --doctor
mobile-use -c 'print(active_app())'
mobile-use --ios -c 'print(active_app())'
mobile-use --android -c 'print(active_app())'
```

### iOS — drive Messages

```bash
iphone-harness -c '
appium("mobile: launchApp", bundleId="com.apple.MobileSMS")
wait_for_app("com.apple.MobileSMS")
field = wait_for_element(name="messageBodyField", timeout=5.0)
tap(field)
type_text("hello from mobile-use")
tap(find(type="XCUIElementTypeButton", name="sendButton"))
'
```

### Android — drive Messages

```bash
android-harness -c '
appium("mobile: startActivity", package="com.google.android.apps.messaging", activity=".ui.ConversationListActivity")
wait_for_app("com.google.android.apps.messaging")
btn = wait_for_element(content_desc="Start chat", timeout=5.0)
tap(btn)
'
```

### Agent mode

Persistent interactive REPL with session continuity — state persists between runs:

```bash
mobile-use agent --ios              # iOS agent loop
mobile-use agent --android          # Android agent loop
mobile-use agent                    # auto-detect platform
mobile-use agent --session mytest   # named session
```

Inside the agent REPL, all helpers are pre-imported. Extra bindings: `agent`, `session`, `perceive()`, `act()`.

### Multi-device (DevicePool)

Drive multiple iOS and Android devices simultaneously:

```python
from mobile_use import DevicePool

pool = DevicePool()
pool.add_ios("iphone1", udid="00008030-XXX", xcode_org_id="ABC", wda_bundle_id="com.me.wda")
pool.add_android("pixel", udid="SERIAL123")

pool.ensure_all_ready()

# Drive all devices
for dev in pool.devices:
    print(dev.name, dev.active_app())

# Drive a specific device
pool["iphone1"].tap_at_xy(200, 400)
pool["pixel"].press_home()

# Parallel execution across all devices
results = pool.broadcast(lambda d: d.screenshot())

# Platform-filtered broadcast
pool.broadcast_ios(lambda d: d.active_app())
pool.broadcast_android(lambda d: d.press_home())
```

Each device gets its own named daemon instance (`IPH_NAME` / `ANH_NAME`) with separate sockets, so they don't collide.

## Skills

### iOS Interaction Skills

| File | What |
|---|---|
| [`alerts.md`](interaction-skills/alerts.md) | System vs. in-app alerts; accept/dismiss patterns |
| [`home-bar-tap-zone.md`](interaction-skills/home-bar-tap-zone.md) | Why taps in the bottom ~80px fail |
| [`native-screenshot.md`](interaction-skills/native-screenshot.md) | Saving images to Photos via AssistiveTouch |
| [`ocr-fallback.md`](interaction-skills/ocr-fallback.md) | Apple Vision OCR when accessibility tree fails |
| [`picker-wheels.md`](interaction-skills/picker-wheels.md) | Driving date/time/value picker wheels |
| [`scroll-into-tappable-zone.md`](interaction-skills/scroll-into-tappable-zone.md) | Auto-scroll out of home-bar zone |
| [`wait-for-animations.md`](interaction-skills/wait-for-animations.md) | Poll-for-element patterns |

### Android Interaction Skills

| File | What |
|---|---|
| [`navigation-bar.md`](android-interaction-skills/navigation-bar.md) | Back/Home/Recents — the Android nav bar zone |
| [`permissions.md`](android-interaction-skills/permissions.md) | Runtime permission dialogs and granting patterns |
| [`notifications.md`](android-interaction-skills/notifications.md) | Notification shade interaction |
| [`toasts.md`](android-interaction-skills/toasts.md) | Toast messages — transient, not in accessibility tree |
| [`webview.md`](android-interaction-skills/webview.md) | Switching between native and webview contexts |

### Domain Skills (per-app playbooks)

Domain skills live in `agent-workspace/domain-skills/<bundleId-or-package>/`. Set `IPH_DOMAIN_SKILLS=1` (iOS) or `ANH_DOMAIN_SKILLS=1` (Android) and call `domain_skills(id)` after launching an app.

| Platform | App | Skill |
|---|---|---|
| iOS | Amazon | [`buy-now.md`](agent-workspace/domain-skills/com.amazon.Amazon/buy-now.md) |
| iOS | Chess.com | [`play-a-bot.md`](agent-workspace/domain-skills/com.chess.iphone/play-a-bot.md) |
| iOS | Instagram | [`navigation.md`](agent-workspace/domain-skills/com.burbn.instagram/navigation.md), [`post-photo.md`](agent-workspace/domain-skills/com.burbn.instagram/post-photo.md) |
| iOS | LinkedIn | [`post.md`](agent-workspace/domain-skills/com.linkedin.LinkedIn/post.md) |
| iOS | Messages | [`send-text.md`](agent-workspace/domain-skills/com.apple.MobileSMS/send-text.md), [`tapback-reaction.md`](agent-workspace/domain-skills/com.apple.MobileSMS/tapback-reaction.md) |
| iOS | Clock | [`create-alarm.md`](agent-workspace/domain-skills/com.apple.mobiletimer/create-alarm.md) |
| iOS | Settings | [`auto-lock.md`](agent-workspace/domain-skills/com.apple.Preferences/auto-lock.md) |
| iOS | X (Twitter) | [`post.md`](agent-workspace/domain-skills/com.atebits.Tweetie2/post.md) |

## Cleaning up and organizing the phone

Bundled skills + helpers for the most common "the phone is full / messy" tasks
on both platforms. Capability matrix and gap analysis:
[`docs/cleanup-capability.md`](docs/cleanup-capability.md).

### Shared helpers (auto-loaded into `iphone-harness -c` and `android-harness -c`)

| Helper | What |
|---|---|
| `list_installed_apps()` | iOS: scrapes Settings → iPhone Storage. Android: `pm list packages -3` with Settings fallback. |
| `uninstall_app(id_or_label)` | Dispatches to platform-specific uninstall. Returns `{ok, action, reason}`. |
| `storage_summary()` | Used / Free / Total. Display strings — parse if needed. |
| `bulk_select(items, deletion_button="Delete")` | Generic Select-mode → tap-each → Delete pattern. |
| `confirm_destructive(label="Delete", timeout=4.0)` | Waits for the confirmation alert and taps it. |

### Cleanup + organize domain skills

| Platform | App | Skill |
|---|---|---|
| iOS | SpringBoard | [`uninstall-app.md`](agent-workspace/domain-skills/com.apple.springboard/uninstall-app.md), [`organize-home-screen.md`](agent-workspace/domain-skills/com.apple.springboard/organize-home-screen.md), [`app-library.md`](agent-workspace/domain-skills/com.apple.springboard/app-library.md) |
| iOS | Settings | [`iphone-storage.md`](agent-workspace/domain-skills/com.apple.Preferences/iphone-storage.md), [`clear-safari-data.md`](agent-workspace/domain-skills/com.apple.Preferences/clear-safari-data.md), [`screen-time-limits.md`](agent-workspace/domain-skills/com.apple.Preferences/screen-time-limits.md) |
| iOS | Photos | [`bulk-delete-photos.md`](agent-workspace/domain-skills/com.apple.mobileslideshow/bulk-delete-photos.md), [`empty-recently-deleted.md`](agent-workspace/domain-skills/com.apple.mobileslideshow/empty-recently-deleted.md), [`delete-by-album.md`](agent-workspace/domain-skills/com.apple.mobileslideshow/delete-by-album.md) |
| iOS | Files | [`browse-and-delete.md`](agent-workspace/domain-skills/com.apple.DocumentsApp/browse-and-delete.md), [`empty-downloads.md`](agent-workspace/domain-skills/com.apple.DocumentsApp/empty-downloads.md), [`empty-files-recently-deleted.md`](agent-workspace/domain-skills/com.apple.DocumentsApp/empty-files-recently-deleted.md) |
| Android | Settings | [`uninstall-app.md`](agent-workspace/domain-skills/com.android.settings/uninstall-app.md), [`storage-cleanup.md`](agent-workspace/domain-skills/com.android.settings/storage-cleanup.md), [`clear-app-cache.md`](agent-workspace/domain-skills/com.android.settings/clear-app-cache.md) |
| Android | Pixel Launcher | [`long-press-uninstall.md`](agent-workspace/domain-skills/com.google.android.apps.nexuslauncher/long-press-uninstall.md), [`organize-home-screen.md`](agent-workspace/domain-skills/com.google.android.apps.nexuslauncher/organize-home-screen.md), [`app-drawer.md`](agent-workspace/domain-skills/com.google.android.apps.nexuslauncher/app-drawer.md) |
| Android | Files by Google | [`cleanup.md`](agent-workspace/domain-skills/com.google.android.apps.nbu.files/cleanup.md) |
| Android | Google Photos | [`bulk-delete.md`](agent-workspace/domain-skills/com.google.android.apps.photos/bulk-delete.md), [`empty-bin.md`](agent-workspace/domain-skills/com.google.android.apps.photos/empty-bin.md) |

### Runnable demos

```bash
# iOS — inventory + folder organize + uninstall a test app + empty Photos bin
python3 docs/demos/clean-and-organize-ios.py

# Preview only (no destructive ops)
DRY_RUN=1 python3 docs/demos/clean-and-organize-ios.py

# Android equivalent — opt in to uninstall by setting TEST_PACKAGE
python3 docs/demos/clean-and-organize-android.py
TEST_PACKAGE=com.example.junkapp python3 docs/demos/clean-and-organize-android.py
```

### Tests

```bash
python3 -m pytest tests/test_cleanup_skills.py -x
```

No device required — tests read skill files and the helpers module from disk.
Out-of-scope (documented, not implemented): rooting/jailbreak, bypassing
Screen Time PIN, cloud-side deletes, OEM-launcher-specific recipes outside
Pixel/AOSP. See [`docs/cleanup-capability.md`](docs/cleanup-capability.md).

## Architecture

Two parallel harnesses sharing the same Appium server:

```
                         ┌──────────────────┐
  iphone-harness -c ──►  │  iphone_harness   │ ──► Appium ──► XCUITest/WDA ──► iPhone
                         │  daemon (iph-*)   │     :4723
                         └──────────────────┘
                         ┌──────────────────┐
  android-harness -c ──► │  android_harness  │ ──► Appium ──► UIAutomator2 ──► Android
                         │  daemon (anh-*)   │     :4723
                         └──────────────────┘
```

### iOS module (`iphone_harness/`)

- `run.py` — `iphone-harness` CLI
- `helpers.py` — public action API (tap, swipe, find, screenshot, ocr, ...)
- `daemon.py` — long-lived process owning the Appium/XCUITest session
- `admin.py` — daemon lifecycle + doctor
- `_ipc.py` — AF_UNIX JSON-line RPC

### Android module (`android_harness/`)

- `run.py` — `android-harness` CLI
- `helpers.py` — public action API (tap, swipe, find, screenshot, ocr, ...)
- `daemon.py` — long-lived process owning the Appium/UIAutomator2 session
- `admin.py` — daemon lifecycle + doctor
- `_ipc.py` — AF_UNIX JSON-line RPC

### Shared (`mobile_use/`)

- `cli.py` — unified `mobile-use` CLI with platform auto-detection
- `multibox.py` — multi-device support (`Device`, `DevicePool`)
- `agent_loop.py` — persistent agent loop (perceive → reason → act cycle)
- `session.py` — session continuity (state persists between agent runs)
- `skills.py` — auto skill authoring (writes `.md` files for discoveries)
- `agent-workspace/` — agent-editable helpers + domain skills
- `interaction-skills/` — iOS UI mechanics
- `android-interaction-skills/` — Android UI mechanics

## Public API (both platforms)

Both harnesses expose the same core API. Platform-specific extras noted.

```
# Perception
screenshot(path=None)                    → str path on host
window_size()                            → {'width', 'height'}
ui_tree(visible_only=False)              → list[dict]
find(...)                                → element or None
find_all(...)                            → list[element]
active_app()                             → dict
ocr(image_path=None)                     → (lines, (px_w, px_h))
find_text(query, ...)                    → line dict or None
annotated_screenshot(path=None)          → (annotated_path, items)
page_source()                            → raw XML

# Input
tap_at_xy(x, y)
tap(element)
tap_safe(element, refind=callable)
double_tap(x, y)
long_press(x, y, duration=1.0)
swipe(x1, y1, x2, y2, duration=0.4)
scroll(direction='down')
scroll_by(dy=-400)
type_text(text)
click(selector/predicate, ...)
send_keys(selector/predicate, keys, ...)
set_value(selector/predicate, value, ...)
paste_text(text, ...)

# Device
unlock()

# iOS-only
native_screenshot()                      # saves to iPhone Photos
set_assistive_touch(on=True)
open_control_center()
close_control_center()
ensure_cc_tile(label)
start_screen_recording()
stop_screen_recording()

# Android-only
press_back()
press_home()
press_recents()
open_notifications()
close_notifications()
grant_permission(package, permission)

# Waits
wait(seconds=1.0)
wait_for(predicate, timeout=10.0)
wait_for_element(...)
wait_for_app(bundle_id_or_package)

# Alerts
alert()
alert_accept()
alert_dismiss()

# Skill discovery
domain_skills(bundle_id_or_package)

# Escape hatch — anything the driver supports
appium('mobile: anything', **params)
```

### Key differences between platforms

| | iOS (`iphone-harness`) | Android (`android-harness`) |
|---|---|---|
| **Element IDs** | `label`, `name` (NSPredicate) | `text`, `resource_id`, `content_desc` |
| **Element types** | `XCUIElementTypeButton`, etc. | `android.widget.Button`, etc. |
| **App identifier** | `bundleId` | `package` + `activity` |
| **find() params** | `label=`, `name=`, `type=`, `value=` | `text=`, `resource_id=`, `type=`, `content_desc=` |
| **click() selector** | iOS NSPredicate string | UiSelector / XPath / accessibility_id / resource ID |
| **Danger zone** | Bottom ~80px (home bar gesture) | Bottom ~48dp (navigation bar) |
| **Setup pain** | Apple signing + WDA provisioning | USB debugging toggle |

## Contributing

PRs welcome — **fork the repo, use it for real tasks, push your improvements back.**

The most valuable contributions are **new skills**:

- **Domain skills** (`agent-workspace/domain-skills/<id>/*.md`) — per-app playbooks for apps on either platform
- **Interaction skills** (`interaction-skills/*.md` or `android-interaction-skills/*.md`) — reusable UI mechanics
- **Bug fixes** and **harness improvements**

### Skills are written by the harness, not by you

Don't write skills from memory. Use the harness for a real task, let the agent figure out the non-obvious parts, and PR the generated `.md` file. Hand-authored skills lie. Agent-generated skills reflect the actual UI tree.

### What NOT to put in skills

- **Pixel coordinates** — use accessibility predicates instead
- **Secrets or personal data** — the directory is public
- **Task narration** — capture the map, not the diary

---

Released under the MIT License. See [`LICENSE`](LICENSE).

Built by [@jackulau](https://github.com/jackulau).
