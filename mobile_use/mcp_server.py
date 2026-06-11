"""`mobile-use mcp` — dependency-free stdio MCP server.

Exposes the curated device-action surface (agent_loop.ACTION_VERBS, dispatched
through the hardened AgentLoop.act path — allowlist + destructive gate) to any
MCP client (Claude Desktop/Code, Cursor, ...) as tools, plus `devices_list`
and a `screenshot` tool that returns real MCP image content.

Protocol: JSON-RPC 2.0, newline-delimited over stdio (MCP 2024-11-05).
No SDK dependency — the server is ~lines of stdlib so `pip install` of the
base package is all an MCP client needs. STDOUT carries only JSON-RPC;
diagnostics go to stderr.

Client config (Claude Desktop / Code):
    {"mcpServers": {"mobile-use": {"command": "mobile-use", "args": ["mcp", "--android"]}}}
Multi-device: add "--name", "<device>" (a named daemon, see `mobile-use devices`).
"""
import base64
import inspect
import json
import os
import sys

PROTOCOL_VERSION = "2024-11-05"

_NUMERIC_PARAMS = {"x", "y", "x1", "y1", "x2", "y2", "duration", "seconds",
                   "timeout", "count", "amount", "scale", "velocity"}


def _server_version():
    try:
        from importlib.metadata import version
        return version("mobile-use")
    except Exception:
        return "dev"


def _param_schema(name, param):
    if name in _NUMERIC_PARAMS:
        return {"type": "number"}
    d = param.default
    if isinstance(d, bool):
        return {"type": "boolean"}
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        return {"type": "number"}
    return {"type": "string"}


def _tool_input_schema(fn):
    """JSON Schema for a helper's kwargs, from its signature."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}
    props, required = {}, []
    for n, p in sig.parameters.items():
        if n == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        props[n] = _param_schema(n, p)
        if p.default is p.empty:
            required.append(n)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


class MCPServer:
    """One MCP session over stdio. Testable line-at-a-time: handle_line()
    returns the JSON response string, or None for notifications."""

    def __init__(self, platform=None, name=None):
        self._platform = platform
        self._device_name = name
        self._loop = None

    # ---- device plumbing ------------------------------------------------

    def _agent(self):
        if self._loop is None:
            if self._device_name:
                os.environ.setdefault("IPH_NAME", self._device_name)
                os.environ.setdefault("ANH_NAME", self._device_name)
            platform = self._platform
            if platform is None:
                from mobile_use.cli import _detect_platform
                platform = _detect_platform()
            if platform is None:
                raise RuntimeError(
                    "no platform: pass --ios or --android to `mobile-use mcp` "
                    "(auto-detect found no/ambiguous devices)")
            from mobile_use.agent_loop import AgentLoop
            self._loop = AgentLoop(platform=platform, session_name="mcp",
                                   collect=False)
        return self._loop

    # ---- tool surface -----------------------------------------------------

    def _tools(self):
        loop = self._agent()
        actions = loop.get_available_actions()
        loop._load_platform()
        h = loop._helpers
        tools = []
        for verb, meta in sorted(actions.items()):
            fn = getattr(h, verb, None)
            tools.append({
                "name": verb,
                "description": (meta.get("doc") or verb).strip()
                               or f"device action {verb}",
                "inputSchema": _tool_input_schema(fn) if fn else
                               {"type": "object", "properties": {}},
            })
        tools.append({
            "name": "screenshot",
            "description": "Capture the device screen; returns a PNG image.",
            "inputSchema": {"type": "object", "properties": {}},
        })
        tools.append({
            "name": "devices_list",
            "description": "List connected iOS + Android devices "
                           "(udid, name, transport).",
            "inputSchema": {"type": "object", "properties": {}},
        })
        return tools

    def _call_tool(self, tool, arguments):
        """Returns the MCP tools/call result object."""
        if tool == "devices_list":
            from mobile_use.devices import discover_connected
            return {"content": [{"type": "text",
                                 "text": json.dumps(discover_connected())}]}
        if tool == "screenshot":
            loop = self._agent()
            loop._load_platform()
            path = loop._helpers.screenshot()
            try:
                data = base64.b64encode(open(path, "rb").read()).decode()
            except OSError as e:
                return {"isError": True,
                        "content": [{"type": "text",
                                     "text": f"screenshot failed: {e}"}]}
            return {"content": [{"type": "image", "data": data,
                                 "mimeType": "image/png"}]}

        loop = self._agent()
        from mobile_use.agent_loop import ACTION_VERBS
        if tool not in ACTION_VERBS:
            return {"isError": True,
                    "content": [{"type": "text",
                                 "text": f"unknown tool: {tool!r}"}]}
        out = loop.act(tool, **(arguments or {}))
        if "error" in out:
            return {"isError": True,
                    "content": [{"type": "text", "text": out["error"]}]}
        return {"content": [{"type": "text",
                             "text": json.dumps(out, default=str)}]}

    # ---- JSON-RPC ----------------------------------------------------------

    def _result(self, rid, result):
        return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})

    def _error(self, rid, code, message):
        return json.dumps({"jsonrpc": "2.0", "id": rid,
                           "error": {"code": code, "message": message}})

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return None
        try:
            req = json.loads(line)
        except ValueError:
            return self._error(None, -32700, "parse error: not valid JSON")
        if not isinstance(req, dict):
            return self._error(None, -32600, "invalid request")
        rid = req.get("id")
        method = req.get("method") or ""
        params = req.get("params") or {}

        if method.startswith("notifications/"):
            return None  # notifications get no response
        try:
            if method == "initialize":
                return self._result(rid, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mobile-use",
                                   "version": _server_version()},
                })
            if method == "ping":
                return self._result(rid, {})
            if method == "tools/list":
                return self._result(rid, {"tools": self._tools()})
            if method == "tools/call":
                name = params.get("name") or ""
                return self._result(
                    rid, self._call_tool(name, params.get("arguments")))
            return self._error(rid, -32601, f"method not found: {method}")
        except Exception as e:
            return self._error(rid, -32603, f"internal error: {e}")

    def serve_stdio(self, stdin=None, stdout=None):
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            resp = self.handle_line(line)
            if resp is not None:
                stdout.write(resp + "\n")
                stdout.flush()


MCP_HELP = """\
mobile-use mcp — serve the device-action surface to MCP clients over stdio.

USAGE:
  mobile-use mcp [--ios|--android] [--name <device>]

Client config (Claude Desktop / Claude Code / Cursor):
  {"mcpServers": {"mobile-use": {"command": "mobile-use", "args": ["mcp", "--android"]}}}

Tools = the curated action set (tap, type_text, swipe, launch_app, ...) plus
screenshot (returns an MCP image) and devices_list. Dispatch goes through the
hardened agent path: hallucinated tools are refused, destructive verbs need
MU_ALLOW_DESTRUCTIVE=1.
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a in {"-h", "--help"} for a in argv):
        print(MCP_HELP)
        return 0
    platform = None
    name = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--ios":
            platform = "ios"
        elif a == "--android":
            platform = "android"
        elif a == "--name" and i + 1 < len(argv):
            name = argv[i + 1]; i += 1
        i += 1
    print(f"mobile-use mcp: serving on stdio (platform={platform or 'auto'}, "
          f"name={name or 'default'})", file=sys.stderr)
    MCPServer(platform=platform, name=name).serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
