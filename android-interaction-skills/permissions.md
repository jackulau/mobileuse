# Runtime Permission Dialogs

Android apps request dangerous permissions (camera, location, contacts, etc.) at runtime. The dialog blocks the app until the user responds.

## What they look like

```
┌─────────────────────────────┐
│  Allow <App> to access      │
│  your camera?               │
│                             │
│  [While using the app]      │
│  [Only this time]           │
│  [Don't allow]              │
└─────────────────────────────┘
```

The dialog is a system UI — it appears in `ui_tree()` with elements from `com.android.permissioncontroller`.

## Handling patterns

### Programmatic grant (preferred for automation)

```python
grant_permission('com.example.app', 'android.permission.CAMERA')
grant_permission('com.example.app', 'android.permission.ACCESS_FINE_LOCATION')
```

This uses `adb shell pm grant` — no UI interaction needed. Works for most permissions.

### UI-based grant (when programmatic fails)

```python
# The button text varies by Android version
allow = find(text="While using the app") or find(text="Allow") or find(resource_id="com.android.permissioncontroller:id/permission_allow_foreground_only_button")
if allow:
    tap(allow)
```

### Deny

```python
deny = find(text="Don't allow") or find(text="Deny")
if deny:
    tap(deny)
```

## Common permission strings

| Permission | String |
|---|---|
| Camera | `android.permission.CAMERA` |
| Fine location | `android.permission.ACCESS_FINE_LOCATION` |
| Coarse location | `android.permission.ACCESS_COARSE_LOCATION` |
| Microphone | `android.permission.RECORD_AUDIO` |
| Read contacts | `android.permission.READ_CONTACTS` |
| Read storage | `android.permission.READ_EXTERNAL_STORAGE` |
| Write storage | `android.permission.WRITE_EXTERNAL_STORAGE` |
| Phone | `android.permission.CALL_PHONE` |
| SMS | `android.permission.SEND_SMS` |

## Gotchas

- **"Don't ask again"**: if the user previously denied with "Don't ask again", the dialog won't appear. Use `grant_permission()` instead.
- **Android 13+**: granular media permissions (`READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `READ_MEDIA_AUDIO`) replace `READ_EXTERNAL_STORAGE`.
- **Notification permission**: Android 13+ requires `POST_NOTIFICATIONS` — a new runtime permission.
