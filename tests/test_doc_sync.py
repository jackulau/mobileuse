"""D6 — doc/code drift guard.

The README support matrix and the wireless docs must stay in lockstep with
mobile_use/versions.py and the new env vars / CLI commands. If someone bumps a
supported version in code but forgets the README (or vice-versa), these fail.
"""
from pathlib import Path

from mobile_use import versions as V

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SETUP = (ROOT / "SETUP.md").read_text(encoding="utf-8")
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_readme_cites_every_support_matrix_row():
    # The exact "supported" cell of each row must appear verbatim in the README
    # table — this is what ties the doc to versions.py's constants.
    for component, supported, _notes in V.support_matrix_rows():
        assert supported in README, f"README missing support range for {component!r}: {supported!r}"


def test_readme_mentions_ios_tunnel_floor():
    # iOS >= 17 tunnel requirement must be documented with the real floor major.
    assert f"iOS >= {V.IOS_TUNNEL_MIN_MAJOR}" in README or f">= {V.IOS_TUNNEL_MIN_MAJOR}" in README
    assert "RemoteXPC" in README
    assert "tunnel" in README.lower()


def test_readme_documents_wireless_surfaces():
    assert "Wireless" in README
    assert "IPH_WDA_URL" in README
    assert "android wifi" in README


def test_env_example_documents_wireless_env():
    assert "IPH_WDA_URL" in ENV
    assert "android wifi" in ENV  # the Android Wi-Fi note


def test_setup_has_wireless_walkthrough():
    low = SETUP.lower()
    assert "wireless" in low
    assert "tunnel" in low
    assert "webdriveragent" in low or "iph_wda_url" in low
    assert "android wifi" in SETUP


def test_doc_sync_ios_max_major_present():
    # A bump of IOS_MAX_MAJOR (e.g. 26 -> 27) must be reflected in the README.
    assert str(V.IOS_MAX_MAJOR) in README
    assert str(V.ANDROID_MAX_MAJOR) in README


# ---- D12: perception speed / local detection feature docs tie to code ----------

CLI = (ROOT / "mobile_use" / "cli.py").read_text(encoding="utf-8")


def test_readme_documents_perception_commands():
    assert "bench-perception" in README
    assert "train-detector" in README


def test_env_example_documents_detector_vars():
    for var in ("MU_LOCAL_DETECTOR", "MU_YOLO_DETECTOR", "MU_DETECTOR_WEIGHTS",
                "MU_LOCAL_SHORTCIRCUIT", "MU_DETECTOR_MIN_CONF"):
        assert var in ENV, f".env.example missing {var}"


def test_cli_help_lists_perception_commands():
    # The HELP block (shipped code) must advertise the subcommands that dispatch.
    assert "bench-perception" in CLI
    assert "train-detector" in CLI


def test_setup_documents_detection_layers():
    assert "MU_LOCAL_SHORTCIRCUIT" in SETUP
    assert "polars-lts-cpu" in SETUP            # the older-CPU training gotcha
    assert "[yolo]" in SETUP and "[detection]" in SETUP
