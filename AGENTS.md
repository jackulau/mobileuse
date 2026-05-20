mobile-use is a thin layer that connects agents to real mobile devices via Appium.
iOS uses XCUITest (iphone_harness), Android uses UIAutomator2 (android_harness).

Built by @jackulau — github.com/jackulau/mobile_use.

# Code priorities
- Clarity
- Precision
- Low verbosity
- Versatility

# Overview

## iOS — `iphone_harness/`
- `daemon.py` — long-lived middleman owning the Appium/XCUITest session
- `helpers.py` — public action API auto-imported into `-c` scripts
- `admin.py` — daemon lifecycle, doctor
- `run.py` — the `iphone-harness` CLI
- `_ipc.py` — AF_UNIX JSON-line RPC

## Android — `android_harness/`
- `daemon.py` — long-lived middleman owning the Appium/UIAutomator2 session
- `helpers.py` — public action API auto-imported into `-c` scripts
- `admin.py` — daemon lifecycle, doctor
- `run.py` — the `android-harness` CLI
- `_ipc.py` — AF_UNIX JSON-line RPC

## Shared
- `SKILL.md` tells agents how to use either harness and CLI.
- `SETUP.md` tells agents how to install, attach a device, and troubleshoot.
- `agent-workspace/` — agent-editable helpers + per-app domain skills
- `interaction-skills/` — iOS-specific UI mechanics
- `android-interaction-skills/` — Android-specific UI mechanics

An agent operating the harness only edits inside `agent-workspace/`:
- `agent_helpers.py` — task-specific helpers the agent adds
- `domain-skills/<bundleId-or-package>/` — per-app skills the agent writes and reads

# Contributing
Consider what is really needed. Prefer the smallest diff that fixes the bug.
