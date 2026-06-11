"""`mobile-use init` — interactive .env writer.

Auto-detects paired devices via `idevice_id -l` and `adb devices`. Reads the
existing .env (preserving any already-set values). Prompts only for unknown
required fields. With `--yes`, uses every default.

iOS required:
  IPH_UDID          (auto-fill if exactly one iPhone paired)
  IPH_XCODE_ORG_ID  (no default — prompt; required for WDA codesign)
  IPH_WDA_BUNDLE_ID (default `com.$USER.mobile-use.wda`)

Android required:
  ANH_UDID          (auto-fill if exactly one Android attached)
"""
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
ALT_ENV_PATH = REPO_ROOT / "agent-workspace" / ".env"


# ---- detection -------------------------------------------------------------

def _idevice_id():
    if shutil.which("idevice_id") is None:
        return []
    try:
        out = subprocess.check_output(["idevice_id", "-l"], timeout=3.0,
                                      stderr=subprocess.DEVNULL).decode().strip()
        return [u for u in out.splitlines() if u]
    except Exception:
        return []


def _ios_sim_udids():
    """Booted iOS Simulator UDIDs (macOS/Xcode only). XCUITest drives a sim by
    UDID like a real device, so `init` can auto-fill from a Simulator when no
    physical iPhone is attached. Returns [] off macOS / on any error."""
    if shutil.which("xcrun") is None:
        return []
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            timeout=5.0, stderr=subprocess.DEVNULL).decode()
        import json
        data = json.loads(out)
    except Exception:
        return []
    udids = []
    for devs in (data.get("devices") or {}).values():
        for d in devs:
            if d.get("state") == "Booted" and d.get("udid"):
                udids.append(d["udid"])
    return udids


def _adb_devices():
    if shutil.which("adb") is None:
        return []
    try:
        out = subprocess.check_output(["adb", "devices"], timeout=3.0,
                                      stderr=subprocess.DEVNULL).decode().strip()
        return [l.split("\t")[0] for l in out.splitlines()[1:] if "\tdevice" in l]
    except Exception:
        return []


def detect_devices():
    """Return {'ios': [udids...], 'android': [serials...]}.

    iOS includes booted Simulators (after physical devices) so `init` can
    auto-fill on a Mac with no spare iPhone.
    """
    ios = _idevice_id()
    for udid in _ios_sim_udids():
        if udid not in ios:
            ios.append(udid)
    return {"ios": ios, "android": _adb_devices()}


# ---- env file parsing ------------------------------------------------------

