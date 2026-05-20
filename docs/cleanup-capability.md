# Cleanup & Organization Capability — iOS and Android

Status of every user-facing "clean up the phone" / "organize the phone" workflow in
`mobile_use` as of 2026-05-20. Marked **works** (verified path), **partial** (helpers
exist, no domain skill yet), **missing** (must be built).

The harness itself is general-purpose — `tap`, `find`, `ui_tree`, `appium("mobile: ...")`
can express any UI flow. "Missing" therefore means *no field-tested recipe* exists for an
agent to follow; the agent would have to discover it on every run. Domain skills convert
"the agent can probably figure it out" into "the agent gets it right the first time."

## Verification approach

For every workflow listed below, capability is judged on three layers:

1. **API reachability** — can Appium / XCUITest / UIAutomator2 reach the target screen at all?
2. **Recipe present** — is there a domain skill (`agent-workspace/domain-skills/<id>/*.md`)
   or core helper documenting the exact sequence?
3. **Demo proven** — has a runnable script under `docs/demos/` exercised the flow on a real device?

`works` requires all three. `partial` = layers 1+2 missing layer 3. `missing` = nothing
beyond layer 1.

## iOS

Bundle IDs in parentheses.

| Workflow | API reachable | Recipe | Demo | Status |
|---|---|---|---|---|
| List installed apps (Settings → General → iPhone Storage) | yes | no | no | missing |
| Uninstall app via SpringBoard long-press (`com.apple.springboard`) | yes (`mobile: tap` + `XCUIElementTypeButton["Delete App"]`) | no | no | missing |
| Uninstall via Settings → iPhone Storage → app → Delete App (`com.apple.Preferences`) | yes | no | no | missing |
| Offload unused app (Settings → iPhone Storage → app → Offload App) | yes | no | no | missing |
| Create folder by drag-and-drop on home screen | yes (W3C `actions` sequence — long-press, dwell, drag, release) | no | no | missing |
| Rename folder | yes | no | no | missing |
| Move app between pages / into folder / out of folder | yes | no | no | missing |
| Hide app to App Library only (Remove from Home Screen — keep in Library) | yes | no | no | missing |
| Open App Library and search | yes (`mobile: swipe` left past last page) | no | no | missing |
| Delete from App Library long-press | yes | no | no | missing |
| Photos: bulk select + delete (`com.apple.mobileslideshow`) | yes | no | no | missing |
| Photos: empty Recently Deleted (Face ID gate) | yes | no | no | missing |
| Photos: delete entire album (Screenshots, Selfies, Duplicates) | yes | no | no | missing |
| Files app: browse + delete (`com.apple.DocumentsApp`) | yes | no | no | missing |
| Files app: empty Downloads | yes | no | no | missing |
| Files app: empty Recently Deleted | yes | no | no | missing |
| Safari: Clear History and Website Data (Settings → Safari) | yes | no | no | missing |
| Storage summary scrape (per-app size from iPhone Storage rows) | yes (text on each row) | no | no | missing |
| Detect Screen Time PIN block on delete | yes (alert appears) | no | no | missing |

Core helpers that any of the above will reuse — already present:
- `find(label=, name=, type=, value=)`, `find_fuzzy`, `find_all`
- `tap`, `tap_safe`, `long_press`, `tap_at_xy`, `swipe`, `scroll_by`
- `wait_for_element`, `wait_for_app`, `wait`
- `alert`, `alert_accept`, `alert_dismiss`
- `screenshot`, `ocr`, `find_text`
- `appium("mobile: ...", **args)` — full XCUITest escape hatch

Helpers that are **missing** and would be valuable cross-cutting additions:
- `installed_apps()` — wrapper over scraping iPhone Storage list
- `uninstall_app(bundle_id_or_label)` — dispatch through SpringBoard
- `home_screen_layout()` — enumerate icons + page index from SpringBoard ui_tree
- `bulk_select(items, deletion_button="Delete")` — generic edit-mode pattern

## Android

Package IDs in parentheses. Field-tested baseline is Pixel + AOSP launcher
(`com.google.android.apps.nexuslauncher`). Samsung One UI / MIUI labels differ; skills
note when they diverge.

