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


def _write_user_traceback():
    """Print traceback of the current exception with cli.py frames stripped.

    The user's -c script runs inside exec() inside _run_ios/_run_android.
    Those wrappers leak `File ".../cli.py", line N, in _run_ios` frames
    above the user's actual error. Walk down tb_next until we leave cli.py,
    then format only the remaining (user-visible) frames + exception.
    """
    import traceback
    exc_type, exc, tb = sys.exc_info()
    while tb is not None and tb.tb_frame.f_code.co_filename.endswith("cli.py"):
        tb = tb.tb_next
    sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))


def _probe_ios_connected():
    """Any physical iPhone on USB or Wi-Fi (idevice_id -l / -n, 1.5s each) —
    single-sourced from devices.py so auto-detect sees cable-unplugged phones."""
    try:
        from mobile_use.devices import ios_udids_with_transport
        return bool(ios_udids_with_transport(timeout=1.5))
    except Exception:
        return False


def _probe_android_connected():
    """One adb probe: any device in 'adb devices' output? (1.5s timeout)."""
    try:
        out = subprocess.check_output(
            ["adb", "devices"], timeout=1.5, stderr=subprocess.DEVNULL
        ).decode().strip()
        return any("\tdevice" in line for line in out.splitlines()[1:])
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def _detect_platform():
    """Auto-detect which platform to use based on connected devices.

    Returns 'ios', 'android', or None if ambiguous/none found.

    The two subprocess probes (idevice_id, adb — 1.5s timeout each) run
    CONCURRENTLY, so the no-device worst case is ~1.5s, not ~3s of serial
    waiting before every bare `mobile-use` invocation. An explicit *_UDID env
    skips that platform's probe entirely.
    """
    need_ios = not os.environ.get("IPH_UDID")
    need_android = not os.environ.get("ANH_UDID")

    ios_connected = not need_ios          # env override = known connected
    android_connected = not need_android

    probes = {}
    if need_ios:
        probes["ios"] = _probe_ios_connected
    if need_android:
        probes["android"] = _probe_android_connected
    if probes:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(probes)) as pool:
            results = {k: pool.submit(fn) for k, fn in probes.items()}
        if need_ios:
            ios_connected = results["ios"].result()
        if need_android:
            android_connected = results["android"].result()

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
    # A .env in the current working directory is honored (project-local config).
    if os.path.exists(os.path.join(os.getcwd(), ".env")):
        return None
    # Also accept a properly-filled .env at the repo root / agent-workspace —
    # that's where `mobile-use init` writes it and where the daemon loads it from,
    # so `-c` / `--reload` work from any cwd once init has run. _check_env_file
    # validates the REQUIRED keys are actually filled (not placeholders/blank), so
    # the "foreign cwd with no real config" error path stays intact.
    # Set MOBILE_USE_NO_REPO_ENV=1 to demand strict cwd/env config (CI/sandboxes
    # that must not pick up a developer's install-location .env).
    if os.environ.get("MOBILE_USE_NO_REPO_ENV") != "1":
        try:
            if platform == "ios":
                from iphone_harness.admin import _check_env_file
            else:
                from android_harness.admin import _check_env_file
            ok, _detail = _check_env_file()
            if ok:
                return None
        except Exception:
            pass
    return (
        f"No .env file found and {key} not set in environment.\n"
        f"   Fix: run `mobile-use init` (auto-fills from connected device).\n"
        f"   Or set {key}=<udid> manually before running."
    )


def _maybe_start_viewer(platform):
    """If MOBILE_USE_HEADED=1, spawn the MJPEG viewer + open browser. Returns
    the ViewerServer (or None). Caller must call .stop() at end. Failures
    log + return None — viewer is a "nice to have", never blocks the command.
    """
    if os.environ.get("MOBILE_USE_HEADED") != "1":
        return None
    try:
        from mobile_use.viewer.server import ViewerServer
        v = ViewerServer(platform=platform)
        v.start()
        print(f"[mobile-use] live viewer at {v.url}  (--headless to disable)",
              file=sys.stderr)
        try:
            import webbrowser
            webbrowser.open(v.url)
        except Exception:
            pass
        return v
    except Exception as e:
        print(f"[mobile-use] viewer failed to start: {e} (continuing without)",
              file=sys.stderr)
        return None


