"""Multi-device multiboxing — drive multiple iOS + Android devices simultaneously.

Each device gets its own named daemon instance. Named instances use separate
sockets (/tmp/iph-<name>.sock, /tmp/anh-<name>.sock) so they don't collide.

Usage:
    from mobile_use.multibox import DevicePool, Device

    pool = DevicePool()
    pool.add_ios("iphone1", udid="00008030-XXX", xcode_org_id="ABC", wda_bundle_id="com.me.wda")
    pool.add_android("pixel", udid="SERIAL123")

    # Drive all devices
    for dev in pool.devices:
        dev.ensure_ready()
        print(dev.name, dev.active_app())

    # Drive specific device
    pool["iphone1"].tap_at_xy(200, 400)
    pool["pixel"].press_home()

    # Parallel execution
    pool.broadcast(lambda d: d.screenshot())
"""
import functools
import hashlib
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

_APPIUM_PORT_RANGE = (4724, 4799)


def _store_entry_name(entry):
    """Pool name for a wifi-store entry: serial / udid / wda_url host,
    sanitized to the daemon-name alphabet ([A-Za-z0-9_-])."""
    if entry.get("platform") == "android":
        base = entry.get("serial") or "android"
    else:
        base = entry.get("udid") or entry.get("wda_url") or "ios"
    base = re.sub(r"^https?://", "", str(base))
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")[:64]
    return base or "device"


