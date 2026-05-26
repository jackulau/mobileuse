#!/usr/bin/env python3
"""Multi-device demo: auto-discover, build a pool, broadcast a tap+screenshot.

Two modes:

  python3 docs/demos/multi-device-broadcast.py --mock
      Runs end-to-end against mocked daemons (CI-safe, no devices needed).

  python3 docs/demos/multi-device-broadcast.py
      Runs against REAL connected devices. Discovers iOS + Android via
      `mobile-use devices list`, builds a DevicePool, starts each device's
      daemon in parallel, broadcasts `tap_at_xy(200, 400)` + `screenshot()`,
      and prints a per-device result table.

      Requires:
        - iOS:     idevice_id + libimobiledevice + Xcode org id in env
                   (IPH_XCODE_ORG_ID, IPH_WDA_BUNDLE_ID).
        - Android: adb + Appium server + UIAutomator2 driver.
"""
import argparse
import os
import sys
from unittest.mock import patch


def _mock_run():
    """Run the same flow against mocked discovery + admin layers."""
    from mobile_use import devices as discovery
    from mobile_use.multibox import Device, DevicePool

    fake_entries = [
        {"platform": "ios", "udid": "MOCK-IOS-A", "name": "iPhone-mock-A"},
        {"platform": "ios", "udid": "MOCK-IOS-B", "name": "iPhone-mock-B"},
        {"platform": "android", "udid": "MOCK-AND-A", "name": "Pixel-mock-A"},
    ]

    with patch.object(discovery, "discover_connected", return_value=fake_entries):
        pool = DevicePool.from_connected(xcode_org_id="MOCK-TEAM",
                                         wda_bundle_id="com.mock.wda")

        print(f"Discovered {len(pool)} devices ({len(pool.ios_devices)} iOS, "
              f"{len(pool.android_devices)} Android):")
        for d in pool:
            url = d._env.get("IPH_APPIUM_URL", d._env.get("ANH_APPIUM_URL", "?"))
            print(f"  {d.name:24} {d.platform:8} appium={url}")

        for d in pool:
            d._load = lambda *_: None
            d._helpers = type("H", (), {"screenshot": lambda *a, **k: b"\x89PNG-mock"})()
            d._admin = type("A", (), {
                "ensure_daemon": lambda *a, **k: None,
                "daemon_alive": lambda *a, **k: True,
            })()

        ready = pool.ensure_all_ready(max_workers=4)
        print(f"\nAll devices ready: {ready}")

        results = pool.broadcast(lambda d: f"tap+screenshot on {d.name}")
        print(f"\nBroadcast complete — {len(results)} responses:")
        for name, payload in results.items():
            tag = "OK" if "result" in payload else "ERR"
            value = payload.get("result") or payload.get("error")
            print(f"  [{tag}] {name:24} {value}")


def _real_run():
    """Run against actually-connected devices."""
    from mobile_use.multibox import DevicePool

    try:
        pool = DevicePool.from_connected(
            xcode_org_id=os.environ.get("IPH_XCODE_ORG_ID"),
            wda_bundle_id=os.environ.get("IPH_WDA_BUNDLE_ID"),
        )
    except RuntimeError as e:
        print(f"discovery failed: {e}", file=sys.stderr)
        return 1

    print(f"Discovered {len(pool)} devices:")
    for d in pool:
        print(f"  {d.name:24} {d.platform}")

    print("\nStarting daemons (parallel)…")
    ready = pool.ensure_all_ready(max_workers=4)
    for name, state in ready.items():
        print(f"  {name:24} {state}")

    print("\nBroadcasting tap_at_xy(200, 400) + screenshot()…")

    def action(d):
        d.tap_at_xy(200, 400)
        png = d.screenshot()
        return f"shot={len(png)}B"

    results = pool.broadcast(action, max_workers=4)
    print(f"\nResults ({len(results)} devices):")
    for name, payload in results.items():
        tag = "OK" if "result" in payload else "ERR"
        value = payload.get("result") or payload.get("error")
        print(f"  [{tag}] {name:24} {value}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true",
                    help="Run against mocked discovery + daemons (no devices needed).")
    args = ap.parse_args()

    if args.mock:
        _mock_run()
        return 0
    return _real_run()


if __name__ == "__main__":
    sys.exit(main())
