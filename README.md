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

## Why mobile_use

The shortest path from "phone in hand" to "LLM agent driving it" — on macOS,
Linux, or Windows, over USB or Wi-Fi, one device or ten:

- **One-command install** that probes what's missing (`mobile-use bootstrap`)
  and a doctor that reads your actual config and tells the truth.
- **Wireless that remembers**: pair once (`android pair` survives reboots),
  `--persist` saves the device, `wifi reconnect` (or the session itself)
  re-establishes after host reboots and DHCP changes.
- **Multi-device without port juggling**: one shared Appium server, per-device
  driver ports auto-assigned collision-free, `DevicePool.from_remembered()`.
- **Agent-native**: built-in agent loop with multimodal grounding, a
  dependency-free MCP server (`mobile-use mcp`), curated action surface with a
  destructive-verb gate, and an interactive live viewer.

Honest feature matrix vs raw Appium, Maestro, mobile-mcp, DroidRun, AppAgent,
and scrcpy — including where they win: [docs/comparison.md](docs/comparison.md).

## Quickstart

Three commands, in order. Each one is idempotent — re-running is safe.

> **Install from git.** `pip install mobile-use` from PyPI is a DIFFERENT,
> unrelated project that happens to share the name — install from this repo.

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

### Windows

Android-on-Windows is a first-class target. `adb` and Appium are
cross-platform — install the Android platform-tools (`adb` on `PATH`) plus
Node + Appium, then:

```powershell
# Windows host (PowerShell):
pip install -e .
mobile-use bootstrap --android-only   # winget steps for adb + node, npm appium install
mobile-use quickstart --android
```

The daemon transport auto-selects **TCP loopback** on Windows (the AF_UNIX
sockets used on macOS/Linux are Unix-only). Each named device gets a
deterministic loopback port, so multi-device routing, `devices status/reload`,
and the viewer all work exactly as on macOS/Linux — no configuration needed.

