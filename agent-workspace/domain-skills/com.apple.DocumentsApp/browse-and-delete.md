# Files app — browse and delete files / folders

Bundle id: `com.apple.DocumentsApp`. Field-tested on iOS 18.3.

Two top-level locations:

- **On My iPhone** — local-only storage. Contains Downloads (Safari, Mail
  attachments), per-app folders (Pages, Numbers, third-party apps that
  publish a Files provider), and anything the user dragged in.
- **iCloud Drive** — cloud-mirrored. Deletes propagate to other devices.

Both expose a uniform Browse → Select → Delete flow.

```python
appium("mobile: terminateApp", bundleId="com.apple.DocumentsApp")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.DocumentsApp")
wait(2.0)

# Force the Browse tab (the app may resume in Recents).
browse = find(label="Browse", type="XCUIElementTypeButton")
if browse: tap(browse); wait(0.6)

# Navigate to a top-level location.
def open_location(name):
    cell = find(label=name, type="XCUIElementTypeCell")
    if cell is None:
        # Expand "Locations" section if collapsed.
        loc = find(label="Locations", type="XCUIElementTypeButton")
        if loc: tap(loc); wait(0.4)
        cell = find(label=name, type="XCUIElementTypeCell")
    if cell is None:
        raise RuntimeError(f"location {name!r} not present")
    tap(cell); wait(1.2)

open_location("On My iPhone")
```

## List items in a folder

```python
def list_items():
    items = []
    invalidate_tree_cache()
    for el in ui_tree(visible_only=True):
        if el.get("type") != "XCUIElementTypeCell":
            continue
        lbl = el.get("label", "")
        if lbl and not lbl.startswith(("Sort", "Display", "More")):
            items.append({"label": lbl, "el": el})
    return items
```

## Enter a subfolder

```python
items = list_items()
target = next((i for i in items if i["label"].startswith("Downloads")), None)
if target:
    tap(target["el"]); wait(1.0)
```

## Select + delete

```python
# Menu button (top-right ellipsis) → Select.
menu = find(label="More", type="XCUIElementTypeButton") or \
       find(name="More", type="XCUIElementTypeButton")
if menu: tap(menu); wait(0.4)
tap(find(label="Select", type="XCUIElementTypeButton")); wait(0.4)

# Tap each item we want to delete.
to_delete = ["report.pdf", "screenshot 2024-01-01.png"]
for name in to_delete:
    el = find(label=name, type="XCUIElementTypeCell")
    if el: tap(el); wait(0.2)

# Bottom toolbar shows Share / Duplicate / Move / Delete. Tap Delete.
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(0.8)

# No confirmation alert on local On My iPhone deletions in iOS 18 — files go
# straight to Recently Deleted within the Files app. Check for one anyway.
confirmed = False
for _ in range(4):
    alert_btn = find(label="Delete", type="XCUIElementTypeButton")
    if alert_btn and alert_btn["cy"] > window_size()["height"] * 0.6:
        # The bottom-of-screen Delete button is the toolbar one; an alert
        # button would sit higher. Skip if same.
        break
    wait(0.3)
```

## Delete a whole folder

Same flow — Select, tap the folder, Delete. The folder and its contents go
to Recently Deleted together.

## Common pitfalls

- **Read-only providers.** Files installed by some apps (e.g. iCloud Drive
  shared folders you don't own) won't expose a Delete button. Detect by
  the toolbar button being disabled (`enabled == False`).
- **Sort order changes layout.** The Display sub-menu lets the user toggle
  grid / list / sort by name / date. The tree element types stay the same
  (`XCUIElementTypeCell`), so `label` lookups still work.
- **Recently Deleted in Files** is separate from Photos'. See
  `empty-files-recently-deleted.md`.
