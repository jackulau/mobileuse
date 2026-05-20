# Photos — bulk delete

Bundle id: `com.apple.mobileslideshow`. Field-tested on iOS 18.3.

Two modes — drag-select across thumbnails (fast for visually contiguous
ranges) or tap-select one at a time (slower but precise). Both end with the
Delete button at the bottom-right and a confirmation alert.

```python
appium("mobile: terminateApp", bundleId="com.apple.mobileslideshow")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.mobileslideshow")
wait(2.0)

# Landing tab: Library / Years / Months / All Photos. Force All Photos for
# predictable thumbnail layout.
tab = find(label="Library", type="XCUIElementTypeButton")
if tab: tap(tab); wait(0.6)
all_btn = find(label="All Photos", type="XCUIElementTypeButton")
if all_btn: tap(all_btn); wait(0.6)
```

## Enter Select mode

```python
select = find(label="Select", type="XCUIElementTypeButton")
if select is None:
    raise RuntimeError("Select button missing — possibly inside an album already")
tap(select); wait(0.4)
```

## Tap-select

Thumbnails are `XCUIElementTypeImage` cells. Their `name` follows
`Photo, MMMM DD, YYYY, HH:MM:SS AM`. Use `find_all` to enumerate.

```python
photos = find_all(type="XCUIElementTypeImage")[:20]   # first 20 thumbs
for p in photos:
    tap(p); wait(0.1)
# Bottom toolbar updates with "<n> Selected".
```

## Drag-select (much faster on big libraries)

```python
photos = find_all(type="XCUIElementTypeImage")
first, last = photos[0], photos[40]
appium("mobile: dragFromToForDuration",
       fromX=first["cx"], fromY=first["cy"],
       toX=last["cx"], toY=last["cy"],
       duration=1.0)
wait(0.6)
```

iOS interprets the swipe as a multi-select range. Tested up to 200 photos in
a single drag.

## Delete

```python
trash = find(label="Delete", type="XCUIElementTypeButton") or \
        find(name="trash", type="XCUIElementTypeButton")
if trash is None:
    raise RuntimeError("Delete (trash) button not on toolbar")
tap(trash); wait(0.6)

# Confirmation alert: "Delete N Items?" → Delete N Items / Cancel.
confirm = find(label="Delete", type="XCUIElementTypeButton")  # picks the destructive red one
if confirm is None:
    # iOS sometimes uses "Delete From This iPhone" when iCloud Photos is on.
    for lbl in ("Delete From This iPhone", "Delete N Items", "Remove from Album"):
        confirm = find(label=lbl, type="XCUIElementTypeButton")
        if confirm: break
if confirm is None:
    raise RuntimeError("Delete confirmation alert missing")
tap(confirm); wait(2.0)
```

## iCloud Photos caveat

When iCloud Photos sync is on, deleting from Library is mirrored across
devices and moves the items into **Recently Deleted** for 30 days. Empty it
explicitly — see `empty-recently-deleted.md`.

## Cross-platform shortcut

`agent_helpers.bulk_select(items, deletion_button="Delete")` wraps the
generic Select → tap-each → Delete sequence. Use the manual recipe above
when you need drag-select or a non-default confirmation button.

## Verification

```python
wait(1.5)
remaining = find_all(type="XCUIElementTypeImage")
print(f"thumbnails visible after delete: {len(remaining)}")
# Check that the count dropped by approximately the number you selected.
```
