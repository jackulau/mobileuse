"""WDA signing helper — `mobile-use ios sign-wda`.

WebDriverAgent signing is the #1 setup blocker for iOS. Apple requires every
developer to re-sign + re-trust WDA every week (free accounts) or year (paid).
This module:
  - detects WDA signing state (signed / not-signed / expired / unknown)
  - opens the WebDriverAgent Xcode project for manual signing
  - prints concrete step-by-step instructions
  - re-verifies after user reports done

No interaction with the device requires Appium running — checks are local
(filesystem + idevice* / xcrun) + provisioning profile parsing.
"""
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Where appium-xcuitest-driver installs WebDriverAgent's xcodeproj.
WDA_PROJECT_CANDIDATES = (
    "~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj",
    "/usr/local/lib/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj",
    "/opt/homebrew/lib/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj",
)

# Provisioning profiles live here on macOS.
PROVISIONING_DIR = Path("~/Library/MobileDevice/Provisioning Profiles").expanduser()

# Bundle ID prefix WDA uses by default. User-configurable via IPH_WDA_BUNDLE_ID.
DEFAULT_WDA_BUNDLE = "com.facebook.WebDriverAgentRunner.xctrunner"


def find_wda_project():
    """Return Path to the WebDriverAgent.xcodeproj, or None if not installed."""
    for p in WDA_PROJECT_CANDIDATES:
        path = Path(p).expanduser()
        if path.exists():
            return path
    return None


def _provisioning_profiles():
    """Yield (path, plist_dict) for every installed provisioning profile.

    .mobileprovision files are CMS-signed plists. `security cms -D -i <path>`
    extracts the inner plist as XML.
    """
    if not PROVISIONING_DIR.exists():
        return
    for p in sorted(PROVISIONING_DIR.glob("*.mobileprovision")):
        try:
            out = subprocess.check_output(
                ["security", "cms", "-D", "-i", str(p)],
                timeout=5.0, stderr=subprocess.DEVNULL,
            )
            yield p, plistlib.loads(out)
        except Exception:
            continue


def _matches_wda(profile, wda_bundle):
    """True if this provisioning profile is for the WDA bundle ID."""
    entitlements = profile.get("Entitlements", {}) or {}
    app_id = entitlements.get("application-identifier", "") or ""
    # app_id format: TEAMID.com.facebook.WebDriverAgentRunner.xctrunner
    return app_id.endswith(wda_bundle) or wda_bundle in app_id


