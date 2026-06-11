"""Platform detection + Linux package-manager helpers.

Single source of truth for `sys.platform` and Linux distro detection.
Other modules (bootstrap, doctor checks, OCR fallback) import from here
instead of sprinkling `sys.platform == "darwin"` checks throughout.

Supported Linux package managers: apt, dnf, pacman, zypper, apk. Anything
else returns None — callers handle that as "manual install required" and
print apt/dnf/pacman one-liners as a fallback hint.

iOS is locked to macOS (Xcode + Apple codesigning). Linux drives iOS only
via a remote macOS Appium server — there is no local Linux path to a built
WebDriverAgent. Callers that need iOS-on-Linux check `IPH_APPIUM_URL`
pointing at a non-localhost host (the remote macOS Appium).
"""
import hashlib
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path


def is_linux() -> bool:
    return sys.platform == "linux"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    """Native Windows (not WSL — WSL reports as linux). iOS work requires a
    remote Mac daemon — Windows hosts cannot build/sign WebDriverAgent."""
    return sys.platform == "win32"


def needs_remote_mac_for_ios() -> bool:
    """True when this host cannot run xcodebuild + libimobiledevice locally
    (anything except macOS). Such hosts can still drive iOS via
    IPH_CONNECT=tcp://<mac-ip>:<port> pointed at a remote macOS daemon."""
    return not is_macos()


def windows_ios_setup_hint() -> str:
    """Multi-line guidance shown on Windows when iOS work is attempted without
    IPH_CONNECT set."""
    return (
        "iOS control from Windows requires a Mac on the network running\n"
        "Appium + a built WebDriverAgent + the iphone-harness daemon bound\n"
        "to TCP. Steps:\n"
        "  1. On the Mac: `mobile-use bootstrap --ios-only` then\n"
        "     `mobile-use ios build-wda` (one-time signing in Xcode).\n"
        "  2. On the Mac: start daemon over TCP --\n"
        "     `IPH_BIND=tcp://127.0.0.1:8763 iphone-harness -c 'pass'`\n"
        "     then `ssh -L 8763:127.0.0.1:8763 mac` from Windows.\n"
        "  3. On Windows: point the CLI at the tunneled daemon --\n"
        "     `mobile-use --ios --remote-daemon tcp://127.0.0.1:8763 -c ...`\n"
        "Alternative (Mac needed ONCE, not at runtime): install a pre-signed\n"
        "WebDriverAgent ipa with `mobile-use ios install-wda <wda.ipa>`, then\n"
        "`mobile-use ios wifi <device-ip> --persist` to drive it cable-free.\n"
        "Docs: see SETUP.md -> 'iOS from Windows / Linux (remote Mac bridge)'."
    )


def windows_runtime_dir() -> str:
    """Windows-writable base dir for daemon pid/log files.

    Prefers %LOCALAPPDATA%\\mobile-use, falls back to tempfile.gettempdir().
    Never '/tmp' — on Windows Path('/tmp') maps to <drive>:\\tmp, which is the
    wrong location and often not writable. Pure function: does NOT create the
    dir (callers mkdir the resolved base). iOS/AF_UNIX constraints don't apply
    on Windows — the daemon uses TCP loopback there (see daemon_tcp_port)."""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return str(Path(base) / "mobile-use")


def default_runtime_base() -> str:
    """Platform-correct base dir for daemon runtime files (pid/log/sock).

    POSIX → '/tmp': AF_UNIX sun_path is capped near 104 bytes and macOS's
    tempfile.gettempdir() returns a too-long /var/folders/... path, so the
    short, well-known /tmp is the only safe AF_UNIX home (this preserves the
    existing behavior documented in iphone_harness/_ipc.py).
    Windows → windows_runtime_dir() (AF_UNIX is unavailable; daemon uses TCP)."""
    if is_windows():
        return windows_runtime_dir()
    return "/tmp"


def daemon_tcp_port(name: str) -> int:
    """Deterministic, idempotent loopback RPC port for a named daemon.

    The Windows default transport (AF_UNIX is unavailable on Windows CPython).
    Hashing the name into a fixed range lets the daemon (bind side) and the
    client (connect side) independently compute the SAME port from the same
    name — name-based routing survives with zero shared state. Range 8400-8499
    is clear of multibox's Appium range (4724-4799). Mirrors the sha256-modulo
    scheme of multibox._allocate_appium_port for consistency."""
    low, high = 8400, 8499
    span = high - low + 1
    digest = int(hashlib.sha256((name or "").encode()).hexdigest(), 16)
    return low + (digest % span)


