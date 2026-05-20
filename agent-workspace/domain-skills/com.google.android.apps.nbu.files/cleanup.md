# Files by Google — Clean tab

Package: `com.google.android.apps.nbu.files`. Field-tested on Pixel +
Android 14.

Files by Google is Pixel's default file manager. The Clean tab surfaces:

- **Junk files** — caches, residual files.
- **Downloaded files** — anything in /Download/.
- **Large files** — > 10 MB.
- **Duplicate files** — exact-match by hash.
- **Backed up photos & videos** — already in Google Photos cloud; safe to
  delete on-device.
- **Unused apps** — heuristic on launch frequency.

```python
appium("mobile: startActivity",
       package="com.google.android.apps.nbu.files",
       activity=".home.HomeActivity")
wait(2.5)

# Two bottom tabs: Clean (broom) / Browse (folder). Tap Clean.
clean = find(text="Clean") or find(content_desc="Clean")
if clean is None:
    raise RuntimeError("Clean tab missing — Files by Google version may be outdated")
tap(clean); wait(1.5)
```

## Card-by-card cleanup

Each card has a "See files" / "Free up N MB" affordance.

### Junk files

```python
junk = find(text="Junk files")
if junk:
    # Drill into the parent card by tapping nearby; or just tap the See button.
    # Pixel's Clean cards put the action button as content_desc="Free up 250 MB"
    btn = find(content_desc="Free up") or find(text="Free up")
    if btn: tap(btn); wait(1.5)
    # Confirmation: "Clear 250 MB? You'll free space..." → Clear
    confirm = find(text="Clear") or find(text="Continue")
    if confirm: tap(confirm); wait(2.5)
```

### Downloaded files

```python
dl = find(text="Downloaded files")
if dl:
    # See files opens a list; select all + delete.
    tap(find(text="See files")); wait(1.5)
    # Menu (top-right) → Select all
    menu = find(content_desc="More options")
    if menu: tap(menu); wait(0.4)
    sa = find(text="Select all")
    if sa: tap(sa); wait(0.4)
    # Delete from bottom toolbar.
    tap(find(text="Delete") or find(content_desc="Delete")); wait(0.5)
    tap(find(text="Move 12 files to trash") or find(text="Move to trash")); wait(2.0)
```

### Large files

```python
lf = find(text="Large files")
if lf:
    tap(find(text="See files")); wait(1.5)
    # Each row is a file with checkbox; sort by size desc by default.
    rows = find_all(resource_id="com.google.android.apps.nbu.files:id/file_name")
    for r in rows[:10]:  # top 10 largest
        # Tap to enter select; subsequent taps toggle.
        if r["enabled"]:
            tap(r); wait(0.1)
    tap(find(text="Delete")); wait(0.5)
    tap(find(text="Move to trash") or find(text="Delete")); wait(2.0)
```

### Duplicates

```python
dup = find(text="Duplicate files")
if dup:
    tap(find(text="See files")); wait(1.5)
    # Each duplicate set has a "Select" button keeping one and removing rest.
    selects = find_all(text="Select")
    for s in selects:
        tap(s); wait(0.2)
    tap(find(text="Confirm and free up")); wait(0.6)
    tap(find(text="Move N files to trash")); wait(2.0)
```

### Backed up photos & videos

```python
bp = find(text="Backed up photos & videos")
if bp:
    tap(find(text="Free up")); wait(1.5)
    # Confirmation card explains "Items will remain in Google Photos cloud."
    tap(find(text="Free up")); wait(2.5)
```

## Trash folder

Files by Google soft-deletes to /Trash/. To permanently free the space,
empty the trash:

```python
# Menu (top-right hamburger) -> Trash
hb = find(content_desc="Show roots") or find(content_desc="Open navigation drawer")
if hb: tap(hb); wait(0.4)
tap(find(text="Trash")); wait(1.0)
menu = find(content_desc="More options"); tap(menu); wait(0.4)
tap(find(text="Empty trash")); wait(0.4)
tap(find(text="Permanently delete")); wait(2.0)
```

## Verification

```python
# Re-open the Clean tab; the previously freed card should be gone or zero.
press_back(); wait(0.4)
press_back(); wait(0.4)
clean = find(text="Clean"); tap(clean); wait(1.0)
```

## Edge cases

- **Files by Google not installed.** Some non-Pixel devices ship a different
  file manager (Samsung My Files, MIUI Files). Skill is N/A; fall back to
  `com.android.settings/storage-cleanup.md`.
- **Junk file warning.** The Clean tab's junk-file detector occasionally
  flags app caches still in use. Trust the app's confirmation; if it asks
  again with a different total, the previous round succeeded partially.
- **WhatsApp / Telegram caches** are visible under per-app Storage but not
  always under Junk. Clear them via
  `com.android.settings/clear-app-cache.md`.
