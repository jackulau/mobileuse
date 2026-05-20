# Settings → General → iPhone Storage

Bundle id: `com.apple.Preferences`. Field-tested on iOS 18.3.

The iPhone Storage screen is the most reliable place to:

- Read a per-app size breakdown (and sort by size visually).
- Offload an app (uninstall the binary, keep its documents and data).
- Delete an app (uninstall binary + data).
- Trigger storage recommendations (Review Large Attachments, Auto Delete Old
  Conversations, etc.).

## Open the screen

```python
appium("mobile: terminateApp", bundleId="com.apple.Preferences")
wait(0.6)
appium("mobile: launchApp", bundleId="com.apple.Preferences")
wait(2.0)

def tap_row(label):
    cell = find(label=label, type="XCUIElementTypeCell")
    if cell is None:
        scroll_by(dy=-300, velocity=400); wait(0.4)
        cell = find(label=label, type="XCUIElementTypeCell")
    if cell is None:
        raise RuntimeError(f"Couldn't find {label!r} row")
    tap(cell); wait(1.8)

tap_row("General")
tap_row("iPhone Storage")

# iPhone Storage computes sizes asynchronously — give the page a moment to
# populate. Three seconds is the empirical floor on a real device.
wait(3.0)
```

## Scrape per-app sizes

Each app row's `label` follows the pattern `"<App Name>, <size>"`. Sometimes
"Last Used" appears in the value or the label trailer; use the comma split.

```python
def storage_rows():
    invalidate_tree_cache()
    out = []
    for el in ui_tree(visible_only=True):
        if el.get("type") != "XCUIElementTypeCell":
            continue
        label = el.get("label", "")
        if not label or any(skip in label for skip in (
            "Recommendations", "iCloud", "Used", "iPhone Storage"
        )):
            continue
        parts = [p.strip() for p in label.split(",")]
        if len(parts) >= 2 and any(u in parts[-1] for u in ("KB", "MB", "GB")):
            out.append({"label": parts[0], "size": parts[-1], "cell": el})
    return out

# Pull everything by scrolling until the bottom doesn't change.
seen = {}
for _ in range(40):
    for row in storage_rows():
        seen.setdefault(row["label"], row["size"])
    before = len(seen)
    scroll_by(dy=-500, velocity=500); wait(0.4)
    if len(seen) == before:
        break

# Sorted by size descending — note sizes here are display strings ("1.5 GB"),
# parse if you need to compare.
import re
def _bytes(sz):
    m = re.match(r"([\d.]+)\s*(KB|MB|GB)", sz)
    if not m: return 0
    v, u = float(m.group(1)), m.group(2)
    return int(v * {"KB": 1024, "MB": 1024**2, "GB": 1024**3}[u])

ranked = sorted(seen.items(), key=lambda kv: _bytes(kv[1]), reverse=True)
for label, size in ranked[:10]:
    print(label, size)
```

## Offload an app

```python
tap_row("Instagram")          # opens the per-app detail page
wait(1.5)
offload = find(label="Offload App", type="XCUIElementTypeButton")
if offload is None:
    raise RuntimeError("Offload affordance missing — not an offloadable app")
tap(offload); wait(0.6)
# Confirm dialog: "Offloading 'Instagram' will free up storage..."
tap(find(label="Offload App", type="XCUIElementTypeButton")); wait(2.5)
```

After offload, the app icon stays on the home screen with a small cloud
glyph; tapping reinstalls from the App Store.

## Delete an app (with data)

Same flow as Offload but tap the "Delete App" button. The cross-platform
helper `agent_helpers.uninstall_app("Instagram")` performs exactly this
sequence and is the recommended entry point.

```python
tap_row("Instagram")
wait(1.5)
tap(find(label="Delete App", type="XCUIElementTypeButton")); wait(0.6)
tap(find(label="Delete App", type="XCUIElementTypeButton")); wait(2.0)  # confirm
```

## Storage recommendations

Above the app list, iOS surfaces 2-5 recommendation rows. The set varies
("Review Large Attachments", "Auto Delete Old Conversations", "Offload
Unused Apps", "Optimize Photos"). They appear inside cells whose label starts
with the recommendation title and whose value is the projected savings.

```python
recs = [el for el in ui_tree(visible_only=True)
        if el.get("type") == "XCUIElementTypeCell"
        and any(t in el.get("label", "") for t in (
            "Review", "Offload Unused", "Auto Delete", "Optimize"
        ))]
for rec in recs:
    print("recommendation:", rec["label"])
```

Tap a recommendation row to drill in; the next screen has an Enable button.

## Edge cases

- **Storage card not ready yet.** If you tap an app row within ~2s of
  landing on iPhone Storage, the detail page may show "Calculating…" instead
  of buttons. Wait an extra 2-3s and re-fetch the tree.
- **Stock apps.** Mail, Safari, Photos: no Delete App button, only the
  per-app data controls.
- **Apple-managed apps inside MDM-supervised devices.** Delete App may be
  disabled by MDM policy. The button is greyed; detect via `enabled == False`
  on the element.
