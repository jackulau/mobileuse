"""android-harness CLI — part of mobile-use (github.com/jackulau/mobile_use).

  android-harness -c '<python>'    run a one-shot script with helpers pre-imported
  android-harness --doctor         diagnose Appium / device / daemon
  android-harness --reload         stop the daemon (next call respawns)
  android-harness --version
"""
import sys

from .admin import (
    _version,
    NAME,
    daemon_alive,
    ensure_daemon,
    restart_daemon,
    run_doctor,
)
from .helpers import *  # noqa: F401,F403

HELP = """android-harness

Direct Android control via Appium/UIAutomator2. Helpers pre-imported. Daemon auto-starts.

Usage:
  android-harness -c '<python>'    run a one-shot script
  android-harness --doctor         diagnose install + device
  android-harness --reload         stop the daemon; next call respawns
  android-harness --version

Required env (or in <repo>/.env):
  ANH_UDID                  Android device serial  (find with `adb devices`)

Optional:
  ANH_PLATFORM_VERSION      e.g. 14.0
  ANH_DEVICE_NAME           default: Android
  ANH_APPIUM_URL            default http://127.0.0.1:4723
  ANH_NAME                  daemon namespace (for multi-device)  default: default
  ANH_DOMAIN_SKILLS=1       enable per-app skill discovery
"""


def main():
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(HELP)
        return
    if args[0] == "--version":
        print(_version() or "unknown")
        return
    if args[0] == "--doctor":
        sys.exit(run_doctor())
    if args[0] == "--reload":
        restart_daemon()
        print("daemon stopped — will respawn on next call")
        return
    if args[0] != "-c" or len(args) < 2:
        sys.exit('Usage: android-harness -c "print(active_app())"')

    ensure_daemon()
    exec(args[1], globals())


if __name__ == "__main__":
    main()
