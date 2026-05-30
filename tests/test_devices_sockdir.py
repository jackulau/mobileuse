"""D13 — `devices status/reload/view` must scan the daemons' real socket dir.

devices.py resolved the socket dir from TMPDIR, but the daemons place sockets
via IPH/ANH_RUNTIME_DIR (else /tmp) and never consult TMPDIR. So on default
macOS (TMPDIR=/var/folders/..., daemons write /tmp) or with a custom
RUNTIME_DIR, `devices` scanned the wrong directory and reported "No named
daemons running" against live daemons.
"""
import mobile_use.devices as devices
from android_harness import _ipc as anh_ipc
from iphone_harness import _ipc as iph_ipc


def test_devices_scans_same_dir_daemon_writes(monkeypatch):
    monkeypatch.setenv("IPH_RUNTIME_DIR", "/tmp/mu-d13-test")
    # devices must look where the iOS daemon actually puts its socket.
    scanned = [str(d) for d in devices._socket_dirs()]
    sock = str(iph_ipc._sock_path("x"))
    assert any(sock.startswith(d.rstrip("/") + "/") for d in scanned), (
        f"devices scans {scanned} but daemon writes {sock}"
    )


def test_devices_does_not_use_tmpdir(monkeypatch):
    # Even with a misleading TMPDIR, devices follows the daemon's resolution.
    monkeypatch.setenv("TMPDIR", "/var/folders/zz/bogus")
    monkeypatch.delenv("IPH_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("IPH_TMP_DIR", raising=False)
    monkeypatch.delenv("ANH_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("ANH_TMP_DIR", raising=False)
    scanned = [str(d) for d in devices._socket_dirs()]
    assert "/var/folders/zz/bogus" not in scanned
    assert "/tmp" in scanned


def test_devices_finds_socket_in_custom_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IPH_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ANH_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "iph.sock").touch()
    (tmp_path / "anh-pixel.sock").touch()
    monkeypatch.setattr(devices, "_probe_daemon", lambda *a: False)
    found = devices.list_running_daemons()
    pairs = sorted((d["platform"], d["name"] or "") for d in found)
    assert pairs == [("android", "pixel"), ("ios", "")]


def test_socket_dirs_dedupes_when_same(monkeypatch):
    monkeypatch.setenv("IPH_RUNTIME_DIR", "/tmp/shared")
    monkeypatch.setenv("ANH_RUNTIME_DIR", "/tmp/shared")
    assert [str(d) for d in devices._socket_dirs()] == ["/tmp/shared"]
