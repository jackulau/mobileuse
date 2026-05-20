# Photos — delete an entire album (Screenshots, Selfies, Duplicates)

Bundle id: `com.apple.mobileslideshow`. Field-tested on iOS 18.3.

iOS auto-curates "Media Types" albums (Screenshots, Selfies, Videos, Live
Photos, Bursts, Time Lapses) and a "Utilities → Duplicates" album. Each is
a great target for bulk cleanup.

```python
appium("mobile: launchApp", bundleId="com.apple.mobileslideshow")
wait(2.0)
tap(find(label="Albums", type="XCUIElementTypeButton")); wait(0.6)
```

## Screenshots / Selfies / Videos

Each lives under "Media Types" — scroll down past My Albums and Shared
Albums to find it.

```python
def open_media_type(name):
    for _ in range(10):
        cell = find(label=name, type="XCUIElementTypeCell") or \
               find(label=name, type="XCUIElementTypeButton")
        if cell:
            tap(cell); wait(1.2); return
        scroll_by(dy=-400, velocity=400); wait(0.3)
    raise RuntimeError(f"{name!r} album not found")

open_media_type("Screenshots")

# Now we're inside the album — same select+delete flow as bulk-delete-photos.md.
tap(find(label="Select", type="XCUIElementTypeButton")); wait(0.4)

# Select All — iOS shows it at the top once Select mode is active.
sa = find(label="Select All", type="XCUIElementTypeButton")
if sa:
    tap(sa); wait(0.4)
else:
    # Fall back to a drag from top-left to bottom-right.
    photos = find_all(type="XCUIElementTypeImage")
    if photos:
        appium("mobile: dragFromToForDuration",
               fromX=photos[0]["cx"], fromY=photos[0]["cy"],
               toX=photos[-1]["cx"], toY=photos[-1]["cy"],
               duration=1.2)
        wait(0.6)

trash = find(label="Delete", type="XCUIElementTypeButton") or \
        find(name="trash", type="XCUIElementTypeButton")
tap(trash); wait(0.5)
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(2.0)
```

## Duplicates

`Albums → Utilities → Duplicates`. Each duplicate set has a Merge button.

```python
open_media_type("Duplicates")

# Each row shows a horizontal pair (or trio) with a "Merge" button at the
# right edge. Iterate row-by-row.
merges = find_all(label="Merge", type="XCUIElementTypeButton")
for m in merges:
    tap(m); wait(0.4)
    # Confirmation: "Merge N Exact Copies?" → Merge N Items / Cancel.
    confirm = find(label="Merge", type="XCUIElementTypeButton")
    if confirm: tap(confirm); wait(0.4)
```

Merge keeps the highest-quality version and moves the others to Recently
Deleted. Empty Recently Deleted (`empty-recently-deleted.md`) to free the
space.

## Empty an entire user album

User-created albums (My Albums → "Trip 2024" etc.) can be deleted whole.

```python
tap(find(label="Albums", type="XCUIElementTypeButton")); wait(0.6)
edit = find(label="Edit", type="XCUIElementTypeButton")
if edit is None:
    # Some iOS versions hide Edit behind a "See All" page.
    sa = find(label="See All", type="XCUIElementTypeButton")
    if sa: tap(sa); wait(0.6)
    edit = find(label="Edit", type="XCUIElementTypeButton")
tap(edit); wait(0.5)

# Album thumbnails now sport a red delete badge.
target = find(label="Trip 2024", type="XCUIElementTypeCell")
badge = find(name="delete", type="XCUIElementTypeButton")  # within `target`
if badge: tap(badge); wait(0.4)
tap(find(label="Delete Album", type="XCUIElementTypeButton")); wait(1.0)
tap(find(label="Done", type="XCUIElementTypeButton")); wait(0.4)
```

Note that deleting an album in the Photos app does NOT delete the photos
themselves — they remain in Library. Use `bulk-delete-photos.md` for that.

## Common pitfalls

- **Empty album shows no Select button** — checking for it is a cheap probe
  that the album has content.
- **Hidden album** — Settings → Photos → Show Hidden Album → On. The
  Hidden album lives under Utilities and is Face ID gated like Recently
  Deleted.
- **iCloud-shared album** items can only be removed by the original sharer;
  the Delete button is hidden for others.
