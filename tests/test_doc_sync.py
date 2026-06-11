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


# ---- goal/022: steady-state speed knobs tie docs to code -----------------------

def test_env_example_documents_steady_state_vars():
    for var in ("MU_PREACT_DISMISS", "IPH_GESTURE_SETTLE", "ANH_GESTURE_SETTLE",
                "IPH_ENSURE_TTL", "ANH_ENSURE_TTL",
                "MU_COLLECT_TREE", "MU_COLLECT_TREE_MAX"):
        assert var in ENV, f".env.example missing {var}"


def test_steady_state_vars_exist_in_code():
    # Every documented knob must be read by shipped code (no doc-only env vars).
    agent_loop = (ROOT / "mobile_use" / "agent_loop.py").read_text(encoding="utf-8")
    collector = (ROOT / "mobile_use" / "collector.py").read_text(encoding="utf-8")
    iph_h = (ROOT / "iphone_harness" / "helpers.py").read_text(encoding="utf-8")
    anh_h = (ROOT / "android_harness" / "helpers.py").read_text(encoding="utf-8")
    iph_a = (ROOT / "iphone_harness" / "admin.py").read_text(encoding="utf-8")
    anh_a = (ROOT / "android_harness" / "admin.py").read_text(encoding="utf-8")
    assert "MU_PREACT_DISMISS" in agent_loop
    assert "MU_COLLECT_TREE" in collector and "MU_COLLECT_TREE_MAX" in collector
    assert "IPH_GESTURE_SETTLE" in iph_h
    assert "ANH_GESTURE_SETTLE" in anh_h
    assert "IPH_ENSURE_TTL" in iph_a
    assert "ANH_ENSURE_TTL" in anh_a


def test_docs_document_fast_test_lane():
    assert "pytest -n auto" in README
    assert "pytest -n auto" in SETUP
    assert "MU_PREACT_DISMISS" in README and "MU_PREACT_DISMISS" in SETUP


def test_setup_documents_detection_layers():
    assert "MU_LOCAL_SHORTCIRCUIT" in SETUP
    assert "polars-lts-cpu" in SETUP            # the older-CPU training gotcha
    assert "[yolo]" in SETUP and "[detection]" in SETUP


# ---- D9: self-validation + compatibility surface docs tie to code --------------

def test_docs_document_selfcheck_command():
    assert "selfcheck" in README
    assert "selfcheck" in SETUP
    assert "selfcheck" in CLI                   # advertised in the shipped HELP block


def test_setup_documents_offline_base_model_and_self_validation():
    assert "offline" in SETUP.lower()
    assert "yolov8n.pt" in SETUP                # the committed base-model copy
    assert "trained_unverified" in SETUP        # the honest post-train status


# ---- goal/023: competitive story + new commands/env documented ----------------

def _read(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_comparison_doc_names_all_competitors():
    t = _read("docs/comparison.md")
    for k in ("Appium", "Maestro", "mobile-mcp", "DroidRun", "AppAgent", "scrcpy"):
        assert k in t, f"comparison.md must cover {k}"
    # Honesty requirement: the doc admits where others win.
    assert "Where others win" in t


def test_comparison_claims_name_shipping_surface():
    """Every flagship claim names a real command/module that exists."""
    t = _read("docs/comparison.md")
    for surface in ("mobile-use bootstrap", "mobile-use mcp", "wifi reconnect",
                    "mobile_use/wifi_store.py", "mobile_use/multibox.py",
                    "Dockerfile.linux-test", "install-wda"):
        assert surface in t, f"comparison.md must name {surface}"


def test_readme_links_comparison_and_warns_pypi():
    t = _read("README.md")
    assert "docs/comparison.md" in t
    assert "DIFFERENT" in t and "PyPI" in t, "PyPI name-collision warning required"


def test_new_env_vars_documented_and_read_by_code():
    """Two-directional: each new env var is in .env.example AND read by code."""
    env_example = _read(".env.example")
    code = (_read("mobile_use/wifi_store.py") + _read("mobile_use/agent_loop.py")
            + _read("iphone_harness/_ipc.py") + _read("android_harness/_ipc.py")
            + _read("mobile_use/cli.py"))
    for var in ("MU_WIFI_STORE", "MU_ALLOW_DESTRUCTIVE", "IPH_TOKEN", "ANH_TOKEN",
                "MOBILE_USE_VIEWER_READONLY"):
        assert var in env_example, f"{var} missing from .env.example"
        assert var in code, f"{var} documented but not read by code"


def test_cli_help_advertises_new_commands():
    from mobile_use.cli import HELP
    for cmd in ("android pair", "wifi reconnect", "devices remembered",
                "ios install-wda", "mobile-use mcp"):
        assert cmd in HELP, f"HELP must list {cmd}"


def test_setup_covers_new_surfaces():
    t = _read("SETUP.md")
    for k in ("android pair", "wifi reconnect", "devices remembered",
              "mcpServers", "mobile-use[agent]", "install-wda", "--read-only"):
        assert k in t, f"SETUP.md must cover {k}"
    assert "brew install uv" not in t, "unused uv install must stay gone"