def _port_is_free(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _allocate_appium_port(name, host="127.0.0.1"):
    """Deterministic per-name port in 4724-4799 (skipping default 4723).

    Hashes the name so repeated calls return the same port (idempotent
    daemon respawn). If the chosen port is bound by something else, walks
    the range until a free port is found. Raises if the whole range is
    saturated.
    """
    low, high = _APPIUM_PORT_RANGE
    span = high - low + 1
    digest = int(hashlib.sha256(name.encode()).hexdigest(), 16)
    start = low + (digest % span)
    for offset in range(span):
        port = low + ((start - low + offset) % span)
        if _port_is_free(port, host):
            return port
    raise RuntimeError(
        f"no free port in {low}-{high} for daemon {name!r}. "
        f"Pass appium_url= explicitly or free up some ports."
    )


class Device:
    """A single managed device instance."""

    def __init__(self, name, platform, env_overrides=None):
        self.name = name
        self.platform = platform
        self._env = env_overrides or {}
        self._helpers = None
        self._admin = None

    def _build_env(self):
        """Build environment dict for this device's daemon."""
        env = dict(os.environ)
        env.update(self._env)
        if self.platform == "ios":
            env["IPH_NAME"] = self.name
        else:
            env["ANH_NAME"] = self.name
        return env

    def _load(self):
        """Import this device's harness modules once. No module reloading and no
        global-env mutation: the per-device daemon name is bound per call via
        helpers._use_name (see _bound) so concurrent devices never cross-route.
        The spawned daemon still receives its IPH_*/ANH_* env via ensure_daemon(env=).
        """
        if self._helpers is not None:
            return
        if self.platform == "ios":
            import iphone_harness.admin as admin
            import iphone_harness.helpers as helpers
        else:
            import android_harness.admin as admin
            import android_harness.helpers as helpers
        self._helpers = helpers
        self._admin = admin

    def _bound(self, fn):
        """Wrap a helpers verb so it runs with this device's daemon name bound for
        the duration of the call — the same per-name addressing NamedStreamClient
        uses. Safe under DevicePool's ThreadPoolExecutor: the contextvar is set in
        the worker thread that actually runs the call."""
        helpers = self._helpers
        dev_name = self.name

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            token = helpers._use_name(dev_name)
            try:
                return fn(*args, **kwargs)
            finally:
                helpers._reset_name(token)
        return wrapper

    def ensure_ready(self):
        """Ensure the daemon for this device is running."""
        self._load()
        env = self._build_env()
        self._admin.ensure_daemon(name=self.name, env=env)

    def run(self, code):
        """Execute a code string with helpers pre-imported, bound to this device."""
        self._load()
        ns = {k: v for k, v in vars(self._helpers).items() if not k.startswith("_")}
        ns["__builtins__"] = __builtins__
        token = self._helpers._use_name(self.name)
        try:
            exec(code, ns)
        finally:
            self._helpers._reset_name(token)

    def __getattr__(self, name):
        """Proxy attribute access to the helpers module, bound to this device's name."""
        if name.startswith("_") or name in ("name", "platform"):
            raise AttributeError(name)
        self._load()
        fn = getattr(self._helpers, name, None)
        if fn is not None and callable(fn):
            return self._bound(fn)
        # Explicit capability signal: in a mixed pool, a platform-only verb (e.g.
        # swipe_back on Android, key_event on iOS) names the platform, not a bare
        # "no helper" — so broadcast()'s per-device error says WHY it failed.
        raise AttributeError(
            f"Device {self.name!r}: action {name!r} is not supported on {self.platform}")

    def __repr__(self):
        return f"Device({self.name!r}, platform={self.platform!r})"


class DevicePool:
    """Manage multiple devices for simultaneous control."""

    def __init__(self):
        self._devices = {}

    def add_ios(self, name, udid, xcode_org_id=None, wda_bundle_id=None,
                appium_url=None, platform_version=None, wda_url=None):
        """Add an iOS device to the pool.

        If `appium_url` is None, auto-allocate a per-name port in 4724-4799
        to avoid collisions when multiple named daemons run simultaneously.
        Pass `appium_url=` explicitly to override (e.g. remote Appium).

        `wda_url` is the cable-free WebDriverAgent endpoint for THIS device —
        it rides the per-name env overrides (IPH_WDA_URL), so several Wi-Fi
        iPhones in one pool never share the one global key.
        """
        env = {"IPH_UDID": udid, "IPH_NAME": name}
        if xcode_org_id:
            env["IPH_XCODE_ORG_ID"] = xcode_org_id
        if wda_bundle_id:
            env["IPH_WDA_BUNDLE_ID"] = wda_bundle_id
        if appium_url:
            env["IPH_APPIUM_URL"] = appium_url
        else:
            port = _allocate_appium_port(f"ios-{name}")
            env["IPH_APPIUM_URL"] = f"http://127.0.0.1:{port}"
        if platform_version:
            env["IPH_PLATFORM_VERSION"] = platform_version
        if wda_url:
            env["IPH_WDA_URL"] = wda_url

        dev = Device(name, "ios", env)
        self._devices[name] = dev
        return dev

    def add_android(self, name, udid, appium_url=None, platform_version=None):
        """Add an Android device to the pool.

        If `appium_url` is None, auto-allocate a per-name port in 4724-4799
        (different range slot than iOS via name prefix) to avoid collisions.
        """
        env = {"ANH_UDID": udid, "ANH_NAME": name}
        if appium_url:
            env["ANH_APPIUM_URL"] = appium_url
        else:
            port = _allocate_appium_port(f"android-{name}")
            env["ANH_APPIUM_URL"] = f"http://127.0.0.1:{port}"
        if platform_version:
            env["ANH_PLATFORM_VERSION"] = platform_version

        dev = Device(name, "android", env)
        self._devices[name] = dev
        return dev

    @classmethod
    def from_connected(cls, **kwargs):
        """Build a pool from `mobile_use.devices.discover_connected()`.

        Auto-populates with every iOS + Android device currently connected.
        Names come from device metadata (e.g. "Pixel-7") with collision
        indexing. iOS devices need `xcode_org_id` / `wda_bundle_id` for daemon
        spawn — pass via kwargs (applied to every iOS device) or set the
        IPH_XCODE_ORG_ID / IPH_WDA_BUNDLE_ID env vars before calling.

        Raises RuntimeError when no devices found, with a hint about which
        discovery tools are missing.
        """
        from . import devices as _discovery
        discovered = _discovery.discover_connected()
        if not discovered:
            hints = _discovery.discovery_hints()
            hint_lines = "\n  ".join(hints) if hints else "(install adb / libimobiledevice and reconnect)"
            raise RuntimeError(
                f"DevicePool.from_connected(): no devices detected.\n  {hint_lines}"
            )

        ios_kwargs = {k: v for k, v in kwargs.items()
                      if k in ("xcode_org_id", "wda_bundle_id", "appium_url", "platform_version")}
        android_kwargs = {k: v for k, v in kwargs.items()
                          if k in ("appium_url", "platform_version")}

        pool = cls()
        for entry in discovered:
            if entry["platform"] == "ios":
                pool.add_ios(entry["name"], udid=entry["udid"], **ios_kwargs)
            else:
                pool.add_android(entry["name"], udid=entry["udid"], **android_kwargs)
        return pool

    @classmethod
    def from_remembered(cls, platform=None, **kwargs):
        """Build a pool from the wireless remember-store (devices saved by
        `--persist`).

        android entries become add_android(udid=<serial>); ios entries become
        add_ios(wda_url=<stored url>) — each daemon carries its own
        IPH_WDA_URL via per-name env overrides, so several Wi-Fi iPhones
        coexist. kwargs forward like from_connected (xcode_org_id /
        wda_bundle_id / appium_url / platform_version).

        Raises RuntimeError when the store has no matching entries.
        """
        from mobile_use.wifi_store import remembered_devices
        entries = remembered_devices(platform)
        if not entries:
            raise RuntimeError(
                "DevicePool.from_remembered(): no remembered wireless devices.\n"
                "  android: mobile-use android wifi <ip> --persist\n"
                "  ios:     mobile-use ios wifi --persist")

        ios_kwargs = {k: v for k, v in kwargs.items()
                      if k in ("xcode_org_id", "wda_bundle_id", "appium_url", "platform_version")}
        android_kwargs = {k: v for k, v in kwargs.items()
                          if k in ("appium_url", "platform_version")}

        pool = cls()
        for e in entries:
            name = e.get("name") or _store_entry_name(e)
            if name in pool:
                i = 2
                while f"{name}-{i}" in pool:
                    i += 1
                name = f"{name}-{i}"
            if e.get("platform") == "ios":
                pool.add_ios(name, udid=e.get("udid") or "",
                             wda_url=e.get("wda_url"), **ios_kwargs)
            else:
                pool.add_android(name, udid=e.get("serial"), **android_kwargs)
        return pool

    def add_from_udid(self, udid, **kwargs):
        """Look up a discovered device by UDID and add it to the pool.

        Useful when you want to cherry-pick specific devices instead of
        adding all connected ones.
        """
        from . import devices as _discovery
        for entry in _discovery.discover_connected():
            if entry["udid"] == udid:
                if entry["platform"] == "ios":
                    return self.add_ios(entry["name"], udid=udid, **kwargs)
                return self.add_android(entry["name"], udid=udid, **kwargs)
        raise ValueError(
            f"udid {udid!r} not found in `mobile-use devices list`. "
            f"Check it's connected and authorized."
        )

    @property
    def devices(self):
        return list(self._devices.values())

    @property
    def ios_devices(self):
        return [d for d in self._devices.values() if d.platform == "ios"]

    @property
    def android_devices(self):
        return [d for d in self._devices.values() if d.platform == "android"]

    def __getitem__(self, name):
        return self._devices[name]

    def __contains__(self, name):
        return name in self._devices

    def __len__(self):
        return len(self._devices)

    def __iter__(self):
        return iter(self._devices.values())

    def ensure_all_ready(self, max_workers=4):
        """Start all device daemons in parallel."""
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(d.ensure_ready): d for d in self._devices.values()}
            results = {}
            for f in as_completed(futures):
                dev = futures[f]
                try:
                    f.result()
                    results[dev.name] = "ready"
                except Exception as e:
                    results[dev.name] = f"error: {e}"
        return results

    def broadcast(self, fn, max_workers=4):
        """Execute a function on all devices in parallel.

        Args:
            fn: callable taking a Device, e.g. lambda d: d.screenshot()

        Returns:
            dict mapping device name → result or error
        """
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fn, d): d for d in self._devices.values()}
            results = {}
            for f in as_completed(futures):
                dev = futures[f]
                try:
                    results[dev.name] = {"result": f.result()}
                except Exception as e:
                    results[dev.name] = {"error": str(e)}
        return results

    def broadcast_ios(self, fn, max_workers=4):
        """Execute on all iOS devices in parallel."""
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fn, d): d for d in self.ios_devices}
            results = {}
            for f in as_completed(futures):
                dev = futures[f]
                try:
                    results[dev.name] = {"result": f.result()}
                except Exception as e:
                    results[dev.name] = {"error": str(e)}
        return results

    def broadcast_android(self, fn, max_workers=4):
        """Execute on all Android devices in parallel."""
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fn, d): d for d in self.android_devices}
            results = {}
            for f in as_completed(futures):
                dev = futures[f]
                try:
                    results[dev.name] = {"result": f.result()}
                except Exception as e:
                    results[dev.name] = {"error": str(e)}
        return results

    def remove(self, name):
        """Remove a device from the pool."""
        if name in self._devices:
            del self._devices[name]

    def status(self):
        """Check status of all devices."""
        out = {}
        for name, dev in self._devices.items():
            try:
                dev._load()
                alive = dev._admin.daemon_alive(name)
                out[name] = {
                    "platform": dev.platform,
                    "daemon": "alive" if alive else "not running",
                }
            except Exception as e:
                out[name] = {
                    "platform": dev.platform,
                    "daemon": f"error: {e}",
                }
        return out

    def summary(self):
        """Human-readable pool summary."""
        if not self._devices:
            return "No devices in pool."
        lines = [f"DevicePool: {len(self._devices)} device(s)"]
        for name, dev in self._devices.items():
            lines.append(f"  {name} ({dev.platform})")
        return "\n".join(lines)
