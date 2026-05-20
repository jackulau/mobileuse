# Photos — empty Recently Deleted

Bundle id: `com.apple.mobileslideshow`. Field-tested on iOS 18.3.

Recently Deleted is the 30-day retention bucket. Items here still occupy
storage; emptying it is what actually frees space. The album is **Face ID
or PIN protected** by default — the script must wait for biometric auth.

```python
appium("mobile: terminateApp", bundleId="com.apple.mobileslideshow")
wait(0.5)
appium("mobile: launchApp", bundleId="com.apple.mobileslideshow")
wait(2.0)

# Bottom tab → Albums.
albums = find(label="Albums", type="XCUIElementTypeButton")
if albums is None:
    raise RuntimeError("Albums tab missing — check iOS version")
tap(albums); wait(0.8)

# Scroll to the Utilities section and tap Recently Deleted.
for _ in range(8):
    rd = find(label="Recently Deleted", type="XCUIElementTypeCell") or \
         find(label="Recently Deleted", type="XCUIElementTypeButton")
    if rd: break
    scroll_by(dy=-400, velocity=400); wait(0.3)
if rd is None:
    raise RuntimeError("Recently Deleted album not found")
tap(rd); wait(1.5)
```

## Face ID / Touch ID / PIN gate

```python
# iOS pops a system biometric prompt. There's no element to tap — the user
# must look at the device (Face ID) or touch the sensor. If a PIN keypad
# appears instead, we abort: the harness cannot type a passcode the user
# hasn't shared.
wait(2.5)  # let Face ID try
if find(label="View Album", type="XCUIElementTypeButton"):
    tap(find(label="View Album", type="XCUIElementTypeButton")); wait(0.6)

# Detect PIN failure: a numeric keypad implies Face ID failed or is off.
if find(label="1", type="XCUIElementTypeKey"):
    # Cancel the keypad and bail.
    cancel = find(label="Cancel", type="XCUIElementTypeButton")
    if cancel: tap(cancel)
    raise RuntimeError("Recently Deleted requires PIN that the harness does not have. "
                       "Keep the device awake and unlocked, or disable the lock "
                       "in Settings → Photos → Use Face ID.")
```

## Select all + delete

```python
sel = find(label="Select", type="XCUIElementTypeButton")
if sel is None:
    raise RuntimeError("Select button missing")
tap(sel); wait(0.4)

all_btn = find(label="Delete All", type="XCUIElementTypeButton") or \
          find(label="Select All", type="XCUIElementTypeButton")
if all_btn is None:
    raise RuntimeError("Neither Delete All nor Select All present — album may be empty")

# Path A: "Delete All" is a direct one-shot.
if all_btn["label"] == "Delete All":
    tap(all_btn); wait(0.5)
    confirm = find(label="Delete From All Devices", type="XCUIElementTypeButton") or \
              find(label="Delete", type="XCUIElementTypeButton")
    tap(confirm); wait(2.5)
else:
    # Path B: Select All → Delete.
    tap(all_btn); wait(0.4)
    trash = find(label="Delete", type="XCUIElementTypeButton") or \
            find(name="trash", type="XCUIElementTypeButton")
    tap(trash); wait(0.5)
    confirm = find(label="Delete From All Devices", type="XCUIElementTypeButton") or \
              find(label="Delete", type="XCUIElementTypeButton")
    tap(confirm); wait(2.5)
```

## Verification

```python
wait(2.0)
# Album page should now show "No Photos or Videos".
empty = find(label="No Photos or Videos", type="XCUIElementTypeStaticText") or \
        find(label="No Items", type="XCUIElementTypeStaticText")
assert empty is not None, "Recently Deleted still has items after Empty"
```

## Edge cases

- **iCloud Photos off** → confirmation reads "Delete N Items" instead of
  "Delete From All Devices". Both work; the helper above tries both.
- **Recovery view** (recently-emptied items can be recovered for a window)
  → ignore, we want them gone.
- **PIN gate when "Use Face ID" is off** → the only mitigation is to ask the
  user to enable Face ID for Photos, or to manually type the PIN before
  invoking. Detected and raised cleanly above.
