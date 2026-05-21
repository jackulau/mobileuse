#!/usr/bin/env python3
"""End-to-end Android demo: clean and organize the phone.

What it does (in order):

  1. List installed third-party apps (via agent_helpers.list_installed_apps).
  2. Print a storage summary (Used / Free / Total).
  3. Take a "before" screenshot of the home screen.
  4. Organize 3 apps into a folder (Chrome + Drive + Docs by default).
  5. Uninstall a designated test app (defaults to TEST_PACKAGE env or
     'com.android.chrome' is NOT a default — see safety note below).
  6. Empty Google Photos Bin (frees prior deletes).
  7. Take an "after" screenshot.

Safety: by default, NO test app is uninstalled. Set TEST_PACKAGE=<package> to
opt in. The script will skip uninstall otherwise. This prevents accidents on
real devices.

Honor `DRY_RUN=1` to perform only steps 1-3 + screenshots (no destructive ops).

Requires:
  - Connected Android with `ANH_UDID` set (in env or `.env`).
  - `android-harness` on $PATH (`pip install -e .` from the repo root).
  - Appium server running (`appium --base-path /`).

Run:
  python3 docs/demos/clean-and-organize-android.py
  DRY_RUN=1 python3 docs/demos/clean-and-organize-android.py
  TEST_PACKAGE=com.example.junkapp python3 docs/demos/clean-and-organize-android.py
"""
import os
import shutil
import subprocess
import sys
import textwrap


