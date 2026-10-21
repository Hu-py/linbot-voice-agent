"""Minimal synchronous HTTP MCP client (2025-06-18) used for tests and the LLM agent.

Only implements the EDU contract subset used here: initialize,
notifications/initialized, tools/list, tools/call. No stdio, Resources or
Prompts. Plain urllib, no third-party dependency.
"""
import json
import urllib.request
import urllib.error

PROTOCOL_VERSION = "2025-06-18"


class McpProtocolError(RuntimeError):
    """Raised for transport or JSON-RPC error envelopes."""


class McpCallError(RuntimeError):
    """Raised when tools/call returns isError=true."""

    def __init__(self, code, message, tool=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tool = tool


class McpClient:
    def __init__(self, url, timeout=10.0):
        self.url = url
        self.timeout = timeout
        self.session_id = None
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def _post(self, payload, expect_notification=False):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        resp_headers = {}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
                resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            status = exc.code
            resp_headers = dict(exc.headers)
        if expect_notification:
            return status, resp_headers, raw
        if not raw.strip():
            raise McpProtocolError("空响应")
        body = json.loads(raw)
        if "error" in body:
            err = body["error"]
            raise McpProtocolError(f"[{err.get('code')}] {err.get('message')}")
        return status, resp_headers, body

    def initialize(self, client_name="linbot-test", client_version="1.0"):
        _, resp_headers, body = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        })
        result = body["result"]
        if result["protocolVersion"] != PROTOCOL_VERSION:
            raise McpProtocolError("协议版本协商失败")
        sid = resp_headers.get("Mcp-Session-Id") or resp_headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return result

    def notify_initialized(self):
        status, _, _ = self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                  expect_notification=True)
        return status

    def list_tools(self):
        _, _, body = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {},
        })
        return body["result"]["tools"]

    def call_tool(self, name, arguments=None):
        _, _, body = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        result = body["result"]
        if result.get("isError"):
            detail = json.loads(result["content"][0]["text"])
            raise McpCallError(detail.get("error_code"), detail.get("message"),
                               detail.get("tool"))
        text = result["content"][0]["text"]
        return json.loads(text)

    # Convenience lifecycle used by tests and scripts.
    def connect(self, client_name="linbot-test"):
        self.initialize(client_name)
        self.notify_initialized()
        return self
