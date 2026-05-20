# Toast Messages

Android Toast messages are transient pop-ups that appear for 2-3.5 seconds and disappear automatically. They are **not in the accessibility tree** — `ui_tree()` and `find()` cannot see them.

## The problem

Toasts are drawn by the system window manager outside the app's view hierarchy. UIAutomator2 can't locate them via normal element finding.

## Detection strategies

### 1. Screenshot + OCR (most reliable)

```python
# Do the action that triggers a toast
tap(find(text="Save"))
wait(0.5)  # toast appears
lines, _ = ocr()
for line in lines:
    if "saved" in line["text"].lower():
        print("Toast confirmed:", line["text"])
```

### 2. Timed screenshot

```python
# Toasts last 2s (short) or 3.5s (long)
tap(find(text="Copy"))
wait(0.3)
path = screenshot()  # capture while toast is visible
# Visually inspect or OCR the screenshot
```

### 3. Snackbars (the modern replacement)

Material Design Snackbars ARE in the accessibility tree (they're app-level views, not system toasts):

```python
snackbar = find(type="com.google.android.material.snackbar.SnackbarContentLayout")
# or
snackbar_text = find(resource_id="com.google.android.material.snackbar.Snackbar")
```

## Gotchas

- **Timing**: toasts disappear fast. Take the screenshot within 0.5s of the triggering action.
- **Toast vs Snackbar**: modern apps use Snackbars (findable) instead of Toasts (not findable). Check `ui_tree()` first.
- **Overlapping toasts**: only one toast at a time. New toast replaces current one.
