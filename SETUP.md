# SETUP — From Zero to Working

Full setup guide for both platforms. By the end, one of these will work:

```bash
iphone-harness -c 'print(active_app())'    # iOS
android-harness -c 'print(active_app())'   # Android
```

If you already know what you're doing, skip to the platform section below.

---

## What you're building

```
your script  ──►  CLI  ──►  daemon  ──►  Appium server  ──►  driver on device
                                          (port 4723)
```

For iOS, the driver is **WebDriverAgent (WDA)** — requires Apple signing.
For Android, the driver is **UIAutomator2** — auto-installed by Appium.

---

# Part A — iOS Setup

## A1. System tools (Mac side)

### Install Xcode (the full thing, not just CLT)

Open the App Store, search **Xcode**, click Get. ~10 GB. Once installed, open it and accept the license. Then:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
xcodebuild -version   # Xcode 16.x or 26.x
```

### Install Homebrew, libimobiledevice, Node.js

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install libimobiledevice ideviceinstaller node
```

### Install Appium + XCUITest driver

```bash
npm i -g appium
appium driver install xcuitest
```

> **Pin to stable:** The 11.x line has bugs on some setups. 10.43.1 is known-good:
> ```bash
> appium driver install --source=npm appium-xcuitest-driver@10.43.1
> ```

### Install uv

```bash
brew install uv
```

## A2. Plug in the iPhone

Use a **data cable** (not charge-only). Tap **Trust This Computer** on the phone.

```bash
idevice_id -l                    # should show your UDID
ideviceinfo | head -20           # ProductType, ProductVersion, DeviceName
```

Enable **Developer Mode**: Settings → Privacy & Security → Developer Mode → On.

## A3. Apple ID + Team ID + signing certificate

1. Open Xcode → Settings → Accounts → add your Apple ID.
2. Create a dummy project to trigger Personal Team creation.
3. Manage Certificates → + → Apple Development.
4. Find Team ID: Keychain Access → My Certificates → Apple Development cert → Get Info → Organizational Unit.

### Stop codesign popup spam

Keychain Access → login → My Certificates → expand Apple Development → double-click private key → Access Control → "Allow all applications" → Save.

## A4. Pre-build WebDriverAgent

This is the step everyone skips. Open the WDA Xcode project:

```bash
open ~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj
```

1. Select **WebDriverAgentRunner** target → Signing & Capabilities
2. Check "Automatically manage signing" → pick your Personal Team
3. Set Bundle Identifier to `com.<your-handle>.mobile-use.wda`
4. Set scheme to `WebDriverAgentRunner`, destination to your iPhone
5. Product → Test (`Cmd+U`)
6. On the phone: Settings → General → VPN & Device Management → Trust

## A5. Wire up

```bash
cd /path/to/mobile_use
cp .env.example .env
# Fill in: IPH_UDID, IPH_XCODE_ORG_ID, IPH_WDA_BUNDLE_ID
pip install -e .          # or: uv pip install -e .
appium --base-path /      # terminal 1
iphone-harness --doctor   # terminal 2
iphone-harness -c 'print(active_app())'
```

---

# Part B — Android Setup

Android setup is significantly simpler than iOS — no signing, no Xcode, no provisioning.
**Works on macOS and Linux.** iOS requires macOS+Xcode; Android does not.

## B1. System tools

### Linux setup

`mobile-use bootstrap --android-only` autodetects the distro via `/etc/os-release`
and runs the right command. If you'd rather install by hand:

```bash
# Ubuntu / Debian / Mint / Pop / Raspbian:
sudo apt install -y android-tools-adb nodejs npm

# Fedora / RHEL / Rocky / AlmaLinux:
sudo dnf install -y android-tools nodejs npm

# Arch / Manjaro / EndeavourOS:
sudo pacman -S --noconfirm android-tools nodejs npm

# openSUSE / SLES:
sudo zypper install -y android-tools nodejs npm

# Alpine:
sudo apk add android-tools nodejs npm

# Then (any Linux):
npm i -g appium
appium driver install uiautomator2
pip install -e .
```

`mobile-use --doctor` then verifies the chain. The doctor's remediation strings
are platform-aware: on Linux you'll see `sudo apt install …` / `sudo dnf install …`
instead of `brew install …`.

### Linux verify (Docker, no local install)

Inside the repo:

