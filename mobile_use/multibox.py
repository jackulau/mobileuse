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
import os
from concurrent.futures import ThreadPoolExecutor, as_completed


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
        if self._helpers is not None:
            return

        device_env = self._build_env()
        for k, v in device_env.items():
            if k.startswith(("IPH_", "ANH_")):
                os.environ.setdefault(k, v)

        if self.platform == "ios":
            env_backup = os.environ.get("IPH_NAME")
            os.environ["IPH_NAME"] = self.name
            for k, v in self._env.items():
                os.environ[k] = v

            import importlib
            import iphone_harness._ipc
            import iphone_harness.helpers
            import iphone_harness.admin

            importlib.reload(iphone_harness._ipc)
            importlib.reload(iphone_harness.helpers)
            importlib.reload(iphone_harness.admin)

            self._helpers = iphone_harness.helpers
            self._admin = iphone_harness.admin

            if env_backup is not None:
                os.environ["IPH_NAME"] = env_backup
        else:
            env_backup = os.environ.get("ANH_NAME")
            os.environ["ANH_NAME"] = self.name
            for k, v in self._env.items():
                os.environ[k] = v

            import importlib
            import android_harness._ipc
            import android_harness.helpers
            import android_harness.admin

            importlib.reload(android_harness._ipc)
            importlib.reload(android_harness.helpers)
            importlib.reload(android_harness.admin)

            self._helpers = android_harness.helpers
            self._admin = android_harness.admin

            if env_backup is not None:
                os.environ["ANH_NAME"] = env_backup

    def ensure_ready(self):
        """Ensure the daemon for this device is running."""
        self._load()
        env = self._build_env()
        self._admin.ensure_daemon(name=self.name, env=env)

    def run(self, code):
        """Execute a code string with helpers pre-imported."""
        self._load()
        ns = {k: v for k, v in vars(self._helpers).items() if not k.startswith("_")}
        ns["__builtins__"] = __builtins__
        exec(code, ns)

    def __getattr__(self, name):
        """Proxy attribute access to the helpers module."""
        if name.startswith("_") or name in ("name", "platform"):
            raise AttributeError(name)
        self._load()
        fn = getattr(self._helpers, name, None)
        if fn is not None and callable(fn):
            return fn
        raise AttributeError(f"Device {self.name!r} has no helper {name!r}")

    def __repr__(self):
        return f"Device({self.name!r}, platform={self.platform!r})"


class DevicePool:
    """Manage multiple devices for simultaneous control."""

    def __init__(self):
        self._devices = {}

    def add_ios(self, name, udid, xcode_org_id=None, wda_bundle_id=None,
                appium_url=None, platform_version=None):
        """Add an iOS device to the pool."""
        env = {"IPH_UDID": udid, "IPH_NAME": name}
        if xcode_org_id:
            env["IPH_XCODE_ORG_ID"] = xcode_org_id
        if wda_bundle_id:
            env["IPH_WDA_BUNDLE_ID"] = wda_bundle_id
        if appium_url:
            env["IPH_APPIUM_URL"] = appium_url
        if platform_version:
            env["IPH_PLATFORM_VERSION"] = platform_version

        dev = Device(name, "ios", env)
        self._devices[name] = dev
        return dev

    def add_android(self, name, udid, appium_url=None, platform_version=None):
        """Add an Android device to the pool."""
        env = {"ANH_UDID": udid, "ANH_NAME": name}
        if appium_url:
            env["ANH_APPIUM_URL"] = appium_url
        if platform_version:
            env["ANH_PLATFORM_VERSION"] = platform_version

        dev = Device(name, "android", env)
        self._devices[name] = dev
        return dev

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
