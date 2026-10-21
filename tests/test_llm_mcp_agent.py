"""Closed-loop tests for the LLM + MCP agent with a scripted fake model.

No network and no real LLM: the scripted transport stands in for Chat
Completions, while real MCP tools run against the ephemeral server. These prove
the tool-call loop, argument validation, result feedback and history; they are
NOT evidence that a real model was tested.
"""
import argparse
import threading
import unittest
from pathlib import Path

from linbot_voice_agent.llm_mcp_agent import (
    HttpChatCompletionsTransport, LlmMcpAgent, LlmUnavailable,
    RuleFallbackAgent, RuleFallbackUsed, build_agent, validate_arguments,
)
from linbot_voice_agent.mcp_client import McpClient
from linbot_voice_agent.mcp_server import (
    SessionStore, build_default_tools, create_server,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "seeds"


class ScriptedTransport:
    """Returns queued Chat Completions responses in order."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def complete(self, messages, tools):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def tool_call(call_id, name, arguments):
    return {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }],
            },
        }],
    }


def final(text):
    return {"choices": [{"message": {"content": text, "tool_calls": []}}]}


class AgentServerBase(unittest.TestCase):
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


class LoopTests(AgentServerBase):
    def test_tool_call_then_final_answer_feeds_result_back(self):
        transport = ScriptedTransport([
            tool_call("c1", "hospital_search", '{"query":"心内科在哪里"}'),
            final("心内科候诊区在3楼A区。"),
        ])
        agent = LlmMcpAgent(self.connected_client(), transport)
        answer = agent.chat("心内科在哪里？")
        self.assertIn("3楼A区", answer)
        self.assertEqual(len(agent.tool_log), 1)
        self.assertTrue(agent.tool_log[0]["ok"])
        self.assertEqual(agent.tool_log[0]["name"], "hospital_search")
        # Tool result was fed back as a tool-role message.
        roles = [m["role"] for m in agent.messages]
        self.assertIn("tool", roles)
        self.assertEqual(transport.calls, 2)

    def test_multi_turn_history_retained(self):
        transport = ScriptedTransport([
            tool_call("c1", "patient_status", "{}"),
            final("你已完成报到。"),
            final("下一步是前往心内科候诊区。"),
        ])
        agent = LlmMcpAgent(self.connected_client(), transport)
        agent.chat("我现在到哪一步了？")
        answer = agent.chat("那接下来呢？")
        self.assertIn("心内科", answer)
        # System + two user turns + assistant/tool messages retained.
        user_turns = [m for m in agent.messages if m["role"] == "user"]
        self.assertEqual(len(user_turns), 2)

    def test_invalid_arguments_rejected_and_feedback_given(self):
        transport = ScriptedTransport([
            tool_call("c1", "hospital_search", '{"query":123}'),
            final("我无法用这个参数检索。"),
        ])
        agent = LlmMcpAgent(self.connected_client(), transport)
        answer = agent.chat("找一下心内科")
        self.assertEqual(agent.tool_log[0]["ok"], False)
        self.assertIn("error", agent.tool_log[0])
        self.assertTrue(answer)

    def test_unknown_tool_call_recorded_as_error(self):
        transport = ScriptedTransport([
            tool_call("c1", "teleport_robot", '{}'),
            final("该工具不可用。"),
        ])
        agent = LlmMcpAgent(self.connected_client(), transport)
        agent.chat("带我走")
        self.assertFalse(agent.tool_log[0]["ok"])


class ValidationAndAvailabilityTests(AgentServerBase):
    def test_validate_arguments_enforces_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["query"],
            "additionalProperties": False,
        }
        validate_arguments(schema, {"query": "x", "limit": 3})
        with self.assertRaises(ValueError):
            validate_arguments(schema, {"limit": 3})  # missing query
        with self.assertRaises(ValueError):
            validate_arguments(schema, {"query": "x", "hack": 1})
        with self.assertRaises(ValueError):
            validate_arguments(schema, {"query": "x", "limit": 99})

    def test_unavailable_model_raises_instead_of_faking(self):
        transport = HttpChatCompletionsTransport(
            "http://127.0.0.1:1/nope", "dummy", "key", timeout=2)
        agent = LlmMcpAgent(self.connected_client(), transport)
        with self.assertRaises(LlmUnavailable):
            agent.chat("你好")

    def test_build_agent_without_config_fails(self):
        ns = argparse.Namespace(
            mcp_url=self.url, base_url=None, model=None, api_key=None,
            fallback_rule=False, query=None)
        with self.assertRaises(LlmUnavailable):
            build_agent(ns)

    def test_rule_fallback_is_labeled_and_not_llm(self):
        fallback = RuleFallbackAgent(self.connected_client())
        out = fallback.chat("药房在哪里")
        self.assertTrue(out.startswith("[规则兜底，非 LLM]"))
        agent = LlmMcpAgent(self.connected_client(), ScriptedTransport([]),
                            mode="rule_fallback")
        with self.assertRaises(RuleFallbackUsed):
            agent.chat("不应走这里")


if __name__ == "__main__":
    unittest.main()
