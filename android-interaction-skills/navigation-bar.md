# Navigation Bar (Back / Home / Recents)

Android reserves the bottom ~48dp for the system navigation bar. This is the Android equivalent of iOS's home-bar gesture zone.

## The problem

Taps in the bottom ~48dp may trigger system navigation (Back, Home, Recents) instead of reaching the app. Elements positioned there are technically visible in `ui_tree()` but untappable.

## The fix

Same pattern as iOS `tap_safe()` — scroll the target element up before tapping:

```python
btn = find(text="Submit")
tap_safe(btn, refind=lambda: find(text="Submit"))
```

`tap_safe()` checks if the element's bottom edge falls in the danger zone (below `window_size()['height'] - 48`) and scrolls up if needed.

## Gesture navigation vs button navigation

- **3-button nav** (old style): Back / Home / Recents buttons. Always visible, ~48dp.
- **Gesture navigation** (modern): thin bar at bottom, ~20dp. Swipe-up-from-bottom = Home, swipe-from-edge = Back.
- **2-button nav** (Pixel): Home pill + Back. ~48dp.

`NAV_BAR_PX = 48` in `helpers.py` covers the worst case. Gesture nav is thinner but the safe zone still works.

## Helpers

```python
press_back()       # programmatic Back — always works, no gesture zone issue
press_home()       # programmatic Home
press_recents()    # programmatic Recents/Overview
```

These use keycodes, not coordinates — they work regardless of navigation bar style.
