# SpringBoard — organize the home screen

Bundle id: `com.apple.springboard`. Field-tested on iOS 18.3.

Covers: creating a folder by drag-and-drop, renaming a folder, moving an icon
into / out of an existing folder, hiding an app to App Library, reordering
pages.

All organization actions require **jiggle mode** (Edit Home Screen). Enter it
by long-pressing any empty area or by long-press → "Edit Home Screen" on any
icon. Exit by pressing Home or tapping "Done" in the top-right.

```python
def enter_jiggle():
    # Long-press an empty zone in the dock area — safer than on an icon.
    long_press(window_size()["width"] // 2, window_size()["height"] - 80, duration=1.2)
    wait(0.6)
    # Some iOS versions show a menu instead; fall back to long-pressing an icon
    # and tapping "Edit Home Screen".
    edit = find(label="Edit Home Screen", type="XCUIElementTypeButton")
    if edit:
        tap(edit); wait(0.5)

def exit_jiggle():
    done = find(label="Done", type="XCUIElementTypeButton")
    if done:
        tap(done); wait(0.5)
    else:
        appium("mobile: pressButton", name="home")
        wait(0.4)
```

## Create a folder by dragging app A onto app B

iOS only creates a folder when one icon is dropped on top of another. Tapping
two icons does nothing. The W3C `actions` API gives us the precise control
needed (the high-level `swipe` helper releases too quickly).

```python
enter_jiggle()

a = find(label="Twitter", type="XCUIElementTypeIcon")
b = find(label="LinkedIn", type="XCUIElementTypeIcon")
assert a and b, "both source and target icons must be on the same page"

# Build a manual pointer-action sequence: press on A, dwell, slow drag to B,
# dwell over B (so SpringBoard reads it as a folder-create), release.
ax, ay = a["cx"], a["cy"]
bx, by = b["cx"], b["cy"]

appium("mobile: dragFromToForDuration",
       fromX=ax, fromY=ay,
       toX=bx, toY=by,
       duration=1.2)
wait(1.4)  # Folder-create animation is slow; SpringBoard needs the dwell.

# A new folder opens automatically; iOS proposes a name based on the category.
# Rename it:
title = find(type="XCUIElementTypeTextField")  # the folder name field is the only TF
if title:
    tap(title); wait(0.3)
    appium("mobile: clear", elementId=title["id"])
    type_text("Social")
    wait(0.3)

# Close the folder by tapping outside the popover.
tap_at_xy(20, 100); wait(0.5)
exit_jiggle()
```

## Move an existing icon into an existing folder

Same dragFromToForDuration, but target a folder icon (an `XCUIElementTypeIcon`
whose label matches the folder name).

```python
enter_jiggle()
src = find(label="Notion", type="XCUIElementTypeIcon")
folder = find(label="Work", type="XCUIElementTypeIcon")
appium("mobile: dragFromToForDuration",
       fromX=src["cx"], fromY=src["cy"],
       toX=folder["cx"], toY=folder["cy"],
       duration=1.0)
wait(1.2)
exit_jiggle()
```

## Pull an app out of a folder

Open the folder, then drag the icon to the edge of the open folder window —
iOS lifts it back out to the home page.

```python
enter_jiggle()
tap(find(label="Social", type="XCUIElementTypeIcon")); wait(0.6)
icon = find(label="Twitter", type="XCUIElementTypeIcon")
w, h = window_size()["width"], window_size()["height"]
appium("mobile: dragFromToForDuration",
       fromX=icon["cx"], fromY=icon["cy"],
       toX=w // 2, toY=h - 100,   # drop on home dock area
       duration=1.0)
wait(1.0)
exit_jiggle()
```

## Hide an app to App Library (Remove from Home Screen)

This is the gentler sibling of Delete App. The app stays installed and
reachable through the App Library; it's only removed from the home screen.

```python
icon = find(label="Reddit", type="XCUIElementTypeIcon")
appium("mobile: touchAndHold", elementId=icon["id"], duration=0.9)
wait(0.6)
tap(find(label="Remove App", type="XCUIElementTypeButton")); wait(0.5)
tap(find(label="Remove from Home Screen", type="XCUIElementTypeButton"))
wait(0.8)
```

If the menu lists "Move to App Library" instead (iOS version differences),
that's the equivalent action.

## Reorder pages

Long-press an empty area to enter jiggle mode, then tap the page-indicator
dots at the bottom of the screen.

```python
enter_jiggle()
# Tap the dots — they are `XCUIElementTypeOther` elements directly above the dock.
dots = find(label="Page", type="XCUIElementTypeButton")  # iOS 18 surface
if dots is None:
    raise RuntimeError("page indicator not in tree — check iOS version")
tap(dots); wait(1.0)

# Each page shows as a thumbnail with a checkmark. Tap to hide a page, drag
# to reorder.
for page in find_all(type="XCUIElementTypeCell"):
    if page["label"].startswith("Page 4"):
        # Untick to hide
        cm = find(label="Selected", type="XCUIElementTypeButton")  # within `page`
        if cm: tap(cm); wait(0.3)

tap(find(label="Done", type="XCUIElementTypeButton")); wait(0.5)
exit_jiggle()
```

## Verification helpers

After any organize action, screenshot for an audit trail:

```python
screenshot("/tmp/home-after.png")
```

For folder membership, the folder icon's `label` is the folder name; its
`value` (or the icons inside, after `tap(folder)`) reveal the contents.
