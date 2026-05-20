# Pixel Launcher — organize home screen

Package: `com.google.android.apps.nexuslauncher`. Field-tested on Pixel +
Android 14.

Covers: creating a folder by drag-and-drop, renaming a folder, moving an
icon between pages, removing an icon from home (keep in drawer), reordering
pages.

```python
press_home(); wait(0.4)
```

## Create a folder

```python
src = find(text="Chrome")
dst = find(text="Drive")
assert src and dst, "both icons must be on the same home page"

# Long-press then drag with a slow speed — fast drags create shortcut
# placement, slow ones trigger folder-creation.
long_press(src["cx"], src["cy"], duration=0.3); wait(0.3)
appium("mobile: dragGesture",
       startX=src["cx"], startY=src["cy"],
       endX=dst["cx"], endY=dst["cy"],
       speed=300)
wait(1.0)
# A folder is now open. Tap the name field to rename.
name = find(class_name="android.widget.EditText") or find(content_desc="Edit folder name")
if name:
    tap(name); wait(0.3)
    # Clear existing default ("Unnamed Folder").
    appium("mobile: longClickGesture",
           elementId=name["id"], duration=400)
    wait(0.3)
    type_text("Productivity")
    press_back()  # closes keyboard, commits name
    wait(0.4)
press_back()  # close the folder
wait(0.4)
```

## Move app into existing folder

```python
src = find(text="Notion")
folder = find(content_desc="Productivity")
long_press(src["cx"], src["cy"], duration=0.3); wait(0.3)
appium("mobile: dragGesture",
       startX=src["cx"], startY=src["cy"],
       endX=folder["cx"], endY=folder["cy"],
       speed=300)
wait(0.8)
```

## Pull app out of a folder

```python
tap(find(content_desc="Productivity")); wait(0.6)
src = find(text="Notion")  # now inside the open folder popover
w, h = window_size()["width"], window_size()["height"]

long_press(src["cx"], src["cy"], duration=0.3); wait(0.3)
appium("mobile: dragGesture",
       startX=src["cx"], startY=src["cy"],
       endX=w - 80, endY=h - 200,
       speed=400)
wait(0.8)
```

## Remove from home (keep in drawer)

```python
icon = find(text="Maps")
long_press(icon["cx"], icon["cy"], duration=0.4); wait(0.3)
appium("mobile: dragGesture",
       startX=icon["cx"], startY=icon["cy"],
       endX=window_size()["width"] // 2, endY=80,
       speed=400)
wait(0.4)
# Pixel's drop zone has two slots: "Remove" (X icon) on the left, "Uninstall"
# (trash icon) on the right. The X removes from home only.
remove = find(text="Remove") or find(content_desc="Remove")
if remove:
    # Already over the drop zone; release happened. Confirm if dialog appears.
    pass
else:
    # Need to nudge further. Re-do with explicit endpoint.
    appium("mobile: dragGesture",
           startX=icon["cx"], startY=icon["cy"],
           endX=window_size()["width"] // 4, endY=80,
           speed=400)
    wait(0.4)
```

A simpler alternative — long-press → "Remove" entry in the popup (Pixel
exposes this on Android 14+).

## Move between pages

```python
icon = find(text="Slack")
w, h = window_size()["width"], window_size()["height"]

long_press(icon["cx"], icon["cy"], duration=0.3); wait(0.3)
# Drag toward the right edge — page flips after dwelling ~600ms there.
appium("mobile: dragGesture",
       startX=icon["cx"], startY=icon["cy"],
       endX=w - 20, endY=h // 2,
       speed=200)
wait(1.2)
# Then release toward the target spot on the new page.
appium("mobile: dragGesture",
       startX=w - 20, startY=h // 2,
       endX=w // 2, endY=h // 2,
       speed=300)
wait(0.8)
```

The two-stage gesture is necessary because `mobile: dragGesture` doesn't
support dwell-at-position. If your launcher has many pages, repeat the
edge-hover step per page.

## Reorder pages / hide a page

Long-press an empty area on home → "Home settings" → "Manage home screens"
on Pixel. Some launchers (Samsung, MIUI) instead pinch-out from the home
screen.

```python
long_press(window_size()["width"] // 2, window_size()["height"] - 80, duration=0.8)
wait(0.6)
hs = find(text="Home settings")
if hs:
    tap(hs); wait(1.0)
    # Configuration screen — no destructive bulk actions; user-driven.
```

## Verification

```python
press_home(); wait(0.4)
screenshot("/tmp/android-home-after.png")
```
