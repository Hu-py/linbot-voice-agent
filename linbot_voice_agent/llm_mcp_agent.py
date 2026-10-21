"""LLM + MCP closed-loop test agent (external Chat Completions model).

The model decides whether to call a hospital MCP tool; this module never replaces
that decision with keywords. It reads tools from the live MCP ``tools/list``,
validates arguments, feeds real tool results back to the model, and keeps a
multi-turn history plus a tool-call log.

This is a *workstation test harness*, not the device agent and not a robot control
path. With no model configured it fails loudly; an explicit rule fallback exists
only for offline demos and is labeled separately, never counted as an LLM test.
"""
import argparse
import json
import os
import urllib.request
import urllib.error

from .mcp_client import McpClient, McpCallError

DEFAULT_SYSTEM_PROMPT = (
    "你是虚拟医院陪诊助手。回答用户前，先判断是否需要调用 MCP 工具："
    "位置/科室/药房/流程问题用 hospital_search；个人就诊进度用 patient_status；"
    "下一步做什么用 hospital_next_step。工具结果是唯一事实来源，不要编造患者状态或地点。"
    "本场景为只读模拟医院，不执行导航或运动。"
)


class LlmUnavailable(RuntimeError):
    """Model endpoint missing, unreachable, or rejected credentials."""


class RuleFallbackUsed(RuntimeError):
    """Raised when code expects a real LLM but only the rule fallback is present."""


