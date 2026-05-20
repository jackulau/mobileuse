#!/usr/bin/env python3
"""End-to-end iOS demo: clean and organize the phone.

What it does (in order):

  1. List installed apps and their sizes (via agent_helpers.list_installed_apps).
  2. Print a storage summary (Used / Total).
  3. Take a "before" screenshot of the home screen.
  4. Organize 3 apps into a folder named 'Social' (Twitter, LinkedIn, Reddit by default).
  5. Uninstall a designated test app (defaults to TEST_APP env or 'Chess').
  6. Empty Photos -> Recently Deleted (frees the space from previous deletes).
  7. Take an "after" screenshot.

Honor `DRY_RUN=1` to perform only steps 1-3 + screenshots (no destructive ops).
Honor `TEST_APP=<label>` to override the app to uninstall.

Requires:
  - Connected iPhone with `IPH_UDID` set (in env or `.env`).
  - `iphone-harness` on $PATH (`pip install -e .` from the repo root).
  - Appium server running (`appium --base-path /`).

Run:
  python3 docs/demos/clean-and-organize-ios.py
  DRY_RUN=1 python3 docs/demos/clean-and-organize-ios.py
  TEST_APP=Chess python3 docs/demos/clean-and-organize-ios.py
"""
import os
import shutil
import subprocess
import sys
import textwrap


def _harness_available():
    if not shutil.which("iphone-harness"):
        return False, "iphone-harness CLI not on $PATH (pip install -e .)"
    if not (os.environ.get("IPH_UDID") or _env_file_has("IPH_UDID")):
        return False, "IPH_UDID env var or .env entry missing — no iPhone connected"
    return True, None


def _env_file_has(key):
    for p in (".env", "agent-workspace/.env"):
        try:
            with open(p) as f:
                for line in f:
                    if line.strip().startswith(f"{key}="):
                        return True
        except FileNotFoundError:
            continue
    return False


def _run(script_body, *, timeout=180):
    """Invoke `iphone-harness -c <body>` and return stdout text."""
    result = subprocess.run(
        ["iphone-harness", "-c", textwrap.dedent(script_body)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        sys.stderr.write(f"iphone-harness FAILED ({result.returncode}):\n")
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def step_inventory():
    print("\n=== STEP 1 — installed apps ===")
    out = _run("""
        from agent_helpers import list_installed_apps, storage_summary
        import json
        apps = list_installed_apps()
        print(json.dumps(apps, indent=2))
        print('---')
        print(json.dumps(storage_summary(), indent=2))
    """, timeout=300)
    print(out)


def step_home_snapshot(tag):
    print(f"\n=== STEP — home screen snapshot ({tag}) ===")
    out = _run(f"""
        appium("mobile: pressButton", name="home")
        wait(0.6)
        appium("mobile: pressButton", name="home")
        wait(0.4)
        path = screenshot("/tmp/ios-home-{tag}.png")
        print('saved:', path)
    """)
    print(out)


def step_organize_folder():
    print("\n=== STEP — organize 'Social' folder ===")
    out = _run("""
        appium("mobile: pressButton", name="home")
        wait(0.5)
        appium("mobile: pressButton", name="home")
        wait(0.4)

        TARGETS = ["Twitter", "LinkedIn", "Reddit"]
        present = [find(label=l, type="XCUIElementTypeIcon") for l in TARGETS]
        present = [(l, el) for l, el in zip(TARGETS, present) if el is not None]

        if len(present) < 2:
            print("not enough target icons on the current page — skipping organize step")
        else:
            a_label, a = present[0]
            b_label, b = present[1]
            appium("mobile: dragFromToForDuration",
                   fromX=a["cx"], fromY=a["cy"],
                   toX=b["cx"], toY=b["cy"],
                   duration=1.2)
            wait(1.5)
            tf = find(type="XCUIElementTypeTextField")
            if tf:
                tap(tf); wait(0.3)
                try:
                    appium("mobile: clear", elementId=tf["id"])
                except Exception:
                    pass
                type_text("Social"); wait(0.3)
            for l, el in present[2:]:
                fresh = find(label=l, type="XCUIElementTypeIcon")
                folder = find(label="Social", type="XCUIElementTypeIcon")
                if fresh and folder:
                    appium("mobile: dragFromToForDuration",
                           fromX=fresh["cx"], fromY=fresh["cy"],
                           toX=folder["cx"], toY=folder["cy"],
                           duration=1.0)
                    wait(1.0)
            appium("mobile: pressButton", name="home")
            wait(0.4)
            print(f"folder 'Social' created with {len(present)} apps")
    """)
    print(out)


def step_uninstall(app):
    print(f"\n=== STEP — uninstall {app!r} ===")
    out = _run(f"""
        from agent_helpers import uninstall_app
        import json
        r = uninstall_app({app!r})
        print(json.dumps(r, indent=2))
    """, timeout=240)
    print(out)


def step_empty_recently_deleted():
    print("\n=== STEP — empty Photos -> Recently Deleted ===")
    out = _run("""
        appium("mobile: terminateApp", bundleId="com.apple.mobileslideshow")
        wait(0.5)
        appium("mobile: launchApp", bundleId="com.apple.mobileslideshow")
        wait(2.0)
        a = find(label="Albums", type="XCUIElementTypeButton")
        if a: tap(a); wait(0.6)
        for _ in range(8):
            rd = find(label="Recently Deleted", type="XCUIElementTypeCell")
            if rd: break
            scroll_by(dy=-400, velocity=400); wait(0.3)
        if rd is None:
            print("Recently Deleted album not found — skipping")
        else:
            tap(rd); wait(2.5)
            if find(label="1", type="XCUIElementTypeKey"):
                print("Face ID/PIN gate active — cannot empty without unlocking")
            else:
                sel = find(label="Select", type="XCUIElementTypeButton")
                if sel:
                    tap(sel); wait(0.4)
                    da = find(label="Delete All", type="XCUIElementTypeButton")
                    if da:
                        tap(da); wait(0.5)
                        from agent_helpers import confirm_destructive
                        ok = confirm_destructive("Delete From All Devices") or \\
                             confirm_destructive("Delete")
                        print("recently-deleted emptied:", ok)
                    else:
                        print("nothing to delete")
                else:
                    print("Select unavailable — bin likely empty")
    """, timeout=240)
    print(out)


def main():
    ok, why = _harness_available()
    dry = os.environ.get("DRY_RUN") == "1"
    app = os.environ.get("TEST_APP", "Chess")

    print(f"clean-and-organize-ios demo  (DRY_RUN={dry}, TEST_APP={app!r})")
    if not ok:
        print(f"\n[skip] {why}")
        print("This demo needs a real iPhone. To preview the script, set DRY_RUN=1 in a")
        print("connected-device session.")
        return 0

    step_inventory()
    step_home_snapshot("before")

    if dry:
        print("\nDRY_RUN=1 — skipping destructive steps.")
        return 0

    step_organize_folder()
    step_uninstall(app)
    step_empty_recently_deleted()
    step_home_snapshot("after")

    print("\ndone. compare /tmp/ios-home-before.png and /tmp/ios-home-after.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