def _process_exists_windows(pid: int) -> bool:
    """Windows process-existence check via the Win32 API — NEVER os.kill.

    On Windows os.kill(pid, sig) maps any non-CTRL signal (including 0) to
    TerminateProcess, so the POSIX `os.kill(pid, 0)` liveness idiom would KILL
    the process being probed. OpenProcess + GetExitCodeProcess is the safe
    read-only equivalent: a running process reports STILL_ACTIVE (259)."""
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        # Couldn't open: ACCESS_DENIED → the pid exists but isn't ours (treat as
        # alive, mirroring POSIX EPERM); any other error → no such process.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # handle opened but exit code unreadable → assume alive
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_exists(pid: int) -> bool:
    """Liveness probe for a pid that is SAFE on every platform.

    POSIX → os.kill(pid, 0): signal 0 only checks existence, never delivers.
    Windows → Win32 OpenProcess (os.kill would TerminateProcess the pid — see
    _process_exists_windows). Mirrors POSIX semantics: a process owned by
    another user counts as alive. Callers must type-validate pid first."""
    if is_windows():
        return _process_exists_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours — don't treat as dead
    except OSError:
        return False


def kill_pid(pid: int, hard: bool = False) -> None:
    """Best-effort terminate a pid, swallowing already-dead / not-ours errors.

    POSIX: hard=False → SIGTERM (graceful), hard=True → SIGKILL.
    Windows: there is no graceful signal and signal.SIGKILL does not exist
    (referencing it raises AttributeError — the bug this replaces). os.kill with
    any non-CTRL signal maps to TerminateProcess, so one SIGTERM is a hard kill;
    SIGKILL is never referenced. Centralized here so both harness admins share
    one Windows-safe implementation (no per-twin signal handling to drift)."""
    if is_windows():
        sig = signal.SIGTERM  # os.kill → TerminateProcess; no SIGKILL on Windows
    else:
        sig = signal.SIGKILL if hard else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        # Windows raises a generic OSError (e.g. WinError 87) for a dead/invalid
        # pid; POSIX may raise EINVAL. Best-effort teardown swallows both.
        pass


