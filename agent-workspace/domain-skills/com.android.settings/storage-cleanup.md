# Android Settings — Storage cleanup

Package: `com.android.settings`. Field-tested on Pixel + Android 14.

Two complementary cleanup surfaces:

1. **Settings → Storage** — shows the per-category storage pie (Apps, Media,
   Documents, Games, System) and links to a "Free up space" wizard that
   hands off to **Files by Google** (`com.google.android.apps.nbu.files`) if
   installed.
2. **Files by Google → Clean tab** — junk files, large files, duplicates,
   downloaded files. See `com.google.android.apps.nbu.files/cleanup.md` for
   the in-app flow.

## Open the Storage dashboard

```python
appium("mobile: startActivity",
       package="com.android.settings",
       activity="com.android.settings.Settings$StorageDashboardActivity")
wait(2.0)
```

If the explicit activity name fails on your OEM (Samsung uses
`com.samsung.android.themestore...` paths), fall back to:

```python
appium("mobile: startActivity", package="com.android.settings",
       activity=".Settings")
wait(1.5)
# Then navigate via taps:
tap(find(text="Storage")); wait(1.5)
```

## Read the storage summary

```python
invalidate_tree_cache()
for el in ui_tree(visible_only=True):
    txt = el.get("text", "")
    if "GB" in txt and ("used" in txt.lower() or "free" in txt.lower()):
        print(txt)

# The header element typically shows "X.X GB used of Y.Y GB" or two separate
# rows for used and free.
```

## Free up space wizard

```python
free = find(text="Free up space") or find(content_desc="Free up space")
if free is None:
    # Alternative label on older Android: "Manage storage"
    free = find(text="Manage storage")
if free is None:
    raise RuntimeError("Free up space button not present — Files by Google may not be installed")
tap(free); wait(2.5)

# Hands off to Files by Google. The first screen shows recommendation cards:
# Junk files / Downloaded files / App data / Large files / Backed up photos.
# See com.google.android.apps.nbu.files/cleanup.md for that flow.
```

## Direct per-category cleanup

Without Files by Google, drill into a category and free space app-by-app.

```python
# From Settings → Storage:
tap(find(text="Apps")); wait(1.5)

# A list of installed apps, sorted by storage. Pick the heaviest:
heavy = find_all(resource_id="android:id/title")[0]
print("heaviest app:", heavy["text"])
tap(heavy); wait(1.5)

# App info → Storage & cache.
tap(find(text="Storage & cache")); wait(1.0)

# Two buttons: Clear storage (full wipe) / Clear cache (cache only).
tap(find(text="Clear cache")); wait(0.6)
```

## Cleanup of Photos backups

If Google Photos has uploaded your library, you can free the on-device
copies safely:

```python
# In Files by Google's Clean tab the "Backed up photos & videos" card is
# the safest large cleanup — see com.google.android.apps.nbu.files/cleanup.md.
```

## Edge cases

- **Encrypted user data.** On corporate-managed devices, the Free up space
  wizard may be disabled by policy. Detect: button greyed, `enabled == False`.
- **Per-user storage.** Multi-user Android shows the current user's storage
  only. To clean another user's data, switch user first (Settings → System
  → Multiple users).
- **External SD card / USB OTG.** Storage dashboard lists them at the
  bottom; tapping one opens its own per-category breakdown.
