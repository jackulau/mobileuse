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
import os
import shutil
import sys
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
        "Docs: see SETUP.md -> 'iOS from Windows / Linux (remote Mac bridge)'."
    )


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
        for line in Path("/etc/os-release").read_text().splitlines():
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

    if ids & apt_like or shutil.which("apt"):
        return "apt"
    if ids & dnf_like or shutil.which("dnf"):
        return "dnf"
    if ids & pacman_like or shutil.which("pacman"):
        return "pacman"
    if ids & zypper_like or shutil.which("zypper"):
        return "zypper"
    if ids & apk_like or shutil.which("apk"):
        return "apk"
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


_UNSET = object()


def linux_install_cmd(pkgs_per_manager, manager=None, prefix=_UNSET):
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
    pm = manager or linux_pkg_manager()
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


def host_os_label() -> str:
    """Short label for the current host. Used in logs and doctor headers."""
    if is_macos():
        return "macOS"
    if is_linux():
        pm = linux_pkg_manager()
        return f"Linux ({pm})" if pm else "Linux (unknown pkg manager)"
    return sys.platform
