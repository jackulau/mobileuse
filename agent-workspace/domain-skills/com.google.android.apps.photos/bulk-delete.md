# Google Photos — bulk delete

Package: `com.google.android.apps.photos`. Field-tested on Pixel +
Android 14, Photos app v6.84+.

Bulk select happens in two equivalent surfaces:

- **Photos tab** (chronological grid) — long-press a thumbnail to enter
  Select, then tap (or drag-select) more.
- **Library → Folders → (Camera / Screenshots / Downloads / etc.)** — same
  flow scoped to one folder.

```python
appium("mobile: startActivity",
       package="com.google.android.apps.photos",
       activity=".home.HomeActivity")
wait(2.5)

# Bottom tab → Photos.
tap(find(text="Photos") or find(content_desc="Photos")); wait(0.6)
```

## Long-press a thumbnail

```python
# Thumbnails are images with content_desc like "Photo taken on Jan 1, 2024".
thumbs = find_all(content_desc="Photo")  # `content_desc` startswith match
if not thumbs:
    # Fallback: find by image class.
    thumbs = [el for el in find_all() if "ImageView" in el.get("type", "")
              and el.get("content_desc", "").startswith("Photo")]

if not thumbs:
    raise RuntimeError("no photo thumbnails found in tree")

long_press(thumbs[0]["cx"], thumbs[0]["cy"], duration=0.6); wait(0.5)

# Select mode is active. Tap more to add.
for t in thumbs[1:10]:
    tap(t); wait(0.1)
```

## Drag-select

```python
first, last = thumbs[0], thumbs[15]
long_press(first["cx"], first["cy"], duration=0.6); wait(0.4)
appium("mobile: dragGesture",
       startX=first["cx"], startY=first["cy"],
       endX=last["cx"], endY=last["cy"],
       speed=400)
wait(0.6)
```

## Move to Bin

```python
trash = find(content_desc="Move to trash") or \
        find(text="Delete") or \
        find(content_desc="Delete")
if trash is None:
    raise RuntimeError("Delete / Move to trash not found in bottom bar")
tap(trash); wait(0.6)

# Confirmation: "Move 10 items to bin? They'll be deleted forever after 60
# days." → Move to trash
for label in ("Move to trash", "Move to bin", "Move to Bin", "Delete"):
    c = find(text=label)
    if c: tap(c); break
wait(2.0)
```

## Sign-out caveat

If the user is signed out of Google Photos, deletes only affect the local
device copy. Sign-in state is visible via the top-right account avatar; if
it shows the generic "Sign in" placeholder, surface a warning before
deleting.

## Verification

```python
wait(1.0)
# Bottom bar should disappear; toolbar reverts to default.
assert find(text="Photos") is not None, "Select mode did not exit"
```

## Edge cases

- **Pixel "Memories" cards** are not deletable from this view; long-pressing
  them does nothing meaningful.
- **Locked Folder** items are not visible from the Photos tab; navigate
  Library → Utilities → Locked Folder (PIN required).
- **Bin is separate.** Items deleted here go to Library → Bin and still
  occupy device storage for ~60 days. To free space, empty the Bin — see
  `empty-bin.md`.
