"""Unified mobile-use CLI.

  mobile-use --ios -c '<python>'       run iOS script
  mobile-use --android -c '<python>'   run Android script
  mobile-use -c '<python>'             auto-detect platform (single device)
  mobile-use --doctor                  diagnose both platforms
  mobile-use agent                     start persistent agent loop
  mobile-use --version
"""
import os
import subprocess
import sys


def _detect_platform():
    """Auto-detect which platform to use based on connected devices.

    Returns 'ios', 'android', or None if ambiguous/none found.
    """
    ios_connected = False
    android_connected = False

    if os.environ.get("IPH_UDID"):
        ios_connected = True
    else:
        try:
            out = subprocess.check_output(
                ["idevice_id", "-l"], timeout=1.5, stderr=subprocess.DEVNULL
            ).decode().strip()
            if out:
                ios_connected = True
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass

    if os.environ.get("ANH_UDID"):
        android_connected = True
    else:
        try:
            out = subprocess.check_output(
                ["adb", "devices"], timeout=1.5, stderr=subprocess.DEVNULL
            ).decode().strip()
            lines = [l for l in out.splitlines()[1:] if "\tdevice" in l]
            if lines:
                android_connected = True
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass

    if ios_connected and not android_connected:
        return "ios"
    if android_connected and not ios_connected:
        return "android"
    if ios_connected and android_connected:
        return None  # ambiguous — user must specify
    return None  # nothing connected


def _check_env_for_platform(platform):
    """Pre-flight: warn if .env hasn't been initialized for the chosen platform.

    Returns None if OK, otherwise an actionable error message ready to surface.
    """
    key = "IPH_UDID" if platform == "ios" else "ANH_UDID"
    if os.environ.get(key, "").strip():
        return None
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(repo_root, ".env")
    alt_path = os.path.join(repo_root, "agent-workspace", ".env")
    if not os.path.exists(env_path) and not os.path.exists(alt_path):
        return (
            f"No .env file found and {key} not set in environment.\n"
            f"   Fix: run `mobile-use init` (auto-fills from connected device).\n"
            f"   Or set {key}=<udid> manually before running."
        )
    return None


def _run_ios(args):
    """Delegate to iphone-harness."""
    from iphone_harness.admin import ensure_daemon, restart_daemon, run_doctor
    import iphone_harness.helpers as _helpers

    if not args or args[0] in {"-h", "--help"}:
        print("mobile-use (iOS mode) — run `mobile-use --help` for full usage")
        return
    if args[0] == "--doctor":
        sys.exit(run_doctor())
    if args[0] == "--reload":
        restart_daemon()
        print("iOS daemon stopped — will respawn on next call")
        return
    if args[0] != "-c" or len(args) < 2:
        sys.exit('Usage: mobile-use --ios -c "print(active_app())"')

    env_err = _check_env_for_platform("ios")
    if env_err:
        sys.exit(env_err)

    try:
        ensure_daemon()
    except RuntimeError as e:
        sys.exit(f"{e}")
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    try:
        exec(args[1], ns)
    except SystemExit:
        raise
    except SyntaxError as e:
        sys.exit(f"Syntax error in your -c script: {e.msg} (line {e.lineno})")
    except Exception as e:
        # Show the user's script error without the cli.py traceback noise.
        import traceback
        tb = traceback.format_exc()
        # Strip the outer cli.py frame so the user sees their script's stack only.
        sys.stderr.write(tb)
        sys.exit(1)


def _run_android(args):
    """Delegate to android-harness."""
    from android_harness.admin import ensure_daemon, restart_daemon, run_doctor
    import android_harness.helpers as _helpers

    if not args or args[0] in {"-h", "--help"}:
        print("mobile-use (Android mode) — run `mobile-use --help` for full usage")
        return
    if args[0] == "--doctor":
        sys.exit(run_doctor())
    if args[0] == "--reload":
        restart_daemon()
        print("Android daemon stopped — will respawn on next call")
        return
    if args[0] != "-c" or len(args) < 2:
        sys.exit('Usage: mobile-use --android -c "print(active_app())"')

    env_err = _check_env_for_platform("android")
    if env_err:
        sys.exit(env_err)

    try:
        ensure_daemon()
    except RuntimeError as e:
        sys.exit(f"{e}")
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    try:
        exec(args[1], ns)
    except SystemExit:
        raise
    except SyntaxError as e:
        sys.exit(f"Syntax error in your -c script: {e.msg} (line {e.lineno})")
    except Exception:
        import traceback
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


def _doctor_both():
    """Run doctor on both platforms."""
    print("=" * 50)
    print("iOS (iphone-harness)")
    print("=" * 50)
    try:
        from iphone_harness.admin import run_doctor as ios_doctor
        ios_rc = ios_doctor()
    except ImportError:
        print("  iphone_harness not available")
        ios_rc = 1

    print()
    print("=" * 50)
    print("Android (android-harness)")
    print("=" * 50)
    try:
        from android_harness.admin import run_doctor as android_doctor
        android_rc = android_doctor()
    except ImportError:
        print("  android_harness not available")
        android_rc = 1

    return max(ios_rc, android_rc)