def _run_ios(args):
    """Delegate to iphone-harness."""
    if not args or args[0] in {"-h", "--help"}:
        print("mobile-use (iOS mode) — run `mobile-use --help` for full usage")
        return
    if args[0] == "--doctor":
        from iphone_harness.admin import run_doctor
        sys.exit(run_doctor())
    if args[0] == "--reload":
        from iphone_harness.admin import restart_daemon
        restart_daemon()
        print("iOS daemon stopped — will respawn on next call")
        return
    if args[0] != "-c" or len(args) < 2:
        sys.exit('Usage: mobile-use --ios -c "print(active_app())"')

    # Check env BEFORE importing helpers — helpers auto-loads .env from the
    # install directory which would mask the missing-env condition.
    env_err = _check_env_for_platform("ios")
    if env_err:
        sys.exit(env_err)

    import iphone_harness.helpers as _helpers
    from iphone_harness.admin import ensure_daemon
    try:
        ensure_daemon()
    except RuntimeError as e:
        sys.exit(f"{e}")

    viewer = _maybe_start_viewer("ios")
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    try:
        try:
            exec(args[1], ns)
        except SystemExit:
            raise
        except SyntaxError as e:
            sys.exit(f"Syntax error in your -c script: {e.msg} (line {e.lineno})")
        except Exception:
            _write_user_traceback()
            sys.exit(1)
    finally:
        if viewer is not None:
            viewer.stop()


def _run_android(args):
    """Delegate to android-harness."""
    if not args or args[0] in {"-h", "--help"}:
        print("mobile-use (Android mode) — run `mobile-use --help` for full usage")
        return
    if args[0] == "--doctor":
        from android_harness.admin import run_doctor
        sys.exit(run_doctor())
    if args[0] == "--reload":
        from android_harness.admin import restart_daemon
        restart_daemon()
        print("Android daemon stopped — will respawn on next call")
        return
    if args[0] != "-c" or len(args) < 2:
        sys.exit('Usage: mobile-use --android -c "print(active_app())"')

    env_err = _check_env_for_platform("android")
    if env_err:
        sys.exit(env_err)

    import android_harness.helpers as _helpers
    from android_harness.admin import ensure_daemon
    try:
        ensure_daemon()
    except RuntimeError as e:
        sys.exit(f"{e}")

    viewer = _maybe_start_viewer("android")
    ns = {k: v for k, v in vars(_helpers).items() if not k.startswith("_")}
    ns["__builtins__"] = __builtins__
    try:
        try:
            exec(args[1], ns)
        except SystemExit:
            raise
        except SyntaxError as e:
            sys.exit(f"Syntax error in your -c script: {e.msg} (line {e.lineno})")
        except Exception:
            _write_user_traceback()
            sys.exit(1)
    finally:
        if viewer is not None:
            viewer.stop()


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


def _reload_both():
    """Best-effort nuke of BOTH daemons (iOS + Android). Backs the bare
    `mobile-use --reload` recovery command when no platform is selected/detected.
    restart_daemon() only kills + cleans up stale state, so this needs no device.
    """
    did = []
    for label, mod in (("iOS", "iphone_harness.admin"), ("Android", "android_harness.admin")):
        try:
            admin = __import__(mod, fromlist=["restart_daemon"])
            admin.restart_daemon()
            did.append(label)
            print(f"[mobile-use] {label} daemon stopped — will respawn on next call")
        except Exception as e:
            print(f"[mobile-use] {label} daemon reload skipped: {e}", file=sys.stderr)
    if not did:
        print("[mobile-use] no daemons reloaded")


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
  mobile-use --doctor                  diagnose all platforms (device connectivity)
  mobile-use --ios --doctor            iOS only
  mobile-use --android --doctor        Android only
  mobile-use selfcheck [--train]       validate the harness itself — dep-rung matrix +
                                       action surface + training smoke (device-free)

