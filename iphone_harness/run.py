"""iphone-harness CLI — part of mobile-use (github.com/jackulau/mobile_use).

  iphone-harness -c '<python>'    run a one-shot script with helpers pre-imported
  iphone-harness --doctor         diagnose Appium / device / daemon
  iphone-harness --reload         stop the daemon (next call respawns)
  iphone-harness --version
"""
import sys

from .admin import (
    NAME,
    _version,
    daemon_alive,
    ensure_daemon,
    restart_daemon,
    run_doctor,
)
from .helpers import *  # noqa: F401,F403 — pre-import helpers into globals for `exec`

HELP = """iphone-harness

Direct iPhone control via Appium. Helpers pre-imported. Daemon auto-starts.

Usage:
  iphone-harness -c '<python>'    run a one-shot script
  iphone-harness --doctor         diagnose install + device
  iphone-harness --reload         stop the daemon; next call respawns
  iphone-harness --version

Required env (or in <repo>/.env):
  IPH_UDID                  iPhone UDID  (find with `idevice_id -l`)
  IPH_PLATFORM_VERSION      e.g. 18.3.2  (optional; XCUITest infers if omitted)
  IPH_XCODE_ORG_ID          your Apple Team ID (for WDA codesign)
  IPH_WDA_BUNDLE_ID         e.g. com.you.iphonecontrol.wda

Optional:
  IPH_APPIUM_URL            default http://127.0.0.1:4723
  IPH_NAME                  daemon namespace (for multi-device)  default: default
  IPH_DOMAIN_SKILLS=1       enable per-app skill discovery
"""


def main():
    from mobile_use._platform import ensure_utf8_streams
    ensure_utf8_streams()  # Windows cp1252 console can't encode '→'/box chars → crash

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
        sys.exit("Usage: iphone-harness -c \"print(active_app())\"")

    ensure_daemon()
    exec(args[1], globals())


if __name__ == "__main__":
    main()