HELP = """mobile-use — direct mobile device control via Appium

QUICKSTART (first run, three commands):
  mobile-use bootstrap [--dry-run] [--ios-only] [--android-only]
                                       install Appium + driver + system deps
  mobile-use init [--yes]              write .env from connected device
  mobile-use quickstart [--ios|--android]
                                       doctor + smoke test (proves it works)

RUN SCRIPTS:
  mobile-use -c '<python>'             auto-detect platform (single device)
  mobile-use --ios -c '<python>'       force iOS
  mobile-use --android -c '<python>'   force Android
  mobile-use agent [--ios|--android]   persistent agent loop

DIAGNOSE:
  mobile-use --doctor                  diagnose all platforms
  mobile-use --ios --doctor            iOS only
  mobile-use --android --doctor        Android only

SETUP & MAINTENANCE:
  mobile-use ios sign-wda [--check]    re-sign WebDriverAgent (iOS #1 blocker)
  mobile-use ios build-wda [--check]   build WebDriverAgent test target (iOS first-run)
  mobile-use --reload                  nuke daemon (kills stale state)
  mobile-use --ios --reload            iOS daemon only
  mobile-use --android --reload        Android daemon only

DATA:
  mobile-use export-training [FILE]    export training data to JSONL
  mobile-use training-stats            show training data summary

OPTIONS:
  --name <NAME>   Named daemon for multiboxing (per-device socket + session).
  --version       Show version

AUTO-DETECT: when exactly one device type is connected, --ios/--android
is inferred. When both connected, you must specify.

MULTIBOXING (Python API):
  from mobile_use import DevicePool
  pool = DevicePool()
  pool.add_ios("iphone1", udid="...")
  pool.add_android("pixel", udid="...")
  pool.ensure_all_ready()
  pool.broadcast(lambda d: d.screenshot())

PLATFORM-SPECIFIC CLIs (also installed):
  iphone-harness -c '...' | --doctor | --reload
  android-harness -c '...' | --doctor | --reload

DOCS:
  README.md         quickstart + runtime helpers
  SETUP.md          full per-step setup + troubleshooting tree
"""


def main():
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(HELP)
        return

    if args[0] == "--version":
        from mobile_use import __version__
        print(f"mobile-use {__version__}")
        return

    # Extract platform flag and --name
    platform = None
    instance_name = None
    remaining = []
    i = 0
    while i < len(args):
        if args[i] == "--ios":
            platform = "ios"
        elif args[i] == "--android":
            platform = "android"
        elif args[i] == "--name" and i + 1 < len(args):
            instance_name = args[i + 1]
            i += 1
        else:
            remaining.append(args[i])
        i += 1

    if instance_name:
        if platform == "ios" or platform is None:
            os.environ["IPH_NAME"] = instance_name
        if platform == "android" or platform is None:
            os.environ["ANH_NAME"] = instance_name

    # Doctor with no platform → run both. Accept both `--doctor` (flag) and `doctor` (subcommand).
    if remaining and remaining[0] in {"--doctor", "doctor"} and platform is None:
        sys.exit(_doctor_both())

    # New UX subcommands (no platform required)
    if remaining and remaining[0] == "bootstrap":
        from . import bootstrap
        sys.exit(bootstrap.main(remaining[1:]))

    if remaining and remaining[0] == "init":
        from . import setup_env
        sys.exit(setup_env.main(remaining[1:]))

    if remaining and remaining[0] == "quickstart":
        from . import quickstart
        sys.exit(quickstart.main(remaining[1:], platform=platform))

    # ios <action> — iOS-specific subcommands.
    if remaining and remaining[0] == "ios":
        if len(remaining) < 2 or remaining[1] in {"-h", "--help"}:
            print(
                "mobile-use ios — iOS-specific subcommands:\n\n"
                "  sign-wda [--check]    Sign WebDriverAgent in Xcode (the #1 setup blocker).\n"
                "                        --check exits 0 if already signed, 1 otherwise.\n"
                "  build-wda [--check]   Build the WebDriverAgent test target (first-run setup).\n"
                "                        --check exits 0 if already built, 1 otherwise.\n"
            )
            sys.exit(0 if len(remaining) >= 2 else 2)
        if remaining[1] == "sign-wda":
            from . import ios_wda
            sys.exit(ios_wda.main(remaining[2:]))
        if remaining[1] == "build-wda":
            from . import ios_wda
            sys.exit(ios_wda.build_main(remaining[2:]))
        sys.exit(f"Unknown `mobile-use ios` action: {remaining[1]!r}. Try `mobile-use ios --help`.")

    # Training data commands
    if remaining and remaining[0] == "export-training":
        from .collector import export_training_data
        out = remaining[1] if len(remaining) > 1 else "training-export.jsonl"
        count = export_training_data(out)
        print(f"Exported {count} events → {out}")
        return

    if remaining and remaining[0] == "training-stats":
        from .collector import training_stats
        stats = training_stats()
        print(f"Sessions: {stats['sessions']}")
        print(f"Events:   {stats['events']}")
        if stats.get("apps"):
            print(f"Apps:     {', '.join(stats['apps'])}")
        return

    # Agent mode
    if remaining and remaining[0] == "agent":
        try:
            from mobile_use.agent_loop import run_agent
            run_agent(platform=platform or _detect_platform(), args=remaining[1:])
        except ImportError:
            print("Agent loop not yet implemented. Use -c for one-shot scripts.")
            sys.exit(1)
        return

    # Auto-detect platform if not specified
    if platform is None:
        platform = _detect_platform()
        if platform is None:
            if remaining and remaining[0] == "--doctor":
                sys.exit(_doctor_both())
            sys.exit(
                "Cannot auto-detect platform. Either:\n"
                "  - Connect exactly one device type, or\n"
                "  - Specify --ios or --android explicitly\n"
                "\nRun `mobile-use --doctor` to check what's connected."
            )

    if platform == "ios":
        _run_ios(remaining)
    elif platform == "android":
        _run_android(remaining)


if __name__ == "__main__":
    main()