# ---------------------------------------------------------------------------
# Chat Completions transport (OpenAI-compatible)
# ---------------------------------------------------------------------------
class HttpChatCompletionsTransport:
    """Posts messages to an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, base_url, model, api_key, timeout=20.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, messages, tools):
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise LlmUnavailable(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmUnavailable(f"模型端点不可达: {exc}") from exc


# ---------------------------------------------------------------------------
# Argument validation (client-side guard; server remains authoritative)
# ---------------------------------------------------------------------------
def validate_arguments(schema, arguments):
    props = schema.get("properties", {})
    allowed = set(props)
    extra = set(arguments) - allowed
    if extra and schema.get("additionalProperties") is False:
        raise ValueError(f"不支持的参数: {', '.join(sorted(extra))}")
    for key in schema.get("required", []):
        if key not in arguments:
            raise ValueError(f"缺少必填参数: {key}")
    for key, value in arguments.items():
        declared = props.get(key, {})
        expected = declared.get("type")
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"参数 {key} 必须是字符串")
        if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"参数 {key} 必须是整数")
        if expected == "integer" and "minimum" in declared and value < declared["minimum"]:
            raise ValueError(f"参数 {key} 小于最小值 {declared['minimum']}")
        if expected == "integer" and "maximum" in declared and value > declared["maximum"]:
            raise ValueError(f"参数 {key} 大于最大值 {declared['maximum']}")
    return arguments


def mcp_tools_to_openai(tools):
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["inputSchema"],
            },
        }
        for t in tools
    ]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
class LlmMcpAgent:
    def __init__(self, mcp: McpClient, transport, system_prompt=DEFAULT_SYSTEM_PROMPT,
                 max_tool_rounds=4, mode="real_llm"):
        self.mcp = mcp
        self.transport = transport
        self.mode = mode
        self.max_tool_rounds = max_tool_rounds
        self.tools = mcp.list_tools()
        self.openai_tools = mcp_tools_to_openai(self.tools)
        self.schema_by_name = {t["name"]: t["inputSchema"] for t in self.tools}
        self.messages = [{"role": "system", "content": system_prompt}]
        self.tool_log = []

    def reset_history(self):
        system = self.messages[0]
        self.messages = [system]
        self.tool_log = []

    def _record(self, name, arguments, ok, result=None, error=None):
        self.tool_log.append({
            "name": name, "arguments": arguments, "ok": ok,
            "result": result if ok else None, "error": error,
        })

    def chat(self, user_text):
        if self.mode != "real_llm":
            raise RuleFallbackUsed("当前不是真实 LLM 模式，禁止冒充真实模型测试")
        self.messages.append({"role": "user", "content": user_text})

        for _ in range(self.max_tool_rounds):
            response = self.transport.complete(self.messages, self.openai_tools)
            choice = response["choices"][0]
            message = choice["message"]
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                answer = message.get("content") or ""
                self.messages.append({"role": "assistant", "content": answer})
                return answer

            # Persist assistant tool-call request, then resolve every call.
            self.messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                fn = call["function"]
                name = fn["name"]
                try:
                    raw_args = fn.get("arguments") or "{}"
                    arguments = json.loads(raw_args)
                    validate_arguments(self.schema_by_name[name], arguments)
                    result = self.mcp.call_tool(name, arguments)
                    self._record(name, arguments, True, result=result)
                    tool_payload = json.dumps(result, ensure_ascii=False)
                except (KeyError, json.JSONDecodeError, ValueError, McpCallError) as exc:
                    self._record(name, fn.get("arguments"), False, error=str(exc))
                    tool_payload = json.dumps(
                        {"error_code": "rejected", "message": str(exc)},
                        ensure_ascii=False)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": tool_payload,
                })

        raise LlmUnavailable(f"超过最大工具轮次 {self.max_tool_rounds} 仍未得到最终回答")


# ---------------------------------------------------------------------------
# Explicit rule fallback (offline demo only; never labeled as LLM)
# ---------------------------------------------------------------------------
class RuleFallbackAgent:
    """Clearly-labeled offline fallback; NOT an LLM and NOT a valid LLM test."""

    mode = "rule_fallback"

    def __init__(self, mcp: McpClient):
        self.mcp = mcp
        self.tool_log = []

    def chat(self, user_text):
        text = user_text.lower()
        if any(k in text for k in ("药房", "药", "在哪", "哪里", "科室", "心内科", "位置", "路线")):
            result = self.mcp.call_tool("hospital_search", {"query": user_text})
            self.tool_log.append({"name": "hospital_search", "arguments": {"query": user_text},
                                  "ok": True, "result": result, "error": None})
            return "[规则兜底，非 LLM] " + json.dumps(result, ensure_ascii=False)
        if "下一步" in text or "接下来" in text:
            result = self.mcp.call_tool("hospital_next_step", {})
            self.tool_log.append({"name": "hospital_next_step", "arguments": {},
                                  "ok": True, "result": result, "error": None})
            return "[规则兜底，非 LLM] " + json.dumps(result, ensure_ascii=False)
        result = self.mcp.call_tool("patient_status", {})
        self.tool_log.append({"name": "patient_status", "arguments": {},
                              "ok": True, "result": result, "error": None})
        return "[规则兜底，非 LLM] " + json.dumps(result, ensure_ascii=False)


def build_agent(args):
    client = McpClient(args.mcp_url).connect()
    if args.fallback_rule:
        return RuleFallbackAgent(client)

    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL")
    model = args.model or os.environ.get("OPENAI_MODEL")
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if not (base_url and model):
        raise LlmUnavailable(
            "未配置模型：需要 --base-url/--model（或 OPENAI_BASE_URL/OPENAI_MODEL）；"
            "离线演示可显式加 --fallback-rule，但那不是 LLM 测试。")
    transport = HttpChatCompletionsTransport(base_url, model, api_key)
    return LlmMcpAgent(client, transport)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LLM + MCP 自由问答测试（只读虚拟医院）")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:18080/mcp")
    parser.add_argument("--base-url", help="OpenAI 兼容 /chat/completions 前缀")
    parser.add_argument("--model", help="模型名")
    parser.add_argument("--api-key", help="Bearer key（也可用 OPENAI_API_KEY）")
    parser.add_argument("--query", help="一次性提问；不填则进入交互式")
    parser.add_argument("--fallback-rule", action="store_true",
                        help="显式启用规则兜底（非 LLM，不得计入模型测试）")
    args = parser.parse_args(argv)

    agent = build_agent(args)
    if args.query:
        print(agent.chat(args.query))
        return
    print("进入自由问答（输入 exit 退出）；mode=", agent.mode)
    while True:
        try:
            user = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in {"exit", "quit"}:
            break
        if not user:
            continue
        try:
            print("助手:", agent.chat(user))
        except LlmUnavailable as exc:
            print("模型不可用:", exc)
            break
    print("工具调用日志:")
    for entry in agent.tool_log:
        print(json.dumps(entry, ensure_ascii=False))


if __name__ == "__main__":
    main()
