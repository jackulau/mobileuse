"""Single source of truth for device-OS + Appium-toolchain version support.

Why this module exists
-----------------------
The harness drives devices through Appium, and Appium's behaviour changes
sharply across OS versions. The biggest cliff is **iOS 17**: Apple replaced the
classic lockdownd service channel with **RemoteXPC** (QUIC over an IPv6 tunnel),
so on iOS 17+ *real* devices Appium must stand up a **tunnel** — its bundled
``appium-ios-remotexpc``, or an external ``pymobiledevice3 remote tunneld`` —
before WebDriverAgent can be reached at all, over USB *or* Wi-Fi. Miss the
tunnel and commands fail with ``RSDRequired`` / ``InvalidServiceError``.

Nothing in the harness previously knew any of this — ``IPH/ANH_PLATFORM_VERSION``
was passed straight through to Appium. This module is the one place that:

  * normalizes a version string ("18.3.2" -> (18, 3, 2)),
  * holds the tested support ranges (iOS / Android / Appium / drivers),
  * answers "does this iOS need a tunnel?" (``ios_needs_tunnel``),
  * classifies a version as supported / untested-newer / too-old / unknown.

``doctor`` and ``bootstrap`` import these so the version story is computed in
exactly one spot, and the README support matrix is generated from the same
constants (a doc-sync test guards against drift).

References
----------
* iOS 17+ RemoteXPC tunnel requirement — pymobiledevice3 / appium-ios-remotexpc.
* xcuitest-driver >= 10 requires Appium 3; >= 4 requires Appium 2.
* Appium drivers target roughly the latest two major OS versions.
"""
import re

# --- Support matrix (single source of truth) --------------------------------
# "major" = the marketing/SDK major number we key everything off of. Ranges are
# the versions this harness is *tested* against; newer is "probably fine, just
# untested" and older is "below what we verify". Keep these conservative and
# bump them as new OS versions are validated — the README table and the
# doctor summary both read straight from here.

IOS_MIN_MAJOR = 15          # oldest iOS we verify against
IOS_MAX_MAJOR = 26          # newest iOS validated (latest at time of writing)
IOS_TUNNEL_MIN_MAJOR = 17   # iOS >= 17 needs the RemoteXPC tunnel (USB or Wi-Fi)

ANDROID_MIN_MAJOR = 8       # Android 8 (API 26) — UiAutomator2 floor we verify
ANDROID_MAX_MAJOR = 16      # newest Android validated

# Appium server. xcuitest-driver >= 10 requires Appium 3, so 3.x is recommended,
# but Appium 2.x still works with older drivers.
APPIUM_MIN = (2, 0, 0)
APPIUM_RECOMMENDED_MAJOR = 3

# Appium drivers. Mins are the oldest we verify; "latest" is always recommended.
XCUITEST_MIN = (5, 0, 0)        # appium driver install xcuitest
XCUITEST_APPIUM3_MIN = (10, 0, 0)  # >= 10 is the Appium-3-only line
UIAUTOMATOR2_MIN = (3, 0, 0)    # appium driver install uiautomator2

# Levels returned by version_support_status / appium_support_status.
SUPPORTED = "supported"
UNTESTED_NEWER = "untested-newer"
TOO_OLD = "too-old"
UNKNOWN = "unknown"


def normalize_version(value):
    """Parse a version string into a tuple of ints.

    Lenient on purpose — accepts bare majors, dotted versions, and strings with
    a leading label, returning the first dotted-numeric run found:

        "18.3.2"   -> (18, 3, 2)
        "14"       -> (14,)
        "26.0"     -> (26, 0)
        "iOS 17.1" -> (17, 1)
        ""/None    -> ()
        "latest"   -> ()

    Returns an empty tuple when nothing numeric is present, so callers can treat
    a missing/garbage version as "unknown" without try/except.
    """
    if value is None:
        return ()
    m = re.search(r"\d+(?:\.\d+)*", str(value))
    if not m:
        return ()
    return tuple(int(part) for part in m.group(0).split("."))


def major(value):
    """Return the major component of a version string, or None if unparseable."""
    nv = normalize_version(value)
    return nv[0] if nv else None