SETUP & MAINTENANCE:
  mobile-use ios sign-wda [--check]    re-sign WebDriverAgent (iOS #1 blocker)
  mobile-use ios build-wda [--check]   build WebDriverAgent test target (iOS first-run)
  mobile-use android wifi <ip>         drive Android over Wi-Fi (adb tcpip + connect)
  mobile-use --reload                  nuke daemon (kills stale state)
  mobile-use --ios --reload            iOS daemon only
  mobile-use --android --reload        Android daemon only

MACROS:
  mobile-use macro record <name>       record a tap/swipe sequence (REPL)
  mobile-use macro replay <name>       literal re-run of recorded steps
  mobile-use macro replay <name> --smart   LLM-adaptive re-run (handles UI drift)
  mobile-use macro list                show saved macros
  mobile-use macro show <name>         print recorded .py script

DATA:
  mobile-use export-training [FILE]    export training data to JSONL
  mobile-use training-stats            show training data summary

PERCEPTION & LOCAL DETECTION (faster grounding, fewer VLM calls):
  mobile-use bench-perception          before/after perception latency (synthetic)
  mobile-use bench-perception --images DIR [--weights best.pt]
                                       REAL measured local grounding over screenshots
  mobile-use train-detector [--train]  distill self-labeled data into a YOLO-nano
                                       (needs `pip install 'mobile-use[yolo]'`)
  env: MU_LOCAL_DETECTOR=1 (template matcher) · MU_YOLO_DETECTOR=1 +
       MU_DETECTOR_WEIGHTS=best.pt (trained detector) · MU_LOCAL_SHORTCIRCUIT=1
       (skip the VLM on confident known elements) · MU_DETECTOR_MIN_CONF (gate)

OPTIONS:
  --name <NAME>           Named daemon for multiboxing (per-device socket + session).
  --remote-daemon <URI>   Client-only mode — drive a remote daemon over TCP.
                          Example: `--remote-daemon tcp://127.0.0.1:8763`
                          (use `ssh -L 8763:127.0.0.1:8763 <mac>` first).
                          Lets a Windows or Linux host control iOS via a Mac
                          running Appium+WebDriverAgent+iphone-harness daemon.
                          Sets IPH_CONNECT (--ios) or ANH_CONNECT (--android).
  --headed                Open a live device-screen viewer in your browser
                          while the command runs (MJPEG at ~6 fps, JPEG q=60).
  --headless              Explicit opt-out of viewer (default).
  --version               Show version

AUTO-DETECT: when exactly one device type is connected, --ios/--android
is inferred. When both connected, you must specify.

iOS FROM WINDOWS / LINUX (remote Mac bridge):
  On the Mac (one time):  mobile-use bootstrap --ios-only && mobile-use ios build-wda
  On the Mac (each run):  IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'
  On Windows/Linux:       ssh -L 8763:127.0.0.1:8763 mac     (in another shell)
  On Windows/Linux:       mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c '...'
  Full walkthrough:       SETUP.md → "iOS from Windows / Linux"

MULTI-DEVICE:
  mobile-use devices list             Auto-detect connected iOS + Android devices
                                      (uses idevice_id / adb under the hood).
  mobile-use devices status           Show running named daemons.
  mobile-use devices reload <name>    Restart one named daemon.
  mobile-use devices reload --all     Restart every running named daemon.
  mobile-use devices view             Live MJPEG grid of every connected device.

  Python API (auto-populated pool from discovery):
    from mobile_use import DevicePool
    pool = DevicePool.from_connected()    # no UDIDs to type
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
    from mobile_use._platform import ensure_utf8_streams
    ensure_utf8_streams()  # Windows cp1252 console can't encode '→'/box chars → crash

    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(HELP)
        return

    if args[0] == "--version":
        from mobile_use import __version__
        print(f"mobile-use {__version__}")
        return

    # Extract platform flag, --name, --remote-daemon, --headed/--headless
    platform = None
    instance_name = None
    remote_daemon = None
    headed = None  # None = default (headless), True = headed, False = explicit headless
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
        elif args[i] == "--remote-daemon" and i + 1 < len(args):
            remote_daemon = args[i + 1]
            i += 1
        elif args[i] == "--headed":
            headed = True
        elif args[i] == "--headless":
            headed = False
        else:
            remaining.append(args[i])
        i += 1

    if instance_name:
        if platform == "ios" or platform is None:
            os.environ["IPH_NAME"] = instance_name
        if platform == "android" or platform is None:
            os.environ["ANH_NAME"] = instance_name

    # --remote-daemon sets IPH_CONNECT / ANH_CONNECT for the chosen platform.
    # Validates the URI eagerly so bad input fails fast with a clear error.
    if remote_daemon:
        from iphone_harness import _ipc as _iph_ipc
        try:
            _iph_ipc.parse_endpoint(remote_daemon)
        except ValueError as e:
            sys.exit(f"Invalid --remote-daemon URI: {e}")
        if platform == "ios" or platform is None:
            os.environ["IPH_CONNECT"] = remote_daemon
        if platform == "android" or platform is None:
            os.environ["ANH_CONNECT"] = remote_daemon

    # --headed / --headless: tri-state. None = leave MOBILE_USE_HEADED untouched
    # (default off). True/False = explicit user choice; pass via mutable env so
    # subprocess-spawn paths (agent loop, etc) inherit, but restore on exit so
    # in-process pytest runs don't leak state into sibling tests.
    _prior_headed = os.environ.get("MOBILE_USE_HEADED")
    if headed is True:
        os.environ["MOBILE_USE_HEADED"] = "1"
    elif headed is False:
        os.environ["MOBILE_USE_HEADED"] = "0"

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

    if remaining and remaining[0] == "devices":
        from . import devices as _devices
        sys.exit(_devices.main(remaining[1:]))

    # wifi <action> — wireless connection management (both platforms).
    if remaining and remaining[0] == "wifi":
        if len(remaining) < 2 or remaining[1] in {"-h", "--help"}:
            print(
                "mobile-use wifi — wireless connection management:\n\n"
                "  reconnect [--json]    Re-establish every remembered wireless device\n"
                "                        (android: adb connect; ios: mDNS re-resolve).\n"
                "                        Devices are remembered by `--persist` (see\n"
                "                        `android wifi` / `ios wifi`).\n"
            )
            sys.exit(0 if len(remaining) >= 2 else 2)
        if remaining[1] == "reconnect":
            from . import devices as _devices
            sys.exit(_devices.wifi_reconnect_main(remaining[2:]))
        sys.exit(f"Unknown `mobile-use wifi` action: {remaining[1]!r}. Try `mobile-use wifi --help`.")

    # ios <action> — iOS-specific subcommands.
    if remaining and remaining[0] == "ios":
        if len(remaining) < 2 or remaining[1] in {"-h", "--help"}:
            print(
                "mobile-use ios — iOS-specific subcommands:\n\n"
                "  sign-wda [--check]    Sign WebDriverAgent in Xcode (the #1 setup blocker).\n"
                "                        --check exits 0 if already signed, 1 otherwise.\n"
                "  build-wda [--check]   Build the WebDriverAgent test target (first-run setup).\n"
                "                        --check exits 0 if already built, 1 otherwise.\n"
                "  wifi [host]           Drive cable-free over Wi-Fi (mDNS-preferred WDA URL).\n"
                "                        Prints/persists IPH_WDA_URL. --check probes reachability.\n"
                "  tunnel [--check]      RemoteXPC tunnel status + the one `sudo` start command\n"
                "                        (iOS 17+ needs it for cable-free to survive unplug).\n"
                "  install-wda <ipa>     Install a pre-signed WebDriverAgent ipa via\n"
                "                        pymobiledevice3 (Linux/Windows: no Mac at runtime).\n"
            )
            sys.exit(0 if len(remaining) >= 2 else 2)
        if remaining[1] == "sign-wda":
            from . import ios_wda
            sys.exit(ios_wda.main(remaining[2:]))
        if remaining[1] == "build-wda":
            from . import ios_wda
            sys.exit(ios_wda.build_main(remaining[2:]))
        if remaining[1] == "install-wda":
            from . import ios_wda
            sys.exit(ios_wda.install_wda_main(remaining[2:]))
        if remaining[1] == "wifi":
            from . import devices as _devices
            sys.exit(_devices.ios_wifi_main(remaining[2:]))
        if remaining[1] == "tunnel":
            from . import devices as _devices
            sys.exit(_devices.ios_tunnel_main(remaining[2:]))
        sys.exit(f"Unknown `mobile-use ios` action: {remaining[1]!r}. Try `mobile-use ios --help`.")

    # android <action> — Android-specific subcommands.
    if remaining and remaining[0] == "android":
        if len(remaining) < 2 or remaining[1] in {"-h", "--help"}:
            print(
                "mobile-use android — Android-specific subcommands:\n\n"
                "  wifi <ip[:port]>      Drive the device over Wi-Fi (adb tcpip + adb connect).\n"
                "                        Prints the ip:port serial to set as ANH_UDID.\n"
                "                        --persist saves it to .env + the remember-store.\n"
                "                        Add --disconnect to drop the wireless connection.\n"
                "  pair <ip:port> <code> Pair via Wireless debugging (Android 11+, no cable ever).\n"
                "                        Pairing survives reboots; then `android wifi ... --persist`.\n"
            )
            sys.exit(0 if len(remaining) >= 2 else 2)
        if remaining[1] == "wifi":
            from . import devices as _devices
            sys.exit(_devices.android_wifi_main(remaining[2:]))
        if remaining[1] == "pair":
            from . import devices as _devices
            sys.exit(_devices.android_pair_main(remaining[2:]))
        sys.exit(f"Unknown `mobile-use android` action: {remaining[1]!r}. Try `mobile-use android --help`.")

    # macro <subcmd> — record/replay action sequences (literal + smart)
    if remaining and remaining[0] == "macro":
        from . import macro
        sys.exit(macro.main(remaining[1:], platform=platform))

    # Training data commands
    if remaining and remaining[0] == "export-training":
        from .collector import export_training_data
        out = remaining[1] if len(remaining) > 1 else "training-export.jsonl"
        count = export_training_data(out)
        print(f"Exported {count} events → {out}")
        return

    if remaining and remaining[0] == "train-detector":
        from .train_detector import train_main
        sys.exit(train_main(remaining[1:]))

    if remaining and remaining[0] == "bench-perception":
        from .perception_cache import bench_main
        sys.exit(bench_main(remaining[1:]))

    if remaining and remaining[0] == "selfcheck":
        from .selfcheck import selfcheck_main
        sys.exit(selfcheck_main(remaining[1:]))

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
        except ImportError as e:
            # run_agent IS implemented — an ImportError here means a broken
            # install / missing dep, so surface the real cause, not a false claim.
            print(f"Could not load the agent loop: {e}. Reinstall with `pip install -e .`",
                  file=sys.stderr)
            sys.exit(1)
        run_agent(platform=platform or _detect_platform(), args=remaining[1:])
        return

    # Auto-detect platform if not specified
    if platform is None:
        # `mobile-use --reload` with no platform/device must still nuke daemons —
        # that is the stale-state recovery command, and the no-device case is
        # exactly when you need it. Don't fall through to the auto-detect gate.
        if "--reload" in remaining:
            _reload_both()
            return
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

    try:
        if platform == "ios":
            _run_ios(remaining)
        elif platform == "android":
            _run_android(remaining)
    finally:
        # Restore prior MOBILE_USE_HEADED so in-process pytest doesn't leak
        # state between tests that call cli.main() with different --headed.
        if headed is not None:
            if _prior_headed is None:
                os.environ.pop("MOBILE_USE_HEADED", None)
            else:
                os.environ["MOBILE_USE_HEADED"] = _prior_headed


if __name__ == "__main__":
    main()
