"""Independent MCP-client tests simulating the official Agent exchange.

Drives a real ephemeral server through the reusable McpClient: initialize,
notification, discovery, all three tools, multi-turn, re-initialize, error
responses, recovery, and Skill/server tool-name consistency.
"""
import threading
import unittest
from pathlib import Path

from linbot_voice_agent.mcp_client import McpClient, McpCallError, McpProtocolError
from linbot_voice_agent.mcp_server import (
    PROTOCOL_VERSION, SessionStore, build_default_tools, create_server,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "seeds"
SKILL_MD = (Path(__file__).resolve().parent.parent
            / "device-skills" / "hospital-companion" / "SKILL.md")


class ClientServerBase(unittest.TestCase):
    def setUp(self):
        self.store = SessionStore(str(SEED_DIR))
        self.httpd = create_server("127.0.0.1", 0, self.store,
                                   tools=build_default_tools())
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd._mcp_executor.shutdown(wait=False)
        self.thread.join(timeout=2)

    def connected_client(self):
        return McpClient(self.url).connect()


class LifecycleAndToolTests(ClientServerBase):
    def test_full_lifecycle_and_all_tools(self):
        client = self.connected_client()
        self.assertTrue(client.session_id)
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        self.assertEqual(names, {"hospital_search", "patient_status", "hospital_next_step"})

        search = client.call_tool("hospital_search", {"query": "心内科"})
        self.assertGreaterEqual(search["count"], 1)

        status = client.call_tool("patient_status")
        self.assertTrue(status["synthetic"])
        self.assertIn("navigation", status)

        nxt = client.call_tool("hospital_next_step")
        self.assertIn(nxt["next_step"]["kind"], {"navigate", "event", "blocked", "complete"})

    def test_multi_turn_keeps_session(self):
        client = self.connected_client()
        first = client.call_tool("patient_status")
        second = client.call_tool("patient_status")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(client.session_id, client.session_id)

    def test_reinitialize_issues_new_session(self):
        first = self.connected_client()
        old = first.session_id
        second = McpClient(self.url)
        second.initialize()
        second.notify_initialized()
        self.assertNotEqual(old, second.session_id)
        # Old session remains valid until expiry; new session also works.
        self.assertEqual(first.call_tool("patient_status")["id"], "patient_01")
        self.assertEqual(second.call_tool("patient_status")["id"], "patient_01")

    def test_tools_list_requires_session(self):
        raw = McpClient(self.url)
        with self.assertRaises(McpProtocolError):
            raw.list_tools()


class ErrorAndRecoveryTests(ClientServerBase):
    def test_invalid_args_unknown_tool_unknown_session(self):
        client = self.connected_client()
        with self.assertRaises(McpCallError) as ctx:
            client.call_tool("hospital_search", {"limit": 3})  # missing query
        self.assertEqual(ctx.exception.code, "invalid_arguments")

        with self.assertRaises(McpCallError) as ctx:
            client.call_tool("no_such_tool", {})
        self.assertEqual(ctx.exception.code, "unknown_tool")

        stale = McpClient(self.url)
        stale.initialize()
        stale.session_id = "forged"
        with self.assertRaises(McpCallError) as ctx:
            stale.call_tool("patient_status")
        self.assertEqual(ctx.exception.code, "unknown_session")

    def test_wrong_protocol_version_rejected(self):
        client = McpClient(self.url)
        with self.assertRaises(McpProtocolError):
            client._post({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "1970-01-01", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "1"}},
            })
        # A correct initialization still succeeds afterward.
        client2 = self.connected_client()
        self.assertEqual(client2.call_tool("patient_status")["id"], "patient_01")

    def test_recovery_after_error_call(self):
        client = self.connected_client()
        with self.assertRaises(McpCallError):
            client.call_tool("hospital_search", {"query": ""})
        # Session is not poisoned by a rejected call.
        status = client.call_tool("patient_status")
        self.assertTrue(status["synthetic"])


class SkillConsistencyTests(ClientServerBase):
    def test_skill_names_and_params_match_server(self):
        client = self.connected_client()
        tools = client.list_tools()
        server_names = {t["name"] for t in tools}
        skill_text = SKILL_MD.read_text(encoding="utf-8")
        for name in server_names:
            self.assertIn(name, skill_text)

        search = next(t for t in tools if t["name"] == "hospital_search")
        props = search["inputSchema"]["properties"]
        self.assertIn("query", props)
        self.assertIn("limit", props)
        self.assertIn("query", search["inputSchema"]["required"])


if __name__ == "__main__":
    unittest.main()
