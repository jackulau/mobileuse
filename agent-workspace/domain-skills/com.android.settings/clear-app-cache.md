# Android Settings — clear an app's cache or storage

Package: `com.android.settings`. Field-tested on Pixel + Android 14.

Two destructive levels:

- **Clear cache** — wipes the app's cache directory. Safe; the app keeps
  account / preferences. Common cleanup for chat apps (WhatsApp, Telegram)
  where the cache balloons with media thumbnails.
- **Clear storage** — full data wipe, including login. Equivalent to a
  fresh install minus the redownload.

```python
# Land on the app's info page. The cleanest entry is the package launcher:
package = "com.whatsapp"
appium("mobile: shell", command="am", args=[
    "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS",
    "-d", f"package:{package}"
])
wait(1.8)

# Some Appium configs disable mobile: shell. Fallback via Settings UI:
# Settings → Apps → See all apps → WhatsApp.
```

If `mobile: shell` is unavailable on your Appium server (it requires
`--relaxed-security` or `--allow-insecure adb_shell`), use:

```python
appium("mobile: startActivity",
       package="com.android.settings",
       activity="com.android.settings.applications.InstalledAppDetails",
       optionalIntentArguments="-d package:com.whatsapp")
wait(1.8)
```

## Clear cache (safe)

```python
tap(find(text="Storage & cache")); wait(1.0)
btn = find(text="Clear cache") or find(content_desc="Clear cache")
if btn is None:
    raise RuntimeError("Clear cache button missing — app may not expose cache")
tap(btn); wait(0.8)
# No confirmation for cache on modern Android.
```

## Clear storage (full wipe)

```python
btn = find(text="Clear storage") or find(text="Clear data")
if btn is None:
    raise RuntimeError("Clear storage button missing")
tap(btn); wait(0.6)
# Confirmation: "Delete app data? All this app's data... will be deleted permanently."
confirm = find(text="OK") or find(text="Delete")
if confirm: tap(confirm); wait(1.5)
```

## Bulk: clear cache for every app over N MB

```python
import re

heavy = []
appium("mobile: startActivity",
       package="com.android.settings",
       activity="com.android.settings.applications.ManageApplications")
wait(2.0)

# Sort by size descending: ellipsis → Sort by → Size.
menu = find(content_desc="More options") or find(content_desc="More")
if menu: tap(menu); wait(0.4)
sb = find(text="Sort by size") or find(text="Sort by")
if sb:
    tap(sb); wait(0.4)
    s = find(text="Size")
    if s: tap(s); wait(0.6)

for _ in range(40):
    invalidate_tree_cache()
    for el in ui_tree(visible_only=True):
        if el.get("resource_id", "").endswith(":id/summary"):
            m = re.search(r"([\d.]+)\s*MB", el.get("text", ""))
            if m and float(m.group(1)) > 100:
                # Parent row contains the title
                heavy.append(el)
    scroll(direction="down"); wait(0.3)

# For each heavy app, drill in and clear cache.
# (Walk `heavy` indices, re-find each row by label to refresh element ids.)
```

## Edge cases

- **Some apps refuse to clear cache** (banking, Authenticator). Their
  buttons exist but `Clear cache` is greyed (`enabled == False`). Skip.
- **Clearing Maps cache logs you out of Maps.** Document this for users
  before running a bulk Clear cache pass.
- **System apps' storage is account-bound** and may not be clearable
  without a factory reset.
