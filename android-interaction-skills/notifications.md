# Notification Shade

## Opening

```python
open_notifications()   # pulls down the notification shade
```

This uses UIAutomator2's built-in `mobile: openNotifications` — reliable across devices.

## Reading notifications

Once open, notifications appear in `ui_tree()`:

```python
open_notifications()
wait(0.5)
for el in ui_tree(visible_only=True):
    if el["type"] == "android.widget.FrameLayout" and el["text"]:
        print(el["text"])
```

Notification elements typically have:
- `android.widget.TextView` for title and body text
- `resource_id` patterns like `android:id/title`, `android:id/text`
- `content_desc` with summary text

## Interacting

```python
# Tap a notification
notif = find(text="New message from Alice")
if notif:
    tap(notif)

# Expand a bundled notification
notif_group = find(content_desc="3 new messages")
if notif_group:
    # Swipe down on it to expand
    long_press(notif_group["cx"], notif_group["cy"], duration=0.3)
```

## Closing

```python
close_notifications()   # presses Back
# or
press_home()            # also dismisses
```

## Quick Settings

Quick Settings is the notification shade pulled down further (two-finger pull or pull down twice):

```python
open_notifications()
wait(0.3)
# Pull down again to reveal Quick Settings
sz = window_size()
swipe(sz["width"] // 2, 100, sz["width"] // 2, sz["height"] // 2)
wait(0.5)
# Now Quick Settings tiles are in the tree
wifi = find(text="Wi-Fi") or find(content_desc="Wi-Fi")
```

## Gotchas

- **Notification grouping**: Android bundles notifications from the same app. Expand first to see individual items.
- **Do Not Disturb**: may suppress notification display even though they exist.
- **Heads-up notifications**: appear at top of screen briefly, then go to shade. `find()` catches them while visible.