```bash
docker build -f Dockerfile.linux-test -t mobile-use-linux-test .
docker run --rm mobile-use-linux-test python3 -m pytest -q
```

This is the same image GitHub Actions runs on the Ubuntu matrix cell.

### macOS

### Install Android SDK Platform Tools

```bash
brew install android-platform-tools
```

This gives you `adb` (Android Debug Bridge) — the USB communication layer.

### Install Node.js (if not already)

```bash
brew install node
```

### Install Appium + UIAutomator2 driver

```bash
npm i -g appium
appium driver install uiautomator2
```

### Install uv

```bash
brew install uv
```

## B2. Enable USB Debugging on the Android device

1. Go to **Settings → About Phone**.
2. Tap **Build Number** 7 times. A toast says "You are now a developer!"
3. Go to **Settings → Developer Options** (now visible).
4. Enable **USB Debugging**.
5. (Optional) Enable **Stay Awake** — keeps screen on while charging. Useful for automation.

## B3. Connect the device

1. Plug the Android device into the Mac via USB cable.
2. On the phone, a prompt appears: **"Allow USB debugging?"** → tap **Allow** (check "Always allow" for this computer).
3. Verify connection:

```bash
adb devices
# Expected:
# List of devices attached
# XXXXXXXX    device
```

If the device shows as `unauthorized`, re-authorize: unplug, replug, tap Allow again.

Save the device serial (the `XXXXXXXX` part) — it goes in `ANH_UDID`.

## B4. Configure

```bash
cd /path/to/mobile_use
cp .env.example .env
```

Fill in the Android section:

```bash
ANH_UDID=XXXXXXXX   # from `adb devices`
```

That's it. No Team ID, no signing, no WDA bundle. Android is simpler.

## B5. Install and run

```bash
pip install -e .              # or: uv pip install -e .
appium --base-path /          # terminal 1
android-harness --doctor      # terminal 2
android-harness -c 'print(active_app())'
```

First call takes 30-60s while UIAutomator2 installs on the device. Subsequent calls are fast.

Expected output:

```
{'package': 'com.android.launcher3', 'activity': '.uioverrides.QuickstepLauncher'}
```

The exact package/activity depends on the launcher. If you see output — the whole stack works.

### Try a real action

```bash
android-harness -c '
unlock()
appium("mobile: startActivity", package="com.android.settings", activity=".Settings")
wait_for_app("com.android.settings")
print("foreground:", active_app()["package"])
print("first 5 visible elements:")
for el in ui_tree(visible_only=True)[:5]:
    print(" ", el["type"], "|", el["text"] or el["content_desc"])
'
```

---

# Part B+ — iOS from Windows / Linux

Xcode + Apple codesigning are macOS-only. So is the WebDriverAgent build.
The pragmatic answer is to keep one Mac in the loop and drive it remotely.
Both patterns are first-class supported.

## Pattern 1 — Remote daemon (recommended)

The Mac runs `iphone-harness` daemon bound to TCP. The Linux/Windows host
runs the CLI and talks to the remote daemon. Zero local daemon, zero
Xcode dependency on the client.

```bash
# On the Mac (one-time setup):
mobile-use bootstrap --ios-only
mobile-use ios build-wda
mobile-use init --ios-only            # writes .env with IPH_UDID etc.

# On the Mac (each run):
IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'

# On the Linux / Windows host (in another shell):
ssh -L 8763:127.0.0.1:8763 <mac-host>     # tunnel — keeps the RPC port loopback

# On the Linux / Windows host (driving iOS):
mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c 'print(active_app())'
```

The harness's `iphone-harness --doctor` skips Xcode + WDA-signing checks
when it sees `IPH_CONNECT=tcp://…` — those run on the Mac, not here.

**Security**: the RPC is unauthenticated. Always tunnel over SSH (above)
rather than binding the daemon on `0.0.0.0`.

## Pattern 2 — Remote Appium URL

The Mac runs only Appium + WDA. The Linux host runs the full
iphone-harness daemon locally, but its Appium calls hit the remote Mac.

```bash
# On the Mac:
appium --base-path /

# On Linux:
export IPH_APPIUM_URL=http://<mac>:4723
mobile-use --ios --doctor
mobile-use --ios -c 'print(active_app())'
```

In this mode the doctor's `Xcode` and `WebDriverAgent signed` checks are
marked `OK: (skipped — Xcode is macOS-only; drive iOS from Linux via
remote IPH_APPIUM_URL)`.

