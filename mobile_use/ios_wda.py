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
