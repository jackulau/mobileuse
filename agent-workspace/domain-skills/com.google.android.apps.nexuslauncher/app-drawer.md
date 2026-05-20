# Pixel Launcher — App Drawer

Package: `com.google.android.apps.nexuslauncher`. Field-tested on Pixel +
Android 14.

The App Drawer is the bottom-up swipe overlay listing every installed app
alphabetically. Use it to:

- Find an app whose home-screen icon you've removed.
- Install a shortcut on the home screen.
- Uninstall an app whose home icon is hidden.

## Open the drawer

```python
press_home(); wait(0.4)
w, h = window_size()["width"], window_size()["height"]
# Swipe from the bottom-third toward the top.
swipe(w // 2, h - 60, w // 2, h // 3, duration=0.25)
wait(0.6)

# The search bar at the top is the canonical drawer-open signal.
assert find(text="Search apps") or find(content_desc="Search apps"), \
    "drawer did not open"
```

## Search the drawer

```python
sb = find(text="Search apps") or find(content_desc="Search apps")
tap(sb); wait(0.3)
type_text("docs")
wait(0.5)
# First result is the canonical Google Docs app.
result = find(text="Docs")
tap(result); wait(2.0)
```

## Long-press in the drawer

Same popup as the home screen — App info / Pause app / Uninstall (when
available) / Widgets.

```python
icon = find(text="Junk App")  # ensure scrolled into view first
long_press(icon["cx"], icon["cy"], duration=0.6); wait(0.6)
ui = find(text="Uninstall") or find(text="App info")
if ui and ui["text"] == "Uninstall":
    tap(ui); wait(0.6)
    tap(find(text="OK")); wait(1.5)
```

## Pin an app to the home screen

```python
icon = find(text="Calculator")
long_press(icon["cx"], icon["cy"], duration=0.4); wait(0.4)
# Drag down to home, then release.
appium("mobile: dragGesture",
       startX=icon["cx"], startY=icon["cy"],
       endX=window_size()["width"] // 2,
       endY=window_size()["height"] - 200,
       speed=300)
wait(0.8)
```

## Enumerate every drawer app

```python
def list_drawer_apps():
    out = []
    invalidate_tree_cache()
    last = None
    for _ in range(30):
        items = [el.get("text", "") for el in find_all(visible_only=True)
                 if el.get("resource_id", "").endswith(":id/icon_title")]
        out.extend(items)
        if items and items[-1] == last:
            break
        last = items[-1] if items else None
        # Scroll down within the drawer.
        scroll(direction="down"); wait(0.3)
    # Dedupe while keeping order.
    seen = set(); uniq = []
    for x in out:
        if x and x not in seen:
            seen.add(x); uniq.append(x)
    return uniq
```

## Close the drawer

```python
press_back(); wait(0.4)   # closes drawer, returns to home
# or
press_home(); wait(0.4)   # same effect
```

## OEM differences

- **Samsung One UI** has an alphabetical drawer too, but the swipe origin
  is slightly different (mid-bottom vs anywhere on home).
- **MIUI / HyperOS** has no drawer by default — all apps live on home
  screens. Skill is N/A; use `com.android.settings/uninstall-app.md`.
- **Nothing OS** has a drawer toggleable via Home settings.
