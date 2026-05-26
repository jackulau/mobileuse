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

## Quickstart

Three commands, in order. Each one is idempotent — re-running is safe.

```bash
git clone https://github.com/jackulau/mobile_use.git && cd mobile_use
pip install -e .                  # installs the mobile-use / iphone-harness / android-harness CLIs
mobile-use bootstrap              # installs Appium + xcuitest + uiautomator2 + brew/node deps
mobile-use init                   # auto-detects connected device, writes .env (prompts for Apple Team ID on iOS)
mobile-use quickstart             # doctor + smoke test — prints "ready" or the first thing to fix
```

`mobile-use bootstrap` accepts `--dry-run` (preview only), `--ios-only`, `--android-only`.
`mobile-use init` accepts `--yes` (non-interactive — defaults for everything).
`mobile-use quickstart` auto-detects platform when one device is paired; pass `--ios` / `--android` to disambiguate.

If anything fails:

```bash
mobile-use --doctor               # numbered checks with one-line remediations
iphone-harness --reload           # nuke the daemon (rare but kills weird stale state)
mobile-use ios sign-wda           # iOS: re-sign WebDriverAgent (the #1 setup blocker)
mobile-use ios build-wda          # iOS: build the WDA test target (first-run setup)
mobile-use quickstart --autostart-appium   # spawn Appium server in background
```

See [`SETUP.md`](SETUP.md) for the manual / per-step appendix, including a
[troubleshooting decision tree](SETUP.md#part-c--troubleshooting).

### Linux

Android-on-Linux is a first-class target. `mobile-use bootstrap` auto-detects
your package manager (apt, dnf, pacman, zypper, apk) and installs `adb`, `node`,
and the Appium uiautomator2 driver natively — no Homebrew required.

```bash
# Linux host (any apt/dnf/pacman/zypper/apk distro):
pip install -e .
mobile-use bootstrap --android-only
mobile-use init --android-only
mobile-use quickstart --android
```

**iOS on Linux** requires a Mac somewhere in the loop (Xcode + Apple
codesigning are macOS-only by Apple). Two patterns:

- **Remote daemon (TCP)** — Linux runs zero daemon locally; talks to a
  remote `iphone-harness` daemon on a Mac via TCP:
  ```bash
  # On the Mac (one shot):
  IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'
  # On Linux (in another shell):
  ssh -L 8763:127.0.0.1:8763 <mac-host>
  mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c 'print(active_app())'
  ```
- **Remote Appium URL** — `IPH_APPIUM_URL=http://<mac>:4723` lets a local
  iphone-harness on Linux talk to a Mac running just Appium+WDA.

See [`SETUP.md` → "iOS from Windows / Linux"](SETUP.md#ios-from-windows--linux)
for the full walkthrough.

### Runtime helpers (no device pain)

```python
from iphone_harness.helpers import wake_device, retry_on_disconnect, record_screen

wake_device()                              # screen-off / locked? wake it.

@retry_on_disconnect(max_attempts=3)        # USB blip / WDA crash → auto-restart + retry
def run_script():
    tap(find(label="Compose"))
    type_text("hello")

record_screen(duration=10)                  # save mp4 to /tmp (XCUITest + UIAutomator2)

# record/replay a tap sequence (dumb — literal replay):
from mobile_use import record_replay
import iphone_harness.helpers as h
record_replay.start_recording("flow.py", helpers=h)
# ... your taps/swipes/typing ...
record_replay.stop_recording()              # writes runnable flow.py
record_replay.replay("flow.py")             # play it back

# smart macro — annotate intent + LLM re-targets when the UI shifts:
with record_replay.recording("compose.py", helpers=h):
    with record_replay.annotate("open compose screen"):
        h.tap(h.find(label="Compose"))
    with record_replay.annotate("type message body"):
        h.type_text("hello")
# replay_smart re-finds buttons via your LLM when labels / layout move
record_replay.replay_smart("compose.py", helpers=h, llm=my_llm_callable)
```

CLI equivalent — `mobile-use macro record <name>` opens a REPL with helpers + recording active; `mobile-use macro replay <name> --smart` adapts steps when the UI shifts. See [docs/macros.md](docs/macros.md) for the full walkthrough.

### Manual setup (skip if `mobile-use bootstrap` worked)

```bash
brew install libimobiledevice ideviceinstaller android-platform-tools node
npm i -g appium
appium driver install xcuitest          # iOS only
appium driver install uiautomator2      # Android only
pip install -e .
cp .env.example .env                    # fill in IPH_UDID / IPH_XCODE_ORG_ID / IPH_WDA_BUNDLE_ID and/or ANH_UDID
```

Plug in iPhone — Trust This Computer, Settings → Privacy & Security → Developer Mode → On,
trust the WDA developer profile.
Plug in Android — enable USB Debugging, tap Allow on this computer.

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

# Navigation (both platforms — Android native buttons, iOS gesture equivalents)
press_home()                             # both — go to home screen
press_back()                             # Android: back key; iOS: swipe-from-left edge
press_recents()                          # Android: recents; iOS: app switcher
swipe_back()                             # iOS: explicit edge-swipe (alias for press_back on iOS)
open_app_switcher()                      # iOS: swipe up + pause

# iOS-only
native_screenshot()                      # saves to iPhone Photos
set_assistive_touch(on=True)
open_control_center()
close_control_center()
ensure_cc_tile(label)
start_screen_recording()
stop_screen_recording()

# Android-only
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
