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

    ensure_daemon()
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    exec(args[1], ns)


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

    ensure_daemon()
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    exec(args[1], ns)


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

Quickstart (first run):
  mobile-use bootstrap                 install Appium + driver + system deps
  mobile-use init                      write .env from connected device
  mobile-use quickstart                doctor + smoke test (proves it works)

Usage:
  mobile-use --ios -c '<python>'       run iOS script (iphone-harness)
  mobile-use --android -c '<python>'   run Android script (android-harness)
  mobile-use -c '<python>'             auto-detect platform
  mobile-use --doctor                  diagnose all platforms
  mobile-use --ios --doctor            diagnose iOS only
  mobile-use --android --doctor        diagnose Android only
  mobile-use agent [--ios|--android]   persistent agent loop
  mobile-use export-training [FILE]   export training data to JSONL
  mobile-use training-stats           show training data summary
  mobile-use --version

Options:
  --name <NAME>   Named daemon instance for multiboxing (multiple devices).
                  Each name gets its own Appium session and Unix socket.

Auto-detection: when only one device type is connected, --ios/--android
is inferred. When both are connected, you must specify.

Multiboxing (Python API):
  from mobile_use import DevicePool
  pool = DevicePool()
  pool.add_ios("iphone1", udid="...")
  pool.add_android("pixel", udid="...")
  pool.ensure_all_ready()
  pool.broadcast(lambda d: d.screenshot())

Platform-specific CLIs still work:
  iphone-harness -c '...'
  android-harness -c '...'
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

    # Doctor with no platform → run both
    if remaining and remaining[0] == "--doctor" and platform is None:
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
