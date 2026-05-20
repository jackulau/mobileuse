# Pixel Launcher — long-press to uninstall

Package: `com.google.android.apps.nexuslauncher`. Field-tested on Pixel +
Android 14 (Pixel/AOSP launcher).

Two visual paths. Both end in the same Settings → App info uninstall flow,
so the helper in `agent_helpers.uninstall_app(...)` is still the right entry
point — but for screen-driven flows or when Settings is restricted, this
launcher route works.

## Path A — long-press → App info → Uninstall

```python
# Make sure we're on the launcher home screen.
press_home(); wait(0.4)

# Find the icon by visible text. Icons are usually `TextView` children of a
# parent View with resource_id ending in :id/launcher.
icon = find(text="Junk App") or find(content_desc="Junk App")
if icon is None:
    # Open the drawer if not on a visible page.
    swipe(window_size()["width"] // 2, window_size()["height"] - 100,
          window_size()["width"] // 2, window_size()["height"] // 3,
          duration=0.3)
    wait(0.6)
    icon = find(text="Junk App") or find(content_desc="Junk App")
if icon is None:
    raise RuntimeError("icon not on home or drawer")

# Long-press opens the contextual popup with "App info / Pause app /
# Uninstall (for sideloaded) / Widgets".
long_press(icon["cx"], icon["cy"], duration=0.7)
wait(0.6)

# Pixel launcher offers "App info" by default for sideloaded apps; some have
# a direct "Uninstall" entry. Try Uninstall first.
direct = find(text="Uninstall") or find(content_desc="Uninstall")
if direct:
    tap(direct); wait(0.6)
    # Confirmation dialog same as Settings flow.
    for label in ("OK", "Uninstall"):
        c = find(text=label)
        if c: tap(c); break
    wait(1.5)
else:
    tap(find(text="App info")); wait(1.5)
    # Now we're in Settings → App info; reuse com.android.settings/uninstall-app.md.
    tap(find(text="Uninstall")); wait(0.6)
    for label in ("OK", "Uninstall"):
        c = find(text=label)
        if c: tap(c); break
    wait(1.5)
```

## Path B — drag icon to the Uninstall target

Pixel launcher reveals an "Uninstall" trash zone at the top while dragging.

```python
press_home(); wait(0.4)
icon = find(text="Junk App")

# Hold then drag toward the top of the screen.
long_press(icon["cx"], icon["cy"], duration=0.4)
wait(0.3)

w, h = window_size()["width"], window_size()["height"]
appium("mobile: dragGesture",
       startX=icon["cx"], startY=icon["cy"],
       endX=w // 2, endY=80,
       speed=400)
wait(0.6)
# Confirmation: "Uninstall this app?" → OK
tap(find(text="OK")); wait(1.5)
```

Path B is brittle because the drop zone is invisible until drag begins — if
`mobile: dragGesture` releases too quickly the icon snaps back. Path A is
preferred.

## When the icon is in a folder

```python
# Open the folder first.
tap(find(content_desc="Work folder")); wait(0.5)
icon = find(text="Junk App")
long_press(icon["cx"], icon["cy"], duration=0.7); wait(0.6)
# … same flow as Path A.
```

## Pinned vs drawer-only icons

A "drawer-only" icon (no home-screen pin) has `Uninstall` on long-press in
the App Drawer too. Open the drawer (`app-drawer.md`) then long-press the
icon there.

## OEM variance

- **Samsung One UI**: long-press shows a small toolbar with Uninstall as
  the first icon. Same flow otherwise.
- **MIUI / HyperOS**: long-press opens a different popup; "Uninstall" is a
  smaller text link below the icon. Use `find_fuzzy("Uninstall")`.
- **Nothing OS / OnePlus**: matches Pixel closely.

Skill recipes default to Pixel; for non-Pixel devices, replace icon finders
with `find_fuzzy(...)` to absorb label drift.
