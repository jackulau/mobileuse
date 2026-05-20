# SpringBoard — App Library

Bundle id: `com.apple.springboard`. Field-tested on iOS 18.3.

App Library is the rightmost page on the home screen. It auto-categorizes
every installed app and exposes a search field. Use it to:

- Delete apps that are no longer pinned to a home page.
- Re-add apps to the home screen.
- Locate apps when you don't know the page they're on.

## Open the App Library

```python
# Make sure we're on the home screen.
appium("mobile: pressButton", name="home")
wait(0.5)
appium("mobile: pressButton", name="home")
wait(0.4)

# Swipe left until we land on App Library. The search field appears at the top
# only on App Library, so we can use its presence as a stop condition.
w, h = window_size()["width"], window_size()["height"]
for _ in range(8):
    if find(label="App Library", type="XCUIElementTypeSearchField"):
        break
    swipe(w * 0.85, h * 0.5, w * 0.15, h * 0.5, duration=0.2)
    wait(0.4)
```

## Search and launch

```python
field = find(label="App Library", type="XCUIElementTypeSearchField")
tap(field); wait(0.3)
type_text("twit")
wait(0.5)
# Results show as cells in a list; the first match is the canonical Twitter app.
result = find(type="XCUIElementTypeCell", label="Twitter")
tap(result); wait(2.0)
```

## Long-press to delete from App Library

This is the only way to delete an app that has been "Removed from Home
Screen" — it now lives only in App Library.

```python
appium("mobile: touchAndHold", elementId=result["id"], duration=0.9)
wait(0.6)
tap(find(label="Delete App", type="XCUIElementTypeButton")); wait(0.5)
tap(find(label="Delete", type="XCUIElementTypeButton")); wait(1.0)
```

## Add an App Library app back to the home screen

```python
# From a category tile or search result:
appium("mobile: touchAndHold", elementId=result["id"], duration=0.9)
wait(0.6)
add = find(label="Add to Home Screen", type="XCUIElementTypeButton")
if add is None:
    # Already on home screen — menu instead shows "Remove from Home Screen"
    raise RuntimeError("app is already on home screen")
tap(add); wait(0.8)
```

## Enumerate every installed app from App Library

The category tiles each contain up to four icons + a "more" mini-tile. To
list every app, tap each category and read its grid.

```python
def list_app_library_apps():
    out = set()
    # Each category tile is a button labeled with the category name.
    for cat in find_all(type="XCUIElementTypeButton"):
        if cat.get("name", "").endswith("Folder") or cat.get("label") in (
            "Suggestions", "Recently Added"
        ):
            tap(cat); wait(0.5)
            # In the open grid, icons are XCUIElementTypeIcon.
            for icon in find_all(type="XCUIElementTypeIcon"):
                if icon.get("label"):
                    out.add(icon["label"])
            # Back out.
            tap_at_xy(20, 80); wait(0.4)
    return sorted(out)
```

This is slower than scraping iPhone Storage (`agent_helpers.list_installed_apps()`
uses Settings) but works when Settings is restricted.

## Limitations

- **Suggestions / Recently Added** categories are dynamic — don't iterate them
  if you need a stable list.
- App Library hides apps that the device classifies as "hidden" (Face ID
  required to view). Those will not appear in `find_all(...)` output until
  authenticated.
- The search field uses **Spotlight-style fuzzy matching** — partial inputs
  may return unrelated apps. Type at least 3 characters when searching.