def check_wda_signing(wda_bundle=None):
    """Return (state, details) where state ∈ {signed, expired, not_signed, unknown}.

    State semantics:
      signed     — valid provisioning profile present, not expired
      expired    — provisioning profile found but ExpirationDate ≤ now
      not_signed — no provisioning profile matches the WDA bundle ID
      unknown    — checks failed (security tool missing, permissions, etc.)
    """
    wda_bundle = wda_bundle or os.environ.get("IPH_WDA_BUNDLE_ID") or DEFAULT_WDA_BUNDLE

    if sys.platform != "darwin":
        return "unknown", "not on macOS — signing only valid on Mac"

    if not shutil.which("security"):
        return "unknown", "`security` tool missing (should be on macOS by default)"

    matching = []
    for path, profile in _provisioning_profiles():
        if not _matches_wda(profile, wda_bundle):
            continue
        expiry = profile.get("ExpirationDate")
        team_name = profile.get("TeamName") or "(unknown team)"
        matching.append((path, expiry, team_name))

    if not matching:
        return "not_signed", f"no provisioning profile for {wda_bundle}"

    # Pick the one with the latest expiry.
    matching.sort(key=lambda m: m[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    path, expiry, team_name = matching[0]

    if expiry is None:
        return "unknown", f"profile found ({path.name}) but no ExpirationDate"

    now = datetime.now(timezone.utc)
    if hasattr(expiry, "tzinfo") and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    if expiry <= now:
        days_ago = (now - expiry).days
        return "expired", f"profile expired {days_ago}d ago ({team_name})"

    days_left = (expiry - now).days
    return "signed", f"signed ({team_name}, expires in {days_left}d)"


def open_in_xcode(path):
    """Open the WDA project in Xcode (macOS-only). Returns True on success."""
    if sys.platform != "darwin":
        return False
    try:
        subprocess.check_call(["open", "-a", "Xcode", str(path)], timeout=10.0)
        return True
    except Exception:
        return False


SIGNING_STEPS = """\
WebDriverAgent signing — 6 steps:

  1. In Xcode (just opened): select the WebDriverAgent project in the file tree.

  2. Select the `WebDriverAgentRunner` target (in the targets list at the top).

  3. Open the "Signing & Capabilities" tab.

  4. Tick "Automatically manage signing".

  5. Pick your team in the Team dropdown.
     • Free Apple ID: re-do this weekly (profile expires every 7 days).
     • Paid Apple Developer Program: re-do this yearly.

  6. Build the target (Cmd+B). Xcode will provision + sign WDA.

After build succeeds:
  • On iPhone: Settings → General → VPN & Device Management
  • Tap your developer profile → "Trust ..."
  • Then re-run: mobile-use ios sign-wda --check

If build fails with a bundle-id collision:
  • Change the bundle ID under "Signing & Capabilities" (e.g. com.you.WDA.xctrunner)
  • Then export it: IPH_WDA_BUNDLE_ID=com.you.WDA.xctrunner
  • Add the same line to your .env file
"""


def check_wda_built():
    """Return (built, detail) — True if a WebDriverAgentRunner-Runner.app is in DerivedData.

    Xcode builds land in ~/Library/Developer/Xcode/DerivedData/WebDriverAgent-*/Build/Products/.
    We scan for the .app to know whether the user has built the test target at least once.
    """
    if sys.platform != "darwin":
        return False, "not on macOS"
    derived = Path("~/Library/Developer/Xcode/DerivedData").expanduser()
    if not derived.exists():
        return False, "no DerivedData dir (Xcode never run)"
    candidates = list(derived.glob("WebDriverAgent-*/Build/Products/*/WebDriverAgentRunner-Runner.app"))
    if not candidates:
        return False, "no WebDriverAgentRunner-Runner.app under DerivedData"
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    age_days = (datetime.now().timestamp() - latest.stat().st_mtime) / 86400
    return True, f"built at {latest.parent.name} ({age_days:.1f}d ago)"


def _team_id_arg():
    """Return DEVELOPMENT_TEAM=... if IPH_XCODE_ORG_ID is set, else empty list."""
    tid = os.environ.get("IPH_XCODE_ORG_ID", "").strip()
    return [f"DEVELOPMENT_TEAM={tid}"] if tid else []


def _bundle_id_arg():
    """Return PRODUCT_BUNDLE_IDENTIFIER=... if IPH_WDA_BUNDLE_ID is set."""
    bid = os.environ.get("IPH_WDA_BUNDLE_ID", "").strip()
    return [f"PRODUCT_BUNDLE_IDENTIFIER={bid}"] if bid else []


def build_wda(udid=None, timeout=600):
    """Build the WebDriverAgent test target. Returns (rc, output).

    Uses `xcodebuild build-for-testing` so we don't need a connected device —
    we just want the .app + .xctestrun to exist for Appium to install at runtime.
    If a UDID is supplied, target that specific device; otherwise build for any iOS device.
    """
    project = find_wda_project()
    if project is None:
        return 1, "WebDriverAgent project not found — run `appium driver install xcuitest` first."

    destination = f"id={udid}" if udid else "generic/platform=iOS"
    cmd = [
        "xcodebuild",
        "build-for-testing",
        "-project", str(project),
        "-scheme", "WebDriverAgentRunner",
        "-destination", destination,
        "-allowProvisioningUpdates",
        "CODE_SIGNING_ALLOWED=YES",
        *_team_id_arg(),
        *_bundle_id_arg(),
    ]
    try:
        proc = subprocess.run(
            cmd, timeout=timeout, capture_output=True, text=True,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"xcodebuild timed out after {timeout}s"
    except FileNotFoundError:
        return 1, "xcodebuild not found — install Xcode (not just CLT)"


def build_main(argv):
    """Entry: mobile-use ios build-wda [--check]

    Build the WebDriverAgent test target so Appium can install it on the device
    on first run. This is the manual step in SETUP.md Part A4 — automated.
    """
    if any(a in {"-h", "--help"} for a in argv):
        print(
            "mobile-use ios build-wda [--check] [--udid UDID]\n\n"
            "Build the WebDriverAgent test target. Required once before the first\n"
            "Appium session can install WDA on the iPhone. This automates SETUP.md\n"
            "Part A4 (the manual Xcode project open + Cmd+U).\n\n"
            "Prerequisites:\n"
            "  - Xcode installed (App Store, ~10 GB)\n"
            "  - WDA signed: mobile-use ios sign-wda  (or sign-wda --check)\n"
            "  - IPH_XCODE_ORG_ID set (your Apple Team ID; from .env)\n\n"
            "Options:\n"
            "  --check        Exit 0 if WDA is already built, 1 otherwise.\n"
            "  --udid UDID    Build targeted at a specific device UDID.\n"
        )
        return 0

    check_only = "--check" in argv
    udid = None
    for i, a in enumerate(argv):
        if a == "--udid" and i + 1 < len(argv):
            udid = argv[i + 1]

    built, detail = check_wda_built()
    print(f"WDA build: {'built' if built else 'not built'}  ({detail})")

    if check_only:
        return 0 if built else 1

    if built:
        print("Nothing to do — WDA is already built.")
        print("Force a rebuild: rm -rf ~/Library/Developer/Xcode/DerivedData/WebDriverAgent-*")
        return 0

    sign_state, sign_detail = check_wda_signing()
    team = os.environ.get("IPH_XCODE_ORG_ID", "").strip()
    if sign_state != "signed":
        # A missing profile is only a hard blocker when we can't auto-provision.
        # With a team ID set, `xcodebuild -allowProvisioningUpdates` creates the
        # profile itself, so attempt the build rather than refusing — otherwise
        # it's a chicken-and-egg: build-wda is the step that generates the profile.
        if not team:
            print(f"\nWDA is not signed ({sign_state}: {sign_detail}).")
            print("Run `mobile-use ios sign-wda` first, then re-run this command.")
            return 1
        print(f"\nWDA not signed ({sign_state}), but IPH_XCODE_ORG_ID={team} is set — "
              "building with automatic provisioning (-allowProvisioningUpdates)...")

    udid = udid or os.environ.get("IPH_UDID", "").strip() or None
    target = f"device {udid}" if udid else "generic iOS"
    print(f"\nBuilding WebDriverAgent for {target} (this can take several minutes)...")

    rc, output = build_wda(udid=udid)
    if rc == 0:
        print("Build succeeded.")
        built2, detail2 = check_wda_built()
        print(f"Verify: {detail2}")
        return 0

    print(f"\nBuild FAILED (rc={rc}). Last 40 lines of xcodebuild output:")
    for line in output.splitlines()[-40:]:
        print(f"  {line}")
    # xcodebuild reports "No Account for Team" / "valid credentials" when the
    # Apple ID session behind an installed cert has expired — the profile can't
    # be auto-created until the account is re-authenticated. Call that out first.
    hint = ""
    if "No Account for Team" in output or "valid credentials" in output:
        hint = (
            "  - Apple ID session expired → Xcode → Settings → Accounts →\n"
            "    re-sign in to the Apple ID owning this team (password + 2FA),\n"
            "    then re-run. `-allowProvisioningUpdates` needs a live account session.\n"
        )
    print(
        "\nCommon fixes:\n"
        + hint +
        "  - Bundle ID collision → change IPH_WDA_BUNDLE_ID (see SETUP.md A4)\n"
        "  - Provisioning failed → re-open Xcode, retry sign-wda\n"
        "  - Code-signing identity missing → Xcode → Settings → Accounts → add Apple ID\n"
    )
    return rc


INSTALL_WDA_HELP = """\
mobile-use ios install-wda <WebDriverAgent.ipa> [--udid UDID]

Install a PRE-SIGNED WebDriverAgent ipa onto the device via pymobiledevice3.
Works from Linux and Windows — no Mac needed at runtime. A Mac is needed ONCE
to build + sign the ipa (yours, a teammate's, or CI); after that this command
plus the Wi-Fi flow drives the iPhone from any host.

After install:
  1. iOS 17+: start the tunnel        mobile-use ios tunnel
  2. resolve + remember the WDA URL   mobile-use ios wifi <device-ip> --persist
  3. drive it                         mobile-use --ios -c 'print(active_app())'

Getting an ipa: build WebDriverAgent once in Xcode (`mobile-use ios
build-wda`), then archive WebDriverAgentRunner-Runner.app into an .ipa.
"""


def _pymobiledevice3_cmd():
    """argv prefix for pymobiledevice3, or None when not installed.

    Console script when on PATH, else the module form (some pip installs only
    register the package, not the script)."""
    exe = shutil.which("pymobiledevice3")
    if exe:
        return [exe]
    try:
        import importlib.util
        if importlib.util.find_spec("pymobiledevice3") is not None:
            return [sys.executable, "-m", "pymobiledevice3"]
    except Exception:
        pass
    return None


def install_wda_main(argv):
    """Entry: mobile-use ios install-wda <wda.ipa> [--udid UDID]"""
    if not argv or argv[0] in {"-h", "--help"}:
        print(INSTALL_WDA_HELP)
        return 0 if argv else 2

    ipa = None
    udid = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--udid" and i + 1 < len(argv):
            udid = argv[i + 1]; i += 1
        elif not a.startswith("-") and ipa is None:
            ipa = a
        i += 1

    if not ipa:
        print("usage: mobile-use ios install-wda <WebDriverAgent.ipa> [--udid UDID]",
              file=sys.stderr)
        return 2
    if not Path(ipa).exists():
        print(f"ipa not found: {ipa}", file=sys.stderr)
        return 2

    prefix = _pymobiledevice3_cmd()
    if prefix is None:
        print("pymobiledevice3 is not installed.\n"
              "  Fix: pip install pymobiledevice3", file=sys.stderr)
        return 1

    cmd = [*prefix, "apps", "install", ipa]
    if udid:
        cmd += ["--udid", udid]
    print(f"installing {ipa} via `{' '.join(cmd)}` ...")
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                      timeout=300).decode(errors="replace")
    except subprocess.CalledProcessError as e:
        print((e.output or b"").decode(errors="replace"))
        print("install failed. Checklist:\n"
              "  - device paired + unlocked (tap Trust if prompted)\n"
              "  - the ipa is signed for THIS device (UDID in the provisioning profile)\n"
              "  - iOS 17+: RemoteXPC tunnel running (`mobile-use ios tunnel`)",
              file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"failed to run pymobiledevice3: {e}", file=sys.stderr)
        return 1

    print(out.strip() or "installed.")
    print("\nNext steps:\n"
          "  1. iOS 17+: keep the RemoteXPC tunnel up:  mobile-use ios tunnel\n"
          "  2. resolve + remember the WDA URL:         mobile-use ios wifi <device-ip> --persist\n"
          "  3. drive it:                               mobile-use --ios -c 'print(active_app())'")
    return 0


def main(argv):
    """Entry: mobile-use ios sign-wda [--check]"""
    if any(a in {"-h", "--help"} for a in argv):
        print(
            "mobile-use ios sign-wda [--check]\n\n"
            "WebDriverAgent must be signed with your Apple Team ID before Appium\n"
            "can run on a physical iPhone. This command opens the WDA Xcode project\n"
            "and prints the 6-step signing flow. Free Apple accounts re-sign weekly.\n\n"
            "Options:\n"
            "  --check    Print signing state and exit 0 if signed, 1 otherwise.\n"
            "             Without --check, opens Xcode and walks through signing.\n"
        )
        return 0
    check_only = "--check" in argv

    state, details = check_wda_signing()
    print(f"WDA signing: {state}  ({details})")

    if check_only:
        # 0 = signed (ready); 1 = needs action.
        return 0 if state == "signed" else 1

    if state == "signed":
        print("Nothing to do — WDA is signed.")
        return 0

    project = find_wda_project()
    if project is None:
        print("\nWebDriverAgent project not found. Install with:")
        print("  appium driver install xcuitest")
        return 1

    print(f"\nOpening {project} in Xcode...")
    if not open_in_xcode(project):
        print(f"  Failed to open. Run manually: open -a Xcode \"{project}\"")
        return 1

    print()
    print(SIGNING_STEPS)
    return 0