def ios_needs_tunnel(value):
    """True if this iOS version needs the RemoteXPC tunnel (iOS >= 17).

    Accepts a version string ("18.3.2"), a bare major int (18), or a tuple.
    Returns False for anything unparseable — "don't claim a tunnel is needed
    unless we actually know the version is >= 17".
    """
    if isinstance(value, int):
        m = value
    elif isinstance(value, (tuple, list)) and value:
        m = value[0]
    else:
        m = major(value)
    return m is not None and m >= IOS_TUNNEL_MIN_MAJOR


def _os_status(label, m, min_major, max_major):
    """Shared classify logic for an OS major number against a tested range."""
    if m is None:
        return UNKNOWN, f"could not parse a {label} version"
    if m < min_major:
        return TOO_OLD, (
            f"{label} {m} is below the minimum tested major ({label} {min_major})"
        )
    if m > max_major:
        return UNTESTED_NEWER, (
            f"{label} {m} is newer than the latest tested major ({label} {max_major}) "
            f"— likely works, but untested here"
        )
    return SUPPORTED, f"{label} {m} is within the tested range ({label} {min_major}-{max_major})"


def version_support_status(platform, value):
    """Classify a device OS version. Returns ``(level, detail)``.

    ``platform`` is "ios" or "android". For iOS, the tunnel requirement is
    appended to the detail when applicable so a single call answers both
    "is it supported?" and "does it need a tunnel?".
    """
    p = (platform or "").lower()
    m = major(value)
    if p == "ios":
        level, detail = _os_status("iOS", m, IOS_MIN_MAJOR, IOS_MAX_MAJOR)
        if ios_needs_tunnel(m):
            detail += " — needs the RemoteXPC tunnel (USB or Wi-Fi)"
        return level, detail
    if p == "android":
        return _os_status("Android", m, ANDROID_MIN_MAJOR, ANDROID_MAX_MAJOR)
    return UNKNOWN, f"unknown platform {platform!r}"


def appium_support_status(value):
    """Classify an Appium *server* version. Returns ``(level, detail)``."""
    nv = normalize_version(value)
    if not nv:
        return UNKNOWN, "could not parse an Appium version"
    # Pad to 3 components for a clean tuple compare.
    padded = (nv + (0, 0, 0))[:3]
    if padded < APPIUM_MIN:
        return TOO_OLD, (
            f"Appium {'.'.join(map(str, nv))} is below the minimum "
            f"({'.'.join(map(str, APPIUM_MIN))}); upgrade with `npm i -g appium`"
        )
    if padded[0] < APPIUM_RECOMMENDED_MAJOR:
        return SUPPORTED, (
            f"Appium {'.'.join(map(str, nv))} works; Appium {APPIUM_RECOMMENDED_MAJOR}.x "
            f"is recommended (required by xcuitest-driver >= "
            f"{'.'.join(map(str, XCUITEST_APPIUM3_MIN))})"
        )
    return SUPPORTED, f"Appium {'.'.join(map(str, nv))} is recommended"


def support_matrix_rows():
    """Rows for the support matrix, as ``(component, supported, notes)`` tuples.

    Used by both the doctor summary and the README table generator so there is
    exactly one description of what we support.
    """
    return [
        (
            "iOS",
            f"{IOS_MIN_MAJOR} – {IOS_MAX_MAJOR}",
            f"iOS >= {IOS_TUNNEL_MIN_MAJOR} needs the RemoteXPC tunnel (USB or Wi-Fi)",
        ),
        (
            "Android",
            f"{ANDROID_MIN_MAJOR} – {ANDROID_MAX_MAJOR}",
            "UiAutomator2; Wi-Fi via `mobile-use android wifi <ip>`",
        ),
        (
            "Appium server",
            f">= {'.'.join(map(str, APPIUM_MIN))}",
            f"{APPIUM_RECOMMENDED_MAJOR}.x recommended",
        ),
        (
            "xcuitest-driver",
            f">= {'.'.join(map(str, XCUITEST_MIN))}",
            f">= {'.'.join(map(str, XCUITEST_APPIUM3_MIN))} requires Appium {APPIUM_RECOMMENDED_MAJOR}",
        ),
        (
            "uiautomator2-driver",
            f">= {'.'.join(map(str, UIAUTOMATOR2_MIN))}",
            "Android driver",
        ),
    ]


def support_matrix_text():
    """Plain-text support matrix for the doctor output (no markdown)."""
    rows = support_matrix_rows()
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    lines = ["Supported versions:"]
    for comp, supported, notes in rows:
        lines.append(f"  {comp.ljust(w0)}  {supported.ljust(w1)}  {notes}")
    return "\n".join(lines)
