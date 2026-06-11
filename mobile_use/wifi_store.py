"""Wireless device remember-store — the persistence behind `--persist`,
`mobile-use devices remembered`, and `mobile-use wifi reconnect`.

A tiny JSON registry of wireless endpoints (Android adb-over-Wi-Fi serials,
iOS WebDriverAgent URLs) so reconnecting after a host reboot / network change
is one command — or zero, when the ensure hooks re-establish automatically.

Storage: ~/.mobile_use/wifi_devices.json (MU_WIFI_STORE overrides; never
/tmp — the registry must survive reboots, and site-packages installs have no
writable repo root). Missing/corrupt files load as an empty store (session.py
convention); writes are tmp-then-rename so a crash can't leave a half-written
registry.
"""
import json
import os
import time
from pathlib import Path

STORE_VERSION = 1


def store_path():
    p = os.environ.get("MU_WIFI_STORE")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".mobile_use" / "wifi_devices.json"


def _fresh():
    return {"version": STORE_VERSION, "devices": []}


def load_store():
    p = store_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _fresh()
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        return _fresh()
    data.setdefault("version", STORE_VERSION)
    return data


def save_store(store):
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _identity(entry):
    """(platform, stable-id) — what makes two entries 'the same device'."""
    plat = entry.get("platform")
    if plat == "android":
        ident = entry.get("serial")
        if not ident and entry.get("host"):
            ident = f"{entry['host']}:{entry.get('port', 5555)}"
    else:
        ident = entry.get("udid") or entry.get("wda_url")
    return (plat, ident)


def remember_device(platform, **fields):
    """Upsert one wireless device; returns the stored entry.

    android: serial (ip:port) and/or host (+ port, default 5555).
    ios: udid and/or wda_url. Extra fields (name, host, port, ...) are stored
    verbatim; None values are dropped. last_seen refreshes on every upsert.
    """
    entry = {"platform": platform,
             **{k: v for k, v in fields.items() if v is not None}}
    if platform == "android" and not entry.get("serial") and entry.get("host"):
        entry["serial"] = f"{entry['host']}:{entry.get('port', 5555)}"
    entry["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    if _identity(entry)[1] is None:
        raise ValueError(
            "remember_device needs an identity: serial/host (android) "
            "or udid/wda_url (ios)")
    store = load_store()
    store["devices"] = [e for e in store["devices"]
                        if _identity(e) != _identity(entry)]
    store["devices"].append(entry)
    save_store(store)
    return entry


def forget_device(platform, **fields):
    """Remove matching entries; returns the count removed.

    With an identity field (serial/udid/wda_url/host) the match is by
    identity; with other fields only, every given field must equal.
    """
    store = load_store()
    probe = {"platform": platform, **fields}
    probe_ident = _identity(probe)
    keep, removed = [], 0
    for e in store["devices"]:
        if probe_ident[1] is not None:
            hit = _identity(e) == probe_ident
        else:
            hit = (e.get("platform") == platform
                   and all(e.get(k) == v for k, v in fields.items()))
        if hit:
            removed += 1
        else:
            keep.append(e)
    if removed:
        store["devices"] = keep
        save_store(store)
    return removed


def remembered_devices(platform=None):
    devs = load_store()["devices"]
    if platform is None:
        return list(devs)
    return [e for e in devs if e.get("platform") == platform]
