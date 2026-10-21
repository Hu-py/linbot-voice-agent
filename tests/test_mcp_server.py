"""End-to-end protocol tests for the hospital HTTP MCP server.

Spins the real ThreadingHTTPServer on an ephemeral port and drives it with a plain
HTTP client. No robot, no Aorta, no LLM. Covers discovery, successful calls,
argument errors, unknown tool/session, session isolation, timeout, and protocol
errors.
"""
import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path

from linbot_voice_agent.mcp_server import (
    PROTOCOL_VERSION,
    SessionStore,
    build_default_tools,
    create_server,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "seeds"
BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": PROTOCOL_VERSION,
}


class McpTestBase(unittest.TestCase):
    def setUp(self, tools=None, tool_timeout=5.0):
        self.store = SessionStore(str(SEED_DIR))
        self.httpd = create_server("127.0.0.1", 0, self.store,
                                   tools=tools or build_default_tools(),
                                   tool_timeout=tool_timeout)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def post(self, payload, headers=None, path="/mcp"):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers={**BASE_HEADERS, **(headers or {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read().decode()
                parsed = json.loads(text) if text.strip() else ""
                return resp.status, dict(resp.headers), parsed
        except urllib.error.HTTPError as exc:
            text = exc.read().decode()
            parsed = json.loads(text) if text.strip() else ""
            return exc.code, dict(exc.headers), parsed

    def initialize(self):
        status, headers, body = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1.0"}},
        })
        return status, headers, body

    def notify_initialized(self, session_id=None):
        headers = {"Mcp-Session-Id": session_id} if session_id else {}
        status, _, body = self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
        return status, body

    def call(self, name, arguments=None, session_id=None, call_id=10):
        headers = {"Mcp-Session-Id": session_id} if session_id else {}
        return self.post({
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }, headers)


class LifecycleAndDiscoveryTests(McpTestBase):
    def test_initialize_notification_then_list_tools(self):
        status, headers, body = self.initialize()
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", body["result"]["capabilities"])
        self.assertTrue(body["result"]["serverInfo"]["name"])
        session_id = headers.get("Mcp-Session-Id")
        self.assertTrue(session_id)

        nstatus, nbody = self.notify_initialized(session_id)
        self.assertEqual(nstatus, 202)
        self.assertEqual(nbody, "")

        status, _, body = self.post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, {"Mcp-Session-Id": session_id})
        names = [t["name"] for t in body["result"]["tools"]]
        self.assertEqual(set(names), {"hospital_search", "patient_status", "hospital_next_step"})
        for tool in body["result"]["tools"]:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])

    def test_wrong_protocol_version_rejected(self):
        status, _, body = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "1970-01-01", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}},
        })
        self.assertEqual(body["error"]["code"], -32600)

    def test_non_dict_params_rejected(self):
        _, _, body = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": ["nope"],
        })
        self.assertEqual(body["error"]["code"], -32600)

    def test_max_sessions_enforced(self):
        from linbot_voice_agent.mcp_server import SessionLimitError
        bounded = SessionStore(str(SEED_DIR), max_sessions=1)
        bounded.create()
        with self.assertRaises(SessionLimitError):
            bounded.create()

    def test_malformed_json_and_unknown_method(self):
        # Malformed JSON returns a JSON-RPC parse-error envelope (HTTP 200).
        status, _, body = self.post_raw(b"{not json")
        self.assertEqual(status, 200)
        self.assertEqual(body["error"]["code"], -32700)

        _, _, body = self.post({"jsonrpc": "2.0", "id": 9, "method": "nosuch/method"})
        self.assertEqual(body["error"]["code"], -32601)

    def post_raw(self, raw, headers=None, path="/mcp"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=raw,
            headers={**BASE_HEADERS, **(headers or {})}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), json.loads(exc.read().decode())


class ToolCallTests(McpTestBase):
    def setUp(self):
        super().setUp()
        _, headers, _ = self.initialize()
        self.session_id = headers["Mcp-Session-Id"]
        self.notify_initialized(self.session_id)

    def _text(self, body):
        return json.loads(body["result"]["content"][0]["text"])

    def test_hospital_search_success(self):
        status, _, body = self.call("hospital_search", {"query": "心内科在哪里"}, self.session_id)
        self.assertFalse(body["result"]["isError"])
        payload = self._text(body)
        self.assertEqual(payload["query"], "心内科在哪里")
        self.assertGreaterEqual(payload["count"], 1)
        self.assertTrue(payload["results"][0]["title"])

    def test_patient_status_reads_server_side_state(self):
        _, _, body = self.call("patient_status", {}, self.session_id)
        self.assertFalse(body["result"]["isError"])
        state = self._text(body)
        self.assertTrue(state["synthetic"])
        self.assertIn("facts", state)
        self.assertIn("navigation", state)

    def test_hospital_next_step_returns_deterministic_step(self):
        _, _, body = self.call("hospital_next_step", {}, self.session_id)
        self.assertFalse(body["result"]["isError"])
        step = self._text(body)["next_step"]
        self.assertIn(step["kind"], {"navigate", "event", "blocked", "complete"})

    def test_invalid_arguments_marked_is_error(self):
        cases = [
            ("hospital_search", {}),                      # missing query
            ("hospital_search", {"query": "x", "hack": 1}),  # extra property
            ("hospital_search", {"query": "x", "limit": 99}),  # out of range
            ("hospital_search", {"query": "   "}),         # blank
        ]
        for name, args in cases:
            with self.subTest(args=args):
                _, _, body = self.call(name, args, self.session_id)
                self.assertTrue(body["result"]["isError"])
                self.assertIn("error_code", self._text(body))

    def test_unknown_tool_and_unknown_session(self):
        _, _, body = self.call("does_not_exist", {}, self.session_id)
        self.assertTrue(body["result"]["isError"])
        self.assertEqual(self._text(body)["error_code"], "unknown_tool")

        _, _, body = self.call("patient_status", {}, "fake-session-id")
        self.assertTrue(body["result"]["isError"])
        self.assertEqual(self._text(body)["error_code"], "unknown_session")


class IsolationAndTimeoutTests(McpTestBase):
    def test_two_sessions_keep_independent_environments(self):
        _, h1, _ = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "a", "version": "1"}},
        })
        _, h2, _ = self.post({
            "jsonrpc": "2.0", "id": 2, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "b", "version": "1"}},
        })
        s1, s2 = h1["Mcp-Session-Id"], h2["Mcp-Session-Id"]
        self.assertNotEqual(s1, s2)
        self.assertEqual(len(self.store), 2)
        env1 = self.store.get(s1).env
        env2 = self.store.get(s2).env
        self.assertIsNot(env1, env2)
        # Same starting snapshot, but distinct objects (no shared mutable state).
        self.assertEqual(env1.patient.id, env2.patient.id)
        self.assertIsNot(env1.patient, env2.patient)

    def test_tool_timeout_produces_is_error(self):
        def slow_handler(_env, _args):
            time.sleep(2.0)
            return {}

        tools = build_default_tools()
        tools["hospital_search"] = (tools["hospital_search"][0], slow_handler)
        self.tearDown()
        super().setUp(tools=tools, tool_timeout=0.3)
        _, h, _ = self.initialize()
        self.notify_initialized(h["Mcp-Session-Id"])
        _, _, body = self.call("hospital_search", {"query": "x"}, h["Mcp-Session-Id"])
        self.assertTrue(body["result"]["isError"])
        payload = json.loads(body["result"]["content"][0]["text"])
        self.assertEqual(payload["error_code"], "tool_timeout")


if __name__ == "__main__":
    unittest.main()