**iOS on Windows** needs a Mac in the loop (Xcode + Apple codesigning are
macOS-only) — use the same remote-Mac bridge as Linux above
([`SETUP.md` → "iOS from Windows / Linux"](SETUP.md#ios-from-windows--linux)).

### Multi-device — drive several phones at once

```bash
mobile-use devices list             # auto-detect every connected iOS + Android
mobile-use devices status           # show which named daemons are running
mobile-use devices reload --all     # restart every named daemon
```

Python API mirrors the CLI — no manual UDID lookup, no port juggling:

```python
from mobile_use import DevicePool

pool = DevicePool.from_connected(
    xcode_org_id="ABCDE12345",        # iOS — set once for every iPhone in the pool
    wda_bundle_id="com.you.wda",
)
pool.ensure_all_ready()                # parallel daemon spawn, isolated Appium ports
pool.broadcast(lambda d: d.tap_at_xy(200, 400))
pool.broadcast(lambda d: d.screenshot())  # → {name: {"result": png_bytes}}
```

Each device gets its own daemon socket (`/tmp/iph-<name>.sock`,
`/tmp/anh-<name>.sock`) and its own auto-allocated Appium port in
4724-4799 so multiple iPhones / Pixels can run side by side without
collisions. Override with `appium_url=` if you need a specific port or a
remote Appium server.

End-to-end example: [`docs/demos/multi-device-broadcast.py`](docs/demos/multi-device-broadcast.py).

**Watch every screen at once:**

```bash
mobile-use devices view             # open all connected devices in a grid (browser)
mobile-use devices view --port 8765 --no-browser
mobile-use devices view --devices iphone-A,pixel-1   # cherry-pick
```

```
┌─ multi-device live view ────────────────── 3/3 streams live ─┐
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│ │ ios/iphone-A │ │ ios/iphone-B │ │ android/px-1 │           │
│ │  [screen]    │ │  [screen]    │ │  [screen]    │           │
│ │  4.0fps · #N │ │  4.0fps · #N │ │  4.0fps · #N │           │
│ └──────────────┘ └──────────────┘ └──────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

One HTTP server, one auto-allocated port, N MJPEG streams under `/stream/<name>`.
Loopback-only, read-only mirror. Single-device view still works via `--headed`.

Example: [`docs/demos/multi-device-viewer.py`](docs/demos/multi-device-viewer.py).

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

### Faster perception — local detection (skip the VLM)

The agent loop's hotspot is the VLM round-trip on every step. Three OFF-by-default
layers cut it down, each degrading cleanly to the next (`yolo → template → tree → VLM`):

```bash
# 1. Perception/action cache (ON by default): a repeated identical screen replays the
#    last action and skips the LLM. Disable with MU_PERCEPTION_CACHE=0.

# 2. Template matcher — grounds tree-less screens (games/canvas/web views) from
#    captured element crops. Needs the [detection] extra:
pip install 'mobile-use[detection]'
MU_LOCAL_DETECTOR=1 mobile-use agent --ios

# 3. Trained YOLO-nano detector — the primary local grounding path (one forward pass).
#    Distill a detector from the self-labeling dataset (every grounded tap records a
#    free training sample), then serve it:
pip install 'mobile-use[yolo]'
mobile-use train-detector --train --epochs 80          # -> runs/train/weights/best.pt
MU_YOLO_DETECTOR=1 MU_DETECTOR_WEIGHTS=runs/train/weights/best.pt mobile-use agent --ios

# Let a confident, task-named match TAP DIRECTLY and skip the VLM for that step (even
# when the tree exists). OFF by default — a wrong match is a real tap with no VLM gate:
MU_LOCAL_SHORTCIRCUIT=1 MU_YOLO_DETECTOR=1 MU_DETECTOR_WEIGHTS=best.pt mobile-use agent
```

Measure the win on real screenshots (modeled VLM latency, real local wall-clock):

```bash
mobile-use bench-perception                              # synthetic (modeled) baseline
mobile-use bench-perception --images ./shots --weights best.pt   # REAL measured
```

No device or labels yet? Generate a synthetic seed dataset to exercise the whole
`dataset → train → weights → ground` pipeline (`mobile_use.synthetic_ui.generate_seed_dataset`).
Confidence gate for both detectors: `MU_DETECTOR_MIN_CONF` (default `0.78`). See
[SETUP.md](SETUP.md) for the full env-var reference (and the `polars-lts-cpu` note for
training on older CPUs).

Training is **self-validating**: `train-detector --train` only reports `trained` after the
produced checkpoint actually loads and runs one inference (else `trained_unverified`), aborts
early on an empty dataset, and resolves the bare `yolov8n.pt` base model to the committed
repo-root copy so an **offline** run never triggers an implicit download.

### Steady-state speed — per-step overhead trimmed (goal/022)

Beyond perception, the loop's per-step side-effect costs were profiled and cut.
Deterministic counts (asserted in `tests/test_step_overhead.py`; wall-clock is
reported, never asserted):

| Per-step cost                        | Before | After | How |
|--------------------------------------|--------|-------|-----|
| Session JSON full-file writes        | 2      | 1     | unchanged `current_app` no longer rewrites the file |
| Screenshot PNG copies (same screen)  | 1/step | 1 total | content-addressed store — hash decides, copy only when new |
| Pre-act `auto_dismiss` device RPCs   | 1/act  | 0     | skipped when the fresh same-step snapshot showed no alert (`MU_PREACT_DISMISS`) |
| `get_available_actions` introspection| every LLM step | once per module | `_ACTIONS_MEMO` |
| YOLO checkpoint deserializes (startup)| 2     | 1     | verification load is kept |

Also: `idevice_id` + `adb` detect probes run **concurrently** (bare `mobile-use`
cold start worst case ~1.5s, was ~3s), `ensure_daemon` trusts a verified probe
for `IPH_ENSURE_TTL`/`ANH_ENSURE_TTL` seconds (default 10), gesture settle
sleeps scale via `IPH_GESTURE_SETTLE`/`ANH_GESTURE_SETTLE` (default 1.0 = stock;
0 for emulators/CI), and the collector's per-row UI-tree dump is compact+capped
(`MU_COLLECT_TREE=full` restores raw). Crops are now named per-sample — the old
source-basename naming silently overwrote every crop into one file.

Dev velocity: the suite is `pytest-xdist`-safe — `pip install 'mobile-use[dev]'`
then `pytest -n auto tests -q` (~30-40s on 8 workers vs ~2-3 min serial).

### Self-check (validate the harness itself)

```bash
mobile-use selfcheck            # dep-rung matrix + action surface + training smoke (device-free)
mobile-use selfcheck --train    # also run a bounded 1-epoch real YOLO train (needs [yolo])
```

`selfcheck` reports which local-grounding rungs are live (and *why not*), confirms the action
verbs are consistent across platforms, and runs the synthetic `dataset → build → ground` smoke —
exit 0 iff the core invariants hold. (For **device** connectivity use `mobile-use --doctor`.)
Every action the agent dispatches is also **argument-validated before it runs** (unknown kwarg /
missing required arg / non-numeric coordinate → a clean error, never a blind call into the daemon).

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

Each device gets its own named daemon instance (`IPH_NAME` / `ANH_NAME`) with
separate sockets. All pool devices share ONE Appium server (4723, or your
`IPH/ANH_APPIUM_URL`) — simultaneous sessions are isolated by auto-assigned
per-device driver ports (`appium:systemPort` / `appium:wdaLocalPort` /
`appium:mjpegServerPort`), deterministic per name and collision-free under
concurrent pool builds. Your own caps always win. Pass `appium_url=` per
device for a dedicated server (e.g. a remote Mac).

Build pools without typing UDIDs:

```python
pool = DevicePool.from_connected()          # every USB/Wi-Fi device discovered now
pool = DevicePool.from_remembered()         # every wireless device saved by --persist
pool.add_ios("wifi-iphone", udid="...", wda_url="http://iPhone.local:8100")  # cable-free member
```

## Headed mode — watch the device while it runs

By default mobile-use is **headless**: scripts run, the daemon talks to the
device, you see no UI. Add `--headed` to spin up a local MJPEG viewer in
your browser and watch the live device screen mirror while the script runs:

```bash
mobile-use --ios --headed -c 'tap_at_xy(100, 200); time.sleep(2)'
# → opens http://127.0.0.1:<random-port>/ in your default browser
# → live mirror at ~6 fps, JPEG quality 60 (knobs in mobile_use/viewer/server.py)
```

The viewer is **interactive**: click the screen to tap that point on the
device, type into the send box (or straight onto the page), and use the
home button — with a visible control on/off toggle. Set
`MOBILE_USE_VIEWER_READONLY=1` (or `--read-only` on `devices view`) for a
plain mirror. Use `--headless` (or omit the flag) to skip the viewer
entirely. Works on iOS and Android.

Quality knobs (via Python API, when running in agent mode):

```python
from mobile_use.viewer.server import ViewerServer
v = ViewerServer(platform="ios", fps=12, quality=80, max_dim=1200)
v.start(); print(v.url)
# ...
v.stop()
```

## iOS from Windows / Linux

Windows hosts can't build WebDriverAgent (no Xcode). Drive iOS via a Mac
on the network running the daemon over TCP:

```bash
# On the Mac (one time): full Part A in SETUP.md
# On the Mac (each session):
IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'

# On Windows / Linux:
ssh -L 8763:127.0.0.1:8763 user@mac.local           # SSH tunnel (recommended)
mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c 'print(active_app())'

# Add --headed to also see the live screen mirror in your local browser:
mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 --headed -c '...'
```

Full walkthrough + security caveat: SETUP.md → "iOS from Windows / Linux
(remote Mac bridge)".

## Supported versions

`mobile-use` tracks the device-OS and Appium-toolchain versions it's verified against.
The matrix lives in `mobile_use/versions.py` and is printed by `mobile-use --doctor`:

| Component | Supported | Notes |
|-----------|-----------|-------|
| iOS | 15 – 26 | iOS >= 17 needs the RemoteXPC tunnel (USB or Wi-Fi) |
| Android | 8 – 16 | UiAutomator2; Wi-Fi via `mobile-use android wifi <ip>` |
| Appium server | >= 2.0.0 | 3.x recommended |
| xcuitest-driver | >= 5.0.0 | >= 10.0.0 requires Appium 3 |
| uiautomator2-driver | >= 3.0.0 | Android driver |

A newer OS than the tested max usually works — `--doctor` flags it "untested-newer"
rather than blocking. The doctor compares your installed Appium + drivers to this matrix
and **warns (never blocks)** when something is out of range.

**iOS 17+ (incl. iOS 26):** Apple replaced lockdownd with RemoteXPC, so Appium reaches
WebDriverAgent only through a **tunnel** — Appium's bundled `appium-ios-remotexpc`, or
`sudo pymobiledevice3 remote tunneld`. This applies over USB *and* Wi-Fi; without it,
session create fails with `RSDRequired` / `InvalidServiceError`.

## Wireless (Wi-Fi) control

Drive a phone over Wi-Fi — no cable tethered during the run.

**iOS — attach to WebDriverAgent over Wi-Fi.** WDA must be installed + running (USB once),
and on iOS 17+ the RemoteXPC tunnel must be up. Then point Appium at the iPhone's Wi-Fi IP
(WDA's default port is 8100):

```bash
# .env (or export):
IPH_WDA_URL=http://192.168.1.50:8100
mobile-use --ios -c 'print(active_app())'
```

`mobile-use --doctor` preflights `IPH_WDA_URL` reachability before connecting. Under the
hood this sets Appium's `appium:webDriverAgentUrl`; an `IPH_CAPS` override still wins.

**Android — adb over Wi-Fi.** One command switches a USB-connected device to TCP, connects,
and prints the serial to use:

```bash
mobile-use android wifi 192.168.1.42 --persist      # adb tcpip + connect; saves ANH_UDID
# -> .env updated AND device remembered (store: ~/.mobile_use/wifi_devices.json)
mobile-use --android -c 'print(active_app())'
mobile-use android wifi 192.168.1.42 --disconnect   # drop the wireless link
```

**No cable, ever (Android 11+):** pair via Wireless debugging — pairing
survives device reboots, unlike plain `adb tcpip`:

```bash
mobile-use android pair 192.168.1.42:37123 123456   # ip:port + code from the pairing dialog
mobile-use android wifi 192.168.1.42 --persist
```

**Remembered devices auto-reestablish.** `--persist` (both platforms) writes
the remember-store; reconnect everything after a host reboot / network change
with one command — or let the session self-heal (the daemon ensure path
retries wifi devices automatically):

```bash
mobile-use devices remembered          # what's saved (+ last_seen)
mobile-use wifi reconnect              # android: adb connect; ios: mDNS re-resolve
```

`mobile-use devices list` shows a TRANSPORT column (usb / wifi) per device —
including Wi-Fi-only iPhones (`idevice_id -n` is merged into discovery).
Full walkthrough incl. the iOS tunnel: SETUP.md → "Wireless (Wi-Fi) control".

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