def parse_env(path):
    """Read a .env file into {key: value}. Missing file → {}."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def has_real_value(values, key):
    """True if `values[key]` is set and not a placeholder."""
    v = values.get(key, "").strip()
    if not v:
        return False
    if v.startswith("YOUR-") or v == "YOURTEAMID":
        return False
    return True


# ---- build the env block ---------------------------------------------------

def build_env(existing=None, devices=None, *, ios=True, android=True,
              yes=False, defaults=None):
    """Merge existing values + auto-detected devices + (optional) prompted
    values into the final {key: value} map. `defaults` is a {key: str} dict
    used when `yes=True` and no other value is available."""
    existing = dict(existing or {})
    devices = devices or detect_devices()
    defaults = defaults or {}
    out = dict(existing)

    def fill(key, value, *, only_if_blank=True):
        if only_if_blank and has_real_value(out, key):
            return
        if value is None or value == "":
            return
        out[key] = value

    if ios:
        # IPH_UDID — auto-fill iff exactly one iPhone is paired
        if not has_real_value(out, "IPH_UDID"):
            if len(devices["ios"]) == 1:
                fill("IPH_UDID", devices["ios"][0])
            elif yes:
                fill("IPH_UDID", defaults.get("IPH_UDID", ""))
            else:
                # Prompt
                if len(devices["ios"]) > 1:
                    print("Multiple iPhones paired:")
                    for i, u in enumerate(devices["ios"]):
                        print(f"  [{i}] {u}")
                    choice = _prompt(f"IPH_UDID (0..{len(devices['ios']) - 1} or paste a UDID)",
                                      default=devices["ios"][0])
                    if choice.isdigit() and int(choice) < len(devices["ios"]):
                        choice = devices["ios"][int(choice)]
                    fill("IPH_UDID", choice)
                else:
                    fill("IPH_UDID",
                         _prompt("IPH_UDID (run `idevice_id -l` to find)", default=""))

        if not has_real_value(out, "IPH_XCODE_ORG_ID"):
            if yes:
                fill("IPH_XCODE_ORG_ID", defaults.get("IPH_XCODE_ORG_ID", ""))
            else:
                if sys.stdin.isatty():
                    print()
                    print("IPH_XCODE_ORG_ID — your Apple Team ID (10 chars, e.g. ABCDE12345)")
                    print("  How to find it:")
                    print("    1. Open Keychain Access (Spotlight: Keychain)")
                    print("    2. Login keychain → My Certificates")
                    print("    3. Expand 'Apple Development: <your name>' → double-click cert")
                    print("    4. Get Info → 'Organizational Unit' field is the 10-char Team ID")
                    print("  Or in Xcode: Settings → Accounts → select Apple ID → Manage Certificates")
                    print("  Full walkthrough: SETUP.md Part A3")
                fill("IPH_XCODE_ORG_ID",
                     _prompt("IPH_XCODE_ORG_ID",
                              default=""))

        if not has_real_value(out, "IPH_WDA_BUNDLE_ID"):
            user = os.environ.get("USER", "you").replace(" ", "")
            default_bid = defaults.get("IPH_WDA_BUNDLE_ID", f"com.{user}.mobile-use.wda")
            fill("IPH_WDA_BUNDLE_ID",
                 default_bid if yes else _prompt("IPH_WDA_BUNDLE_ID", default=default_bid))

    if android:
        if not has_real_value(out, "ANH_UDID"):
            if len(devices["android"]) == 1:
                fill("ANH_UDID", devices["android"][0])
            elif yes:
                fill("ANH_UDID", defaults.get("ANH_UDID", ""))
            else:
                if len(devices["android"]) > 1:
                    print("Multiple Android devices attached:")
                    for i, s in enumerate(devices["android"]):
                        print(f"  [{i}] {s}")
                    choice = _prompt(f"ANH_UDID (0..{len(devices['android']) - 1} or paste a serial)",
                                      default=devices["android"][0])
                    if choice.isdigit() and int(choice) < len(devices["android"]):
                        choice = devices["android"][int(choice)]
                    fill("ANH_UDID", choice)
                else:
                    fill("ANH_UDID",
                         _prompt("ANH_UDID (run `adb devices` to find)", default=""))

    return out


def _prompt(label, *, default=""):
    """Stdin prompt with default. Skips if stdin is not a tty (returns default)."""
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


# ---- serialize -------------------------------------------------------------

# Keys `mobile-use init` owns. Everything else in an existing .env — wireless
# persistence (IPH_WDA_URL), caps overrides (IPH_CAPS/ANH_CAPS), MU_* knobs,
# hand-added keys — is NOT managed and must survive an init re-run verbatim.
REQUIRED_IOS_KEYS = ("IPH_UDID", "IPH_XCODE_ORG_ID", "IPH_WDA_BUNDLE_ID")
OPTIONAL_IOS_KEYS = ("IPH_PLATFORM_VERSION", "IPH_DEVICE_NAME", "IPH_APPIUM_URL",
                     "IPH_NAME", "IPH_DOMAIN_SKILLS", "IPH_NEW_COMMAND_TIMEOUT")
MANAGED_IOS_KEYS = REQUIRED_IOS_KEYS + OPTIONAL_IOS_KEYS
REQUIRED_ANDROID_KEYS = ("ANH_UDID",)
OPTIONAL_ANDROID_KEYS = ("ANH_PLATFORM_VERSION", "ANH_DEVICE_NAME", "ANH_APPIUM_URL",
                         "ANH_NAME", "ANH_DOMAIN_SKILLS", "ANH_NEW_COMMAND_TIMEOUT")
MANAGED_ANDROID_KEYS = REQUIRED_ANDROID_KEYS + OPTIONAL_ANDROID_KEYS
REQUIRED_KEYS = REQUIRED_IOS_KEYS + REQUIRED_ANDROID_KEYS


def env_target_path():
    """The .env both `mobile-use init` and the wireless --persist writers
    target. Repo root wins when both exist — that matches daemon load
    precedence (repo/.env is loaded first; setdefault means its values win)."""
    if DEFAULT_ENV_PATH.exists():
        return DEFAULT_ENV_PATH
    if ALT_ENV_PATH.exists():
        return ALT_ENV_PATH
    return DEFAULT_ENV_PATH


def render_env(values, *, ios=True, android=True):
    """Render the values into a tidy .env body, preserving section headers
    from .env.example. Used for FRESH files only — existing files go through
    merge_env_text so unmanaged keys survive."""
    out = ["# Generated / updated by `mobile-use init`", ""]
    if ios:
        out += [
            "# ============================================================================",
            "# iOS (iphone-harness)",
            "# ============================================================================",
        ]
        for k in REQUIRED_IOS_KEYS:
            out.append(f"{k}={values.get(k, '')}")
        for k in OPTIONAL_IOS_KEYS:
            if k in values and values[k]:
                out.append(f"{k}={values[k]}")
        out.append("")
    if android:
        out += [
            "# ============================================================================",
            "# Android (android-harness)",
            "# ============================================================================",
        ]
        for k in REQUIRED_ANDROID_KEYS:
            out.append(f"{k}={values.get(k, '')}")
        for k in OPTIONAL_ANDROID_KEYS:
            if k in values and values[k]:
                out.append(f"{k}={values[k]}")
        out.append("")
    return "\n".join(out)


def _has_key_line(text, key):
    return any(ln.lstrip().startswith(f"{key}=") for ln in text.splitlines())


def _upsert_line(text, key, value):
    """Replace the first `KEY=...` line in place; append if absent. Every
    other line — comments, blank lines, unknown keys — passes through verbatim."""
    lines = text.splitlines()
    out, replaced = [], False
    for ln in lines:
        if not replaced and ln.lstrip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def merge_env_text(text, values, *, ios=True, android=True):
    """Line-preserving merge into an existing .env: managed keys updated in
    place (or appended), everything else kept verbatim. An init re-run must
    never destroy wireless/persisted config it doesn't manage (IPH_WDA_URL,
    IPH_CAPS, MU_*, hand-added keys)."""
    managed = (MANAGED_IOS_KEYS if ios else ()) + (MANAGED_ANDROID_KEYS if android else ())
    for k in managed:
        v = values.get(k, "")
        if v:
            text = _upsert_line(text, k, v)
        elif k in REQUIRED_KEYS and not _has_key_line(text, k):
            text = _upsert_line(text, k, "")
    return text


def _merged_text(path, values, *, ios=True, android=True):
    if path.exists():
        return merge_env_text(path.read_text(encoding="utf-8"), values,
                              ios=ios, android=android)
    return render_env(values, ios=ios, android=android)


def write_env(path, values, *, ios=True, android=True):
    """Write the values to `path`. Existing file → line-preserving merge;
    missing file → full template render. Creates parent dir if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_merged_text(path, values, ios=ios, android=android),
                    encoding="utf-8")