| Workflow | API reachable | Recipe | Demo | Status |
|---|---|---|---|---|
| List installed apps (Settings → Apps → See all apps) | yes | no | no | missing |
| Uninstall via Settings → Apps → app → Uninstall (`com.android.settings`) | yes | no | no | missing |
| Uninstall via launcher long-press → drag to Uninstall target | yes | no | no | missing |
| Disable system app (when Uninstall unavailable) | yes | no | no | missing |
| Clear app cache (Settings → Apps → app → Storage & cache → Clear cache) | yes | no | no | missing |
| Clear app storage (full data wipe for single app) | yes | no | no | missing |
| Home screen folder create (drag app onto app) | yes | no | no | missing |
| Home screen folder rename | yes | no | no | missing |
| Move app on home screen / between pages | yes | no | no | missing |
| Remove from home screen (keep in drawer) | yes | no | no | missing |
| App Drawer open + search + long-press | yes | no | no | missing |
| Storage cleanup wizard (Settings → Storage → Free up space → Files by Google handoff) | yes | no | no | missing |
| Files by Google — Clean tab (junk, large, duplicates, downloads) (`com.google.android.apps.nbu.files`) | yes | no | no | missing |
| Google Photos — bulk select + Move to Bin (`com.google.android.apps.photos`) | yes | no | no | missing |
| Google Photos — Empty Bin (Library → Bin → ⋮ → Empty Bin) | yes | no | no | missing |
| Detect device-admin / work-profile uninstall block | yes | no | no | missing |

Core helpers already present:
- `find(text=, resource_id=, type=, content_desc=)`, `find_fuzzy`, `find_all`
- `tap`, `tap_safe`, `long_press`, `tap_at_xy`, `swipe`, `scroll`, `scroll_by`
- `click(selector, by=...)` — UIAutomator strategies
- `press_back`, `press_home`, `press_recents`
- `open_notifications`, `close_notifications`, `grant_permission`
- `appium("mobile: ...", **args)` — full UIAutomator2 escape hatch

Helpers that are **missing**:
- `installed_apps()` — wrap `mobile: shell` `pm list packages -3` (third-party)
- `uninstall_app(package_or_label)` — `mobile: removeApp` is fast path; long-press fallback
- `app_drawer_apps()` — list visible labels
- `bulk_select(items)` — generic select-mode pattern

## Cross-cutting gaps

- **No `agent_helpers.uninstall_app(...)`** that dispatches iOS vs Android by the active
  daemon. Building this requires reaching into both `iphone_harness.helpers` and
  `android_harness.helpers`; one of them won't be importable without its daemon, so the
  dispatcher must lazy-import.
- **No bulk-select primitive**. Every "select 50 things → delete" flow ends up bespoke.
  Worth a single helper that takes a list of cell-finder callables + a confirmation button
  label.
- **No screenshot-anchored verification step.** Cleanup flows are destructive — every
  recipe should `screenshot()` *before* the destructive tap so an operator can audit.
- **No "is this app a system app?" check.** Uninstall on Android silently degrades to
  Disable. iOS just refuses (the Delete App option is absent from the long-press menu).
  Each recipe needs a defensive branch.

## What ships in this goal

1. `docs/cleanup-capability.md` (this file) — the map.
2. `agent-workspace/agent_helpers.py` — cross-platform helpers: `list_installed_apps`,
   `uninstall_app`, `storage_summary`, `bulk_select`, `confirm_destructive`.
3. Domain skills under `agent-workspace/domain-skills/`:
   - iOS: `com.apple.springboard`, `com.apple.Preferences`, `com.apple.mobileslideshow`,
     `com.apple.DocumentsApp`.
   - Android: `com.android.settings`, `com.google.android.apps.nexuslauncher`,
     `com.google.android.apps.nbu.files`, `com.google.android.apps.photos`.
4. `docs/demos/clean-and-organize-ios.py` and `…-android.py` — runnable demos honoring
   `DRY_RUN=1`.
5. `tests/test_cleanup_skills.py` — guards every required skill file exists, non-empty,
   contains the required terminology, and resolves through `domain_skills()`.
6. README section linking everything.

Limitations that are **out of scope** (not "missing" — deliberately deferred):

- Rooting / jailbreaking. Everything below assumes a normal, unmodified device.
- Bypassing Screen Time / parental PIN. Skill files document the dead-end and surface it.
- Cloud-side cleanup (iCloud.com, photos.google.com). On-device only.
- Per-OEM launcher variants (Samsung, MIUI). Pixel/AOSP only; OEM notes flagged.
