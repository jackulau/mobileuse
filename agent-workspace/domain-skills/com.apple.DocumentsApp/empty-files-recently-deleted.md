# Files app — empty Recently Deleted (separate from Photos)

Bundle id: `com.apple.DocumentsApp`. Field-tested on iOS 18.3.

The Files app has its own 30-day Recently Deleted bucket for files. It is
distinct from the Photos app's Recently Deleted (which is image/video only)
— deleting a PDF in Files puts it here.

```python
appium("mobile: terminateApp", bundleId="com.apple.DocumentsApp")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.DocumentsApp")
wait(2.0)
tap(find(label="Browse", type="XCUIElementTypeButton")); wait(0.6)

# Scroll to find Recently Deleted (lives below Tags, near the bottom).
for _ in range(10):
    rd = find(label="Recently Deleted", type="XCUIElementTypeCell")
    if rd: break
    scroll_by(dy=-400, velocity=400); wait(0.3)
if rd is None:
    raise RuntimeError("Recently Deleted location missing — verify iOS version")
tap(rd); wait(1.5)
```

## Empty all

```python
# Ellipsis -> Select.
menu = find(name="More", type="XCUIElementTypeButton")
if menu: tap(menu); wait(0.4)
tap(find(label="Select", type="XCUIElementTypeButton")); wait(0.3)

# Ellipsis -> Delete All (appears after entering Select mode).
menu = find(name="More", type="XCUIElementTypeButton")
if menu: tap(menu); wait(0.4)
da = find(label="Delete All", type="XCUIElementTypeButton")
if da:
    tap(da); wait(0.6)
else:
    # Older iOS: Select All -> Delete.
    tap(find(label="Select All", type="XCUIElementTypeButton")); wait(0.4)
    tap(find(label="Delete", type="XCUIElementTypeButton")); wait(0.6)

# Confirmation alert: "Delete N Items? This will permanently remove them."
confirm = find(label="Delete", type="XCUIElementTypeButton")
tap(confirm); wait(2.0)
```

## Targeted purge by age

iCloud Drive's Recently Deleted sorts by date — purge only items older than
N days:

```python
import re, datetime

cutoff = datetime.date.today() - datetime.timedelta(days=7)
items = []
for el in find_all(type="XCUIElementTypeCell"):
    lbl = el.get("label", "")
    # iOS labels include the deletion date: "report.pdf, 23 days remaining"
    m = re.search(r"(\d+)\s+days remaining", lbl)
    if m and 30 - int(m.group(1)) >= 7:
        items.append(el)

tap(find(label="Select", type="XCUIElementTypeButton")); wait(0.3)
for el in items:
    tap(el); wait(0.1)
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(0.6)
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(2.0)
```

## Verification

```python
empty = find(label="No Items", type="XCUIElementTypeStaticText")
print("recently deleted empty:", empty is not None)
```
