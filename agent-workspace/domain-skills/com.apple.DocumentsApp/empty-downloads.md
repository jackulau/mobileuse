# Files app — empty Downloads

Bundle id: `com.apple.DocumentsApp`. Field-tested on iOS 18.3.

Safari, Mail attachments, and AirDropped files default to **On My iPhone →
Downloads**. It tends to accumulate quietly. Clean it with:

```python
appium("mobile: terminateApp", bundleId="com.apple.DocumentsApp")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.DocumentsApp")
wait(2.0)

# Browse tab.
tap(find(label="Browse", type="XCUIElementTypeButton")); wait(0.6)

# Open On My iPhone -> Downloads.
def open_cell(label):
    cell = find(label=label, type="XCUIElementTypeCell")
    if cell is None:
        scroll_by(dy=-300, velocity=400); wait(0.3)
        cell = find(label=label, type="XCUIElementTypeCell")
    if cell is None:
        raise RuntimeError(f"{label!r} cell not present")
    tap(cell); wait(1.0)

open_cell("On My iPhone")
open_cell("Downloads")
```

## Select all + delete

```python
# Top-right ellipsis → Select.
ellipsis = find(name="More", type="XCUIElementTypeButton") or \
           find(label="More", type="XCUIElementTypeButton")
if ellipsis: tap(ellipsis); wait(0.4)
sel = find(label="Select", type="XCUIElementTypeButton")
tap(sel); wait(0.4)

# After entering Select mode, the same ellipsis exposes "Select All".
ellipsis = find(name="More", type="XCUIElementTypeButton")
if ellipsis: tap(ellipsis); wait(0.4)
sa = find(label="Select All", type="XCUIElementTypeButton")
if sa:
    tap(sa); wait(0.4)
else:
    # Fall back: tap every item in the tree.
    for el in find_all(type="XCUIElementTypeCell"):
        if el.get("label"):
            tap(el); wait(0.1)

tap(find(label="Delete", type="XCUIElementTypeButton")); wait(1.0)
```

## Empty by extension

```python
# Inside Downloads, drill into a specific cell type — say all .ipa files.
items = [el for el in find_all(type="XCUIElementTypeCell")
         if el.get("label", "").lower().endswith(".ipa")]
tap(find(label="Select", type="XCUIElementTypeButton")); wait(0.4)
for el in items:
    tap(el); wait(0.15)
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(0.8)
```

## Done indicator

```python
wait(1.5)
empty = find(label="No Items", type="XCUIElementTypeStaticText") or \
        find(label="No Documents", type="XCUIElementTypeStaticText")
print("downloads empty:", empty is not None)
```

If the folder isn't fully empty (some readonly items remained), the screen
won't show "No Items" — drill in to inspect.

## Free the space

Local On My iPhone deletes go to **Recently Deleted** inside Files (not
Photos). Empty it next — see `empty-files-recently-deleted.md`. Without
that step, the freed bytes are still reserved.