def _harness_available():
    if not shutil.which("android-harness"):
        return False, "android-harness CLI not on $PATH (pip install -e .)"
    if not (os.environ.get("ANH_UDID") or _env_file_has("ANH_UDID")):
        return False, "ANH_UDID env var or .env entry missing — no Android device"
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
    result = subprocess.run(
        ["android-harness", "-c", textwrap.dedent(script_body)],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        sys.stderr.write(f"android-harness FAILED ({result.returncode}):\n")
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def step_inventory():
    print("\n=== STEP 1 — installed apps ===")
    out = _run("""
        import json
        # list_installed_apps, storage_summary auto-loaded into globals by harness
        apps = list_installed_apps()
        print(json.dumps(apps[:30], indent=2))
        print(f'... and {max(0, len(apps) - 30)} more.')
        print('---')
        print(json.dumps(storage_summary(), indent=2))
    """, timeout=240)
    print(out)


def step_home_snapshot(tag):
    print(f"\n=== STEP — home screen snapshot ({tag}) ===")
    out = _run(f"""
        press_home(); wait(0.4)
        press_home(); wait(0.4)
        path = screenshot("/tmp/android-home-{tag}.png")
        print('saved:', path)
    """)
    print(out)


def step_organize_folder():
    print("\n=== STEP — organize 'Productivity' folder ===")
    out = _run("""
        press_home(); wait(0.4)
        TARGETS = ["Chrome", "Drive", "Docs"]
        present = []
        for label in TARGETS:
            el = find(text=label) or find(content_desc=label)
            if el:
                present.append((label, el))

        if len(present) < 2:
            print("not enough target icons on home — skipping organize step")
        else:
            a_label, a = present[0]
            b_label, b = present[1]
            long_press(a["cx"], a["cy"], duration=0.3); wait(0.3)
            appium("mobile: dragGesture",
                   startX=a["cx"], startY=a["cy"],
                   endX=b["cx"], endY=b["cy"],
                   speed=300)
            wait(1.2)
            name = find(class_name="android.widget.EditText")
            if name:
                tap(name); wait(0.3)
                type_text("Productivity")
                press_back(); wait(0.4)
            for label, el in present[2:]:
                fresh = find(text=label) or find(content_desc=label)
                folder = find(content_desc="Productivity") or find(text="Productivity")
                if fresh and folder:
                    long_press(fresh["cx"], fresh["cy"], duration=0.3); wait(0.3)
                    appium("mobile: dragGesture",
                           startX=fresh["cx"], startY=fresh["cy"],
                           endX=folder["cx"], endY=folder["cy"],
                           speed=300)
                    wait(0.8)
            press_back(); wait(0.4)
            print(f"folder 'Productivity' created with {len(present)} apps")
    """)
    print(out)


def step_uninstall(package):
    if not package:
        print("\n=== STEP — uninstall ===  (skipped, set TEST_PACKAGE=... to opt in)")
        return
    print(f"\n=== STEP — uninstall {package!r} ===")
    out = _run(f"""
        import json
        r = uninstall_app({package!r})  # auto-loaded by harness
        print(json.dumps(r, indent=2))
    """, timeout=240)
    print(out)


def step_empty_photos_bin():
    print("\n=== STEP — empty Google Photos -> Bin ===")
    out = _run("""
        try:
            appium("mobile: startActivity",
                   package="com.google.android.apps.photos",
                   activity=".home.HomeActivity")
        except Exception as e:
            print("Photos app not present:", e)
        else:
            wait(2.5)
            lib = find(text="Library") or find(content_desc="Library")
            if lib:
                tap(lib); wait(0.8)
            b = find(text="Bin") or find(content_desc="Bin") or find(text="Trash")
            if not b:
                print("Bin card not found — Photos version differs")
            else:
                tap(b); wait(1.5)
                menu = find(content_desc="More options") or find(content_desc="More")
                if menu:
                    tap(menu); wait(0.4)
                    e = find(text="Empty bin") or find(text="Empty trash")
                    if not e:
                        print("Empty action not in menu — bin already empty")
                    else:
                        tap(e); wait(0.5)
                        ok = confirm_destructive("Delete") or \\
                             confirm_destructive("Permanently delete") or \\
                             confirm_destructive("Empty bin")
                        print("bin emptied:", ok)
                else:
                    print("no overflow menu — UI changed")
    """, timeout=240)
    print(out)


def main():
    import argparse
    p = argparse.ArgumentParser(prog="clean-and-organize-android",
                                description="Clean+organize an Android via mobile_use.")
    p.add_argument("--check", action="store_true",
                   help="Preflight only: report whether the harness path is ready, then exit.")
    p.add_argument("--dry-run", action="store_true",
                   help="Run inventory + screenshots only (no destructive ops). Equivalent to DRY_RUN=1.")
    args = p.parse_args()

    ok, why = _harness_available()
    dry = args.dry_run or (os.environ.get("DRY_RUN") == "1")
    pkg = os.environ.get("TEST_PACKAGE", "")

    print(f"clean-and-organize-android demo  (DRY_RUN={dry}, TEST_PACKAGE={pkg or '<none>'})")

    if args.check:
        if ok:
            print("\n[check] harness path OK — connected, CLI on PATH, .env loaded.")
            return 0
        print(f"\n[check] FAIL: {why}")
        print("  Fix:  mobile-use bootstrap   (installs CLIs + deps)")
        print("        mobile-use init        (auto-fills .env from connected device)")
        print("        mobile-use --doctor    (full diagnostic)")
        return 2

    if not ok:
        print(f"\n[skip] {why}")
        print("This demo needs a real Android device. Setup in three commands:")
        print("  mobile-use bootstrap   (installs Appium + uiautomator2 + deps)")
        print("  mobile-use init        (writes .env from connected device)")
        print("  mobile-use quickstart  (verifies the whole chain)")
        print("Or DRY_RUN=1 + a connected device to preview safely.")
        return 2

    try:
        step_inventory()
        step_home_snapshot("before")

        if dry:
            print("\nDRY_RUN=1 — skipping destructive steps.")
            return 0

        step_organize_folder()
        step_uninstall(pkg)
        step_empty_photos_bin()
        step_home_snapshot("after")
    except SystemExit:
        print("\n[hint] Diagnose with: android-harness --doctor")
        raise
    except Exception as e:
        print(f"\n[fail] {type(e).__name__}: {e}")
        print("[hint] Diagnose with: android-harness --doctor")
        return 1

    print("\ndone. compare /tmp/android-home-before.png and /tmp/android-home-after.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
