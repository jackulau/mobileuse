# Google Photos — empty Bin

Package: `com.google.android.apps.photos`. Field-tested on Pixel +
Android 14.

The Bin is the 60-day retention bucket. Items here still consume device
storage when "Backup off" or when Photos has cached cloud items locally —
emptying it is what actually frees space.

```python
appium("mobile: startActivity",
       package="com.google.android.apps.photos",
       activity=".home.HomeActivity")
wait(2.5)

# Bottom tab → Library.
tap(find(text="Library") or find(content_desc="Library")); wait(0.6)

# Library cards: Favorites / Utilities / Bin / Archive / per-album cards.
bin_card = find(text="Bin") or find(content_desc="Bin")
if bin_card is None:
    # Some versions label it "Trash". Try both.
    bin_card = find(text="Trash")
if bin_card is None:
    raise RuntimeError("Bin/Trash card not found in Library")
tap(bin_card); wait(1.5)
```

## Empty bin (top-right overflow)

```python
menu = find(content_desc="More options") or find(content_desc="More")
if menu is None:
    raise RuntimeError("overflow menu not present")
tap(menu); wait(0.4)

empty = find(text="Empty bin") or find(text="Empty trash")
if empty is None:
    raise RuntimeError("Empty bin not in menu — bin may already be empty")
tap(empty); wait(0.5)

# Confirmation: "Permanently delete all items in Bin?" → Delete
delete = find(text="Delete") or find(text="Permanently delete") or \
         find(text="Empty bin")
if delete:
    tap(delete); wait(2.5)
```

## Selective purge

```python
long_press(find_all(content_desc="Photo")[0]["cx"],
           find_all(content_desc="Photo")[0]["cy"], duration=0.6)
wait(0.5)
# Select remaining items via tap or drag (see bulk-delete.md).

trash = find(content_desc="Delete") or find(text="Delete")
tap(trash); wait(0.5)
# Confirmation: "Permanently delete N items from Bin?" → Delete
tap(find(text="Delete")); wait(2.0)
```

## Verification

```python
wait(1.0)
empty = find(text="No items in your Bin") or find(text="Nothing in Bin")
assert empty is not None, "Bin still has items after Empty"
```

## Notes

- **Backup-on libraries** will see the Bin re-populate from cloud after a
  sync; emptying the on-device Bin doesn't delete from the cloud Bin. To
  empty cloud Bin too, sign in to photos.google.com.
- **Locked Folder Bin** is separate and PIN-protected.
- **Quota-exhausted accounts** can show a banner above the Bin asking the
  user to upgrade — does not block the Empty action.
