"""MCP server — handshake, tools/list, tools/call, errors. All device-free:
the AgentLoop is replaced by fakes; no network, no real daemon."""
import base64
import json
import sys
import types

import pytest

from mobile_use.mcp_server import MCPServer, _tool_input_schema


def _fake_helpers(tmp_path):
    m = types.ModuleType("fake_helpers")

    def tap(x, y):
        return True

    def type_text(text, submit=False):
        return True

    def screenshot(path=None):
        p = tmp_path / "shot.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")
        return str(p)

    def ui_tree():
        return []

    def active_app():
        return {"bundleId": "x"}

    def window_size():
        return {"width": 390, "height": 844}

    def alert():
        return None

    def auto_dismiss_dialog():
        return False

    for fn in (tap, type_text, screenshot, ui_tree, active_app, window_size,
               alert, auto_dismiss_dialog):
        setattr(m, fn.__name__, fn)
    return m


def _server(monkeypatch, tmp_path):
    """MCPServer with a fully faked agent loop (no platform load)."""
    import iphone_harness
    fake_h = _fake_helpers(tmp_path)
    fake_a = types.ModuleType("fake_admin")
    fake_a.ensure_daemon = lambda *a, **kw: True
    monkeypatch.setitem(sys.modules, "iphone_harness.helpers", fake_h)
    monkeypatch.setitem(sys.modules, "iphone_harness.admin", fake_a)
    monkeypatch.setattr(iphone_harness, "helpers", fake_h, raising=False)
    monkeypatch.setattr(iphone_harness, "admin", fake_a, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return MCPServer(platform="ios")


def _rpc(server, method, params=None, rid=1):
    line = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                       "params": params or {}})
    out = server.handle_line(line)
    return json.loads(out) if out else None


# ---- handshake ----------------------------------------------------------------

def test_initialize_returns_protocol_and_serverinfo(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "initialize")
    assert resp["result"]["protocolVersion"]
    assert resp["result"]["serverInfo"]["name"] == "mobile-use"
    assert "tools" in resp["result"]["capabilities"]


def test_ping_returns_empty_result(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "ping")
    assert resp["result"] == {}


def test_notifications_get_no_response(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert s.handle_line(line) is None


# ---- tools/list -----------------------------------------------------------------

def test_tools_list_exposes_curated_verbs_with_schemas(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "tools/list")
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert "tap" in tools
    assert "type_text" in tools
    assert "screenshot" in tools
    assert "devices_list" in tools
    tap_schema = tools["tap"]["inputSchema"]
    assert tap_schema["type"] == "object"
    assert tap_schema["properties"]["x"] == {"type": "number"}
    assert "x" in tap_schema.get("required", [])
    # Uncurated module attrs are NOT exposed as tools.
    assert "ui_tree" not in tools


def test_tool_input_schema_types():
    def fake(x, y, text="hi", submit=False):
        pass
    schema = _tool_input_schema(fake)
    assert schema["properties"]["x"]["type"] == "number"
    assert schema["properties"]["text"]["type"] == "string"
    assert schema["properties"]["submit"]["type"] == "boolean"
    assert set(schema["required"]) == {"x", "y"}


# ---- tools/call -----------------------------------------------------------------

def test_tools_call_routes_to_helper(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "tools/call", {"name": "tap", "arguments": {"x": 10, "y": 20}})
    content = resp["result"]["content"]
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"]) == {"result": True}


def test_tools_call_screenshot_returns_image_content(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "tools/call", {"name": "screenshot"})
    content = resp["result"]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["mimeType"] == "image/png"
    assert base64.b64decode(content[0]["data"]).startswith(b"\x89PNG")


def test_tools_call_devices_list(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    import mobile_use.devices as devices
    monkeypatch.setattr(devices, "discover_connected",
                        lambda: [{"platform": "android", "udid": "X",
                                  "name": "pixel", "transport": "usb"}])
    resp = _rpc(s, "tools/call", {"name": "devices_list"})
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data[0]["udid"] == "X"


def test_tools_call_unknown_tool_is_error_result(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "tools/call", {"name": "rm_rf_slash"})
    assert resp["result"]["isError"] is True
    assert "unknown tool" in resp["result"]["content"][0]["text"]


def test_tools_call_destructive_refused_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MU_ALLOW_DESTRUCTIVE", raising=False)
    s = _server(monkeypatch, tmp_path)
    import mobile_use.agent_loop as al
    monkeypatch.setattr(al, "ACTION_VERBS", [*al.ACTION_VERBS, "uninstall_app"])
    resp = _rpc(s, "tools/call", {"name": "uninstall_app",
                                  "arguments": {"bundle_id": "com.x"}})
    assert resp["result"]["isError"] is True
    assert "MU_ALLOW_DESTRUCTIVE" in resp["result"]["content"][0]["text"]


def test_tools_call_bad_args_error_result(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "tools/call", {"name": "tap",
                                  "arguments": {"x": "ten", "y": 20}})
    assert resp["result"]["isError"] is True


# ---- protocol errors ----------------------------------------------------------------

def test_malformed_line_is_parse_error(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = json.loads(s.handle_line("{this is not json"))
    assert resp["error"]["code"] == -32700


def test_unknown_method_is_method_not_found(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    resp = _rpc(s, "resources/list")
    assert resp["error"]["code"] == -32601


def test_blank_line_ignored(monkeypatch, tmp_path):
    s = _server(monkeypatch, tmp_path)
    assert s.handle_line("   \n") is None


# ---- serve_stdio ---------------------------------------------------------------------

def test_serve_stdio_round_trip(monkeypatch, tmp_path):
    import io
    s = _server(monkeypatch, tmp_path)
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    s.serve_stdio(stdin=stdin, stdout=stdout)
    lines = [json.loads(x) for x in stdout.getvalue().splitlines()]
    assert len(lines) == 2  # notification produced no output
    assert lines[0]["id"] == 1
    assert lines[1]["id"] == 2


# ---- cli routing ---------------------------------------------------------------------

def test_cli_routes_mcp(monkeypatch):
    import mobile_use.cli as cli
    import mobile_use.mcp_server as mcp_server
    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(mcp_server, "main", fake_main)
    monkeypatch.setattr(sys, "argv",
                        ["mobile-use", "--android", "--name", "px1", "mcp"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 0
    assert seen["argv"] == ["--android", "--name", "px1"]