def ensure_utf8_streams() -> None:
    """Force stdout/stderr to UTF-8 so non-ASCII output doesn't crash the CLI.

    Help/doctor text uses arrows ('→'), box-drawing, and check marks. A Windows
    console defaults stdout to a legacy code page (cp1252) that cannot encode
    '→' (U+2192) → UnicodeEncodeError → the CLI exits non-zero and prints
    nothing. Reconfiguring to UTF-8 with errors='replace' makes every entry
    point emit consistent UTF-8 on all platforms. No-op where reconfigure is
    unavailable (e.g. a captured/replaced stream under pytest)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def linux_pkg_manager():
    """Return 'apt' | 'dnf' | 'pacman' | 'zypper' | 'apk' | None.

    Reads /etc/os-release ID + ID_LIKE for portability across derivatives:
    Debian/Ubuntu/Mint/Pop/Raspbian → apt
    Fedora/RHEL/CentOS/Rocky/Alma → dnf
    Arch/Manjaro/EndeavourOS → pacman
    openSUSE/SLES → zypper
    Alpine → apk

    Falls back to PATH lookup when /etc/os-release is unreadable.
    """
    if not is_linux():
        return None

    ids = set()
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if k == "ID":
                ids.add(v)
            elif k == "ID_LIKE":
                ids.update(v.split())
    except (FileNotFoundError, OSError):
        pass

    apt_like = {"debian", "ubuntu", "mint", "pop", "raspbian"}
    dnf_like = {"fedora", "rhel", "centos", "rocky", "almalinux"}
    pacman_like = {"arch", "manjaro", "endeavouros"}
    zypper_like = {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "suse"}
    apk_like = {"alpine"}

    # /etc/os-release wins when present — it's the authoritative source. PATH
    # lookup is only a fallback for hosts with no /etc/os-release at all.
    # (Bug: a Fedora dev who happened to install apt as a downloader would
    #  otherwise be classified as apt-based.)
    if ids:
        if ids & apt_like:
            return "apt"
        if ids & dnf_like:
            return "dnf"
        if ids & pacman_like:
            return "pacman"
        if ids & zypper_like:
            return "zypper"
        if ids & apk_like:
            return "apk"
        return None  # known os-release but unrecognized — better None than wrong

    # No usable os-release → fall back to PATH-order detection.
    for mgr in ("apt", "dnf", "pacman", "zypper", "apk"):
        if shutil.which(mgr):
            return mgr
    return None


def sudo_prefix():
    """Return ['sudo'] if needed and available, [] if root, None if missing.

    Same shape as the legacy `_sudo_prefix` in bootstrap.py — preserved so
    callers can detect "we need root but can't get it" via `is None`.
    """
    if not is_linux():
        return []
    try:
        if os.geteuid() == 0:
            return []
    except AttributeError:
        return []
    if shutil.which("sudo") is None:
        return None
    return ["sudo"]


# Per-manager package names. Other modules import these directly when they
# need a specific tool, rather than re-deriving the mapping each time.
LINUX_ADB_PKGS = {
    "apt": ["android-tools-adb"],
    "dnf": ["android-tools"],
    "pacman": ["android-tools"],
    "zypper": ["android-tools"],
    "apk": ["android-tools"],
}
LINUX_NODE_PKGS = {
    "apt": ["nodejs", "npm"],
    "dnf": ["nodejs", "npm"],
    "pacman": ["nodejs", "npm"],
    "zypper": ["nodejs", "npm"],
    "apk": ["nodejs", "npm"],
}
LINUX_LIBIMOBILEDEVICE_PKGS = {
    "apt": ["libimobiledevice6", "libimobiledevice-utils", "usbmuxd"],
    "dnf": ["libimobiledevice", "libimobiledevice-utils", "usbmuxd"],
    "pacman": ["libimobiledevice", "usbmuxd"],
    "zypper": ["libimobiledevice", "libimobiledevice-tools", "usbmuxd"],
    "apk": ["libimobiledevice"],
}


# winget package ids for doctor/bootstrap remediation on Windows. Keyed by the
# (first word of the) brew package name callers already pass to install_hint.
WINGET_IDS = {
    "android-platform-tools": "Google.PlatformTools",
    "node": "OpenJS.NodeJS.LTS",
}


_UNSET = object()


def linux_install_cmd(pkgs_per_manager, manager=_UNSET, prefix=_UNSET):
    """Build the install argv for the current Linux distro, or None.

    `pkgs_per_manager` maps manager name → list[str] of package names. Use
    the module constants (LINUX_ADB_PKGS, LINUX_NODE_PKGS, …) where they fit.

    Returns None when:
      - No supported package manager detected (and manager= not passed)
      - Sudo needed but unavailable
      - This manager has no entry in the supplied map

    When `manager` is explicitly passed, the host-platform check is skipped
    (callers who know what they want — e.g. unit tests, dry-run plan
    generation — bypass auto-detection). When `prefix` is passed, sudo
    auto-detection is skipped (use `[]` for "no prefix", `None` for "no
    sudo available — return None").

    Example::

        argv = linux_install_cmd(LINUX_ADB_PKGS)
        # ['sudo', 'apt', 'install', '-y', 'android-tools-adb']
    """
    if manager is _UNSET:
        pm = linux_pkg_manager()
    else:
        # Explicit None from caller = "I asked, there's no manager" — honor it.
        pm = manager
    if pm is None or pm not in pkgs_per_manager:
        return None
    if prefix is _UNSET:
        prefix = sudo_prefix()
    if prefix is None:
        return None
    pkgs = pkgs_per_manager[pm]
    if pm == "apt":
        return prefix + ["apt", "install", "-y", *pkgs]
    if pm == "dnf":
        return prefix + ["dnf", "install", "-y", *pkgs]
    if pm == "pacman":
        return prefix + ["pacman", "-S", "--noconfirm", *pkgs]
    if pm == "zypper":
        return prefix + ["zypper", "install", "-y", *pkgs]
    if pm == "apk":
        return prefix + ["apk", "add", *pkgs]
    return None


class OCRNotAvailableError(RuntimeError):
    """Raised when ocr() is invoked on a host that has no OCR backend.

    Currently macOS is the only platform with a bundled OCR backend
    (Apple Vision via PyObjC). Linux + other hosts must install Tesseract
    or another OCR engine themselves. The error message points at
    SETUP.md for the install path.
    """


def install_hint(brew_pkg: str, pkgs_per_manager: dict) -> str:
    """One-line install command for the current host.

    On macOS → `brew install {brew_pkg}` (the original behavior).
    On Windows → the winget command when the package has a known id.
    On Linux → the apt/dnf/pacman/zypper/apk command for the detected pkg
    manager, or a multi-line list of all five if the manager is unknown.

    Used in doctor remediation messages so Linux/Windows users don't see
    `brew install …` they can't act on.
    """
    if is_macos():
        return f"brew install {brew_pkg}"
    if is_windows():
        wid = WINGET_IDS.get(brew_pkg.split()[0])
        if wid:
            return f"winget install --id {wid}"
        return f"(install {brew_pkg} via winget or your package manager)"
    if not is_linux():
        return f"(install {brew_pkg} via your OS package manager)"
    pm = linux_pkg_manager()
    if pm and pm in pkgs_per_manager:
        pkgs = " ".join(pkgs_per_manager[pm])
        if pm == "apt":
            return f"sudo apt install {pkgs}"
        if pm == "dnf":
            return f"sudo dnf install {pkgs}"
        if pm == "pacman":
            return f"sudo pacman -S {pkgs}"
        if pm == "zypper":
            return f"sudo zypper install {pkgs}"
        if pm == "apk":
            return f"sudo apk add {pkgs}"
    # Unknown distro — show all known managers so the user can pick.
    lines = ["install via your distro's package manager — options:"]
    for mgr in ("apt", "dnf", "pacman", "zypper", "apk"):
        if mgr in pkgs_per_manager:
            pkgs = " ".join(pkgs_per_manager[mgr])
            verb = {"apt": "apt install", "dnf": "dnf install",
                    "pacman": "pacman -S", "zypper": "zypper install",
                    "apk": "apk add"}[mgr]
            lines.append(f"     - {mgr}: sudo {verb} {pkgs}")
    return "\n".join(lines)


def host_os_label() -> str:
    """Short label for the current host. Used in logs and doctor headers."""
    if is_macos():
        return "macOS"
    if is_linux():
        pm = linux_pkg_manager()
        return f"Linux ({pm})" if pm else "Linux (unknown pkg manager)"
    return sys.platform