## Why no fully-local Linux iOS path?

Apple gates iOS automation behind Xcode-built and -signed `WebDriverAgent.app`.
Linux can install [libimobiledevice](https://libimobiledevice.org/) and pair
an iPhone, but it can't build/sign a runner from scratch — the toolchain is
Apple-only. A prebuilt `.ipa` would still need provisioning-profile renewal
from a Mac. The remote patterns above are simpler and don't drift.

---

# Part C — Troubleshooting

## Decision tree — most common failures

```
Something broke.
│
├── First: run `mobile-use --doctor` — shows the bad check + a one-line fix.
│
├── "daemon unreachable" / "stale session"
│   └── → `iphone-harness --reload` (or `android-harness --reload`).
│       Restart Appium too if the issue persists.
│
├── iOS: "xcodebuild failed with code 65"
│   └── → WDA signing. Run `mobile-use ios sign-wda`.
│       Free Apple account? Re-sign weekly (profile expires every 7 days).
│
├── iOS: "Tunnel registry port not found"
│   └── → xcuitest 11.x bug. Pin to 10.43.1:
│       `appium driver install --source=npm appium-xcuitest-driver@10.43.1`
│
├── Android: "device not found" / "unauthorized"
│   └── → unplug, replug, tap **Allow** on the phone.
│       Verify with `adb devices`.
│
├── "USB disconnect during script"
│   └── → Wrap your script with `@retry_on_disconnect(max_attempts=3)`.
│       Plug into a powered USB hub instead of a laptop port if it keeps happening.
│
├── "device locked" / "screen off"
│   └── → Call `wake_device()` at the top of your script.
│
├── Tests pass but real run fails
│   └── → Daemon is stuck. `rm /tmp/iph-*.sock /tmp/iph-*.pid` (iOS) or
│       `rm /tmp/anh-*.sock /tmp/anh-*.pid` (Android), then re-run.
│
└── Everything else
    └── → `mobile-use --doctor` then read SETUP.md for that step.
```

## Daemon logs (debugging)

When `iphone-harness -c '...'` or `android-harness -c '...'` fails after running for a while, check the daemon logs:

```bash
# iOS (one log per IPH_NAME, default "default"):
tail -50 /tmp/iph-default.log

# Android:
tail -50 /tmp/anh-default.log

# Live tail while running another shell:
tail -f /tmp/iph-default.log
```

The daemon writes `connecting to Appium…`, `session ok`, `stale session, reconnecting`, and any `fatal:` lines here. If the log is empty or missing, the daemon never started — run `mobile-use --doctor`.

## iOS

- **`xcodebuild failed with code 65`**: check Appium server log — usually untrusted cert, missing provisioning, or missing device support files.
- **`Tunnel registry port not found`**: xcuitest 11.x bug → downgrade to 10.43.1.
- **Codesign popup spam**: set private key Access Control to "Allow all applications" in Keychain Access.
- **WDA cert expired (weekly on free accounts)**: open WDA in Xcode → Cmd+U → re-trust on phone.

## Android

- **`adb devices` shows nothing**: USB debugging not enabled, or cable is charge-only.
- **`adb devices` shows `unauthorized`**: re-authorize on the phone (unplug, replug, tap Allow).
- **UIAutomator2 install fails**: ensure enough storage on device. Try `adb shell pm list packages | grep uiautomator` to check.
- **`Appium session create failed`**: check that Appium has the uiautomator2 driver: `appium driver list`.
- **App not found**: use `adb shell pm list packages | grep <name>` to find the exact package name.
- **Permission denied for shell commands**: some devices restrict `adb shell` commands. Root may be needed for advanced operations.

## Both platforms

- **Daemon won't come up**: `<harness> --doctor` shows the last 10 log lines.
- **Stale daemon**: `<harness> --reload` then call again.
- **After a hard kill**: `rm /tmp/iph-*.sock /tmp/iph-*.pid` (iOS) or `rm /tmp/anh-*.sock /tmp/anh-*.pid` (Android).

---

## What you end up with

- Appium on `:4723` serving both iOS and Android sessions.
- iOS daemon at `/tmp/iph-default.sock` (when using iOS).
- Android daemon at `/tmp/anh-default.sock` (when using Android).
- Both CLIs (`iphone-harness`, `android-harness`) with helpers pre-imported.

Daily workflow: plug in phone → `appium --base-path /` → `<harness> -c '...'`.