# ---- CLI entry -------------------------------------------------------------

def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="mobile-use init",
                                description="Write .env from connected devices.")
    p.add_argument("--yes", action="store_true",
                   help="Non-interactive: use defaults for everything, leave unknowns blank.")
    p.add_argument("--print", action="store_true",
                   help="Print the resulting .env to stdout instead of writing.")
    p.add_argument("--ios-only", action="store_true")
    p.add_argument("--android-only", action="store_true")
    p.add_argument("--path", default=None,
                   help="Where to write .env (default: <repo>/.env).")
    args = p.parse_args(argv)

    if args.ios_only and args.android_only:
        print("--ios-only and --android-only are mutually exclusive", file=sys.stderr)
        return 2

    ios = not args.android_only
    android = not args.ios_only

    # Linux + --ios-only: print remote-Mac guidance up front. The user can
    # still proceed (the .env writer is platform-neutral), but they need to
    # know IPH_APPIUM_URL must point at a real macOS Appium server.
    from mobile_use._platform import is_linux
    if is_linux() and ios and not android:
        print("Heads up: iOS local setup needs macOS (Xcode + WebDriverAgent).")
        print("On Linux, drive iOS via a remote Mac:")
        print("  - Set IPH_APPIUM_URL=http://<your-mac>:4723 in the .env this script writes,")
        print("    OR use `--remote-daemon tcp://<mac>:8763` on the CLI.")
        print("  - See SETUP.md → 'iOS from Windows / Linux' for the full walkthrough.")
        print()

    target = Path(args.path).resolve() if args.path else env_target_path()
    existing = parse_env(target)
    devices = detect_devices()

    if not args.yes:
        print(f"mobile-use init  →  {target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target}")
        print(f"  iPhones paired:  {devices['ios'] or 'none'}")
        print(f"  Androids:        {devices['android'] or 'none'}")
        if existing:
            print(f"  Existing keys:   {sorted(existing.keys())}")
        print()

    values = build_env(existing=existing, devices=devices,
                       ios=ios, android=android, yes=args.yes)

    if args.print:
        print(_merged_text(target, values, ios=ios, android=android))
        return 0

    write_env(target, values, ios=ios, android=android)
    print(f"\nWrote {target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target}")
    if not has_real_value(values, "IPH_UDID") and not has_real_value(values, "ANH_UDID"):
        print("Warning: no device UDIDs set. Re-run after connecting a phone, or edit .env manually.")
        return 1
    print("\nNext: `mobile-use quickstart`   (verifies the whole chain)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
