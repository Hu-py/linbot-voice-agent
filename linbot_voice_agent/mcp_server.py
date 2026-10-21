"""HTTP MCP server for the hospital companion (read-only tools).

Implements the EDU HTTP MCP contract (protocol version ``2025-06-18``) on a single
``POST /mcp`` endpoint. This layer is the **MCP** boundary between the device
``vbot-agent-harness`` and this Python service; it is NOT Aorta and NOT ROS 2:
no robot session, no motion command, no SDK import.

Dynamic patient state stays server-side inside a per-session ``SyntheticEnvironment``.
The device Skill only declares when and how to call the tools; it never hard-codes
the virtual patient's changing facts.

Security defaults: bind ``127.0.0.1`` unless explicitly told otherwise; the device
address ``192.168.127.2`` is only used on the trusted robot internal network.
"""
import argparse
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from .simulator import SyntheticEnvironment

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "linbot-hospital-mcp"
SERVER_VERSION = "0.2.0"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_TOOL_TIMEOUT = 5.0
SESSION_TTL = 1800.0
DEFAULT_MAX_SESSIONS = 32
DEFAULT_EXECUTOR_WORKERS = 8

# JSON-RPC error codes (2025-06-18 transport conventions).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Tool declarations (JSON Schema subset; the server validates arguments itself)
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS = [
    {
        "name": "hospital_search",
        "description": "检索虚拟医院的地点、科室、药房、服务与就诊流程知识，返回命中的条目文本。"
                       "用于回答位置、路线指引和一般流程问答；不返回动态患者状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户问题或关键词，例如“心内科在哪里”“药房在哪”。"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4,
                          "description": "返回条目数量上限，1-8，默认 4。"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "patient_status",
        "description": "读取当前会话服务端保存的虚拟患者动态状态：预约科室、当前位置、流程事实、"
                       "紧急标记和模拟导航状态。只读，不推进流程。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "hospital_next_step",
        "description": "由确定性流程引擎给出当前患者的下一步：可导航目标、待完成事件或阻塞原因。"
                       "只读建议，不直接启动导航，不修改患者事实。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class SessionLimitError(RuntimeError):
    """Raised when the session cap is reached."""


class McpSession:
    """One isolated SyntheticEnvironment per MCP session."""

    def __init__(self, seed_dir, patient_id, ttl=SESSION_TTL):
        self.env = SyntheticEnvironment(seed_dir, patient_id)
        self.id = uuid.uuid4().hex
        self.created = time.monotonic()
        self.last_seen = self.created
        self.ttl = ttl

    def renewed(self):
        self.last_seen = time.monotonic()

    def expired(self, now=None):
        now = now or time.monotonic()
        return now - self.last_seen > self.ttl


class SessionStore:
    """Thread-safe, bounded, TTL-expiring session registry."""

    def __init__(self, seed_dir, patient_id="patient_01", ttl=SESSION_TTL,
                 max_sessions=DEFAULT_MAX_SESSIONS):
        self.seed_dir = seed_dir
        self.patient_id = patient_id
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions = {}

    def _purge_expired(self, now):
        stale = [sid for sid, s in self._sessions.items() if s.expired(now)]
        for sid in stale:
            self._sessions.pop(sid, None)

    def create(self):
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            if len(self._sessions) >= self.max_sessions:
                raise SessionLimitError(f"已达最大会话数 {self.max_sessions}，请稍后重试")
            session = McpSession(self.seed_dir, self.patient_id, self.ttl)
            self._sessions[session.id] = session
            return session

    def get(self, session_id):
        if not session_id or not isinstance(session_id, str):
            return None
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.renewed()
            return session

    def __len__(self):
        with self._lock:
            return len(self._sessions)


# ---------------------------------------------------------------------------
# Tool handlers: each receives the session env and validated arguments
# ---------------------------------------------------------------------------
def _tool_hospital_search(env, args):
    query = args["query"]
    limit = args.get("limit", 4)
    rows = env.knowledge.search(query, limit=limit)
    results = [
        {
            "id": row["id"],
            "title": row["title"],
            "text": row["text"],
            "source": row.get("source", ""),
        }
        for row in rows
    ]
    return {"query": query, "count": len(results), "results": results}


def _tool_patient_status(env, _args):
    patient = env.patient.snapshot()
    robot = env.robot
    patient["navigation"] = {
        "status": robot.status,
        "position": robot.position,
        "task_id": robot.task_id,
        "destination": robot.destination,
        "simulated": True,
    }
    return patient


def _tool_hospital_next_step(env, _args):
    step = env.workflow.next_step(env.patient)
    return {"next_step": step}


def build_default_tools():
    """Registry: name -> (definition, handler(env, args))."""
    handlers = {
        "hospital_search": _tool_hospital_search,
        "patient_status": _tool_patient_status,
        "hospital_next_step": _tool_hospital_next_step,
    }
    definitions = {d["name"]: d for d in TOOL_DEFINITIONS}
    return {name: (definitions[name], fn) for name, fn in handlers.items()}


# ---------------------------------------------------------------------------
# Argument validation (declared schema does not enforce by itself)
# ---------------------------------------------------------------------------
class ToolArgumentError(ValueError):
    pass


def _validate(definition, args):
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolArgumentError("arguments 必须是对象")
    schema = definition["inputSchema"]
    allowed = set(schema.get("properties", {}))
    extra = set(args) - allowed
    if extra and schema.get("additionalProperties") is False:
        raise ToolArgumentError(f"不支持的参数: {', '.join(sorted(extra))}")
    for key in schema.get("required", []):
        if key not in args:
            raise ToolArgumentError(f"缺少必填参数: {key}")
    if "query" in args:
        query = args["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ToolArgumentError("query 必须为 1-200 字的非空字符串")
        args["query"] = query.strip()
    if "limit" in args:
        limit = args["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 8):
            raise ToolArgumentError("limit 必须为 1-8 的整数")
    return args


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

    # Suppress default access logging; tests inspect payloads instead.
    def log_message(self, *args):
        return

    # -- response helpers ---------------------------------------------------
    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before reading; harmless on the server side.
            pass

    def _rpc_error(self, code, message, request_id=None, http_status=200):
        self._send_json(http_status, {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        })

    def _accepted(self):
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": SERVER_NAME})
        else:
            self._rpc_error(METHOD_NOT_FOUND, f"未知路径: {self.path}", http_status=404)

    def do_POST(self):
        if self.path != "/mcp":
            self._rpc_error(METHOD_NOT_FOUND, f"未知路径: {self.path}", http_status=404)
            return

        # Read exactly Content-Length bytes; never block waiting for a fixed cap.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = -1
        if length < 0:
            self._rpc_error(INVALID_REQUEST, "无效的 Content-Length", http_status=411)
            return
        if length > MAX_BODY_BYTES:
            self._rpc_error(INVALID_REQUEST, "请求体超过大小上限", http_status=413)
            return
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._rpc_error(PARSE_ERROR, "解析错误：请求不是合法 JSON")
            return
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            self._rpc_error(INVALID_REQUEST, "无效请求：需要 jsonrpc=2.0 对象",
                            request.get("id"))
            return

        method = request.get("method")
        request_id = request.get("id")
        # Notifications carry no id; anything else with an id must be scalar.
        if method != "notifications/initialized" and request_id is not None:
            if not isinstance(request_id, (str, int, float)) or isinstance(request_id, bool):
                self._rpc_error(INVALID_REQUEST, "id 必须为字符串或数字", http_status=400)
                return
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            self._rpc_error(INVALID_REQUEST, "params 必须是对象", request_id)
            return

        session_header = self.headers.get("Mcp-Session-Id")

        if method == "notifications/initialized":
            # Notification: no id; 202 empty regardless of optional session header.
            self._accepted()
            return

        if method == "initialize":
            return self._handle_initialize(request_id, params)

        if method == "tools/list":
            session = self.server.store.get(session_header)
            if session is None:
                self._rpc_error(INVALID_REQUEST, "缺少有效会话，请先 initialize", request_id)
                return
            self._send_json(200, {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"tools": TOOL_DEFINITIONS},
            })
            return

        if method == "tools/call":
            return self._handle_tool_call(request_id, params, session_header)

        self._rpc_error(METHOD_NOT_FOUND, f"未知方法: {method}", request_id)

    # -- methods ------------------------------------------------------------
    def _handle_initialize(self, request_id, params):
        client_version = params.get("protocolVersion")
        if not isinstance(client_version, str) or client_version != PROTOCOL_VERSION:
            self._rpc_error(INVALID_REQUEST,
                            f"协议版本不兼容: 需要 {PROTOCOL_VERSION}, 收到 {client_version}",
                            request_id)
            return
        client_info = params.get("clientInfo")
        if client_info is not None and not isinstance(client_info, dict):
            self._rpc_error(INVALID_REQUEST, "clientInfo 必须是对象", request_id)
            return
        try:
            session = self.server.store.create()
        except SessionLimitError as exc:
            self._rpc_error(INVALID_REQUEST, str(exc), request_id)
            return
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
        self._send_json(200, {"jsonrpc": "2.0", "id": request_id, "result": result},
                        {"Mcp-Session-Id": session.id})

    def _handle_tool_call(self, request_id, params, session_header):
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            self._tool_failure(request_id, "invalid_arguments", "缺少工具名 name")
            return
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            self._tool_failure(request_id, "invalid_arguments", "arguments 必须是对象", tool=name)
            return

        session_id = session_header or arguments.get("session_id")
        session = self.server.store.get(session_id)
        if session is None:
            self._tool_failure(request_id, "unknown_session",
                               "会话不存在或已过期，请先 initialize", tool=name)
            return

        registry = self.server.tools
        if name not in registry:
            self._tool_failure(request_id, "unknown_tool", f"未注册工具: {name}", tool=name)
            return
        definition, handler = registry[name]
        try:
            validated = _validate(definition, arguments)
        except ToolArgumentError as exc:
            self._tool_failure(request_id, "invalid_arguments", str(exc), tool=name)
            return

        # Bounded execution on a shared pool; a hung tool becomes isError and the
        # pool stays reusable instead of leaking a thread per request.
        try:
            future = self.server.executor.submit(handler, session.env, validated)
            payload = future.result(timeout=self.server.tool_timeout)
        except FutureTimeout:
            self._tool_failure(request_id, "tool_timeout",
                               f"工具 {name} 执行超时", tool=name)
            return
        except Exception as exc:  # noqa: BLE001 - surface as tool error
            self._tool_failure(request_id, "tool_error", f"{type(exc).__name__}: {exc}",
                                tool=name)
            return

        self._send_json(200, {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "structuredContent": payload,
                "isError": False,
            },
        })

    def _tool_failure(self, request_id, code, message, tool=None):
        detail = {"error_code": code, "message": message}
        if tool:
            detail["tool"] = tool
        self._send_json(200, {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}],
                "isError": True,
            },
        })


def create_server(host, port, store, tools=None, tool_timeout=DEFAULT_TOOL_TIMEOUT,
                  executor_workers=DEFAULT_EXECUTOR_WORKERS):
    httpd = ThreadingHTTPServer((host, port), McpHttpHandler)
    httpd.store = store
    httpd.tools = tools or build_default_tools()
    httpd.tool_timeout = tool_timeout
    httpd.executor = ThreadPoolExecutor(max_workers=executor_workers, thread_name_prefix="mcp-tool")
    # Close the shared pool when the socket server closes; no per-call pool leak.
    httpd._mcp_executor = httpd.executor
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="Linbot 医院陪诊 HTTP MCP 服务（只读工具）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="默认仅监听 127.0.0.1；设备部署才绑定可信内网 192.168.127.2")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--seed-dir", default=str(files("linbot_voice_agent").joinpath("data/seeds")),
                        help="虚拟医院种子目录（hospital.json/workflows.json/patient_profiles.json）")
    parser.add_argument("--patient", default="patient_01")
    parser.add_argument("--tool-timeout", type=float, default=DEFAULT_TOOL_TIMEOUT)
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    args = parser.parse_args(argv)

    if not args.host.startswith("127.") and args.host not in ("::1", "localhost"):
        print(f"警告：绑定非回环地址 {args.host}，仅限可信机器人内网，禁止暴露到公网。")

    store = SessionStore(args.seed_dir, args.patient, max_sessions=args.max_sessions)
    httpd = create_server(args.host, args.port, store, tool_timeout=args.tool_timeout)
    print(f"MCP 监听 http://{args.host}:{args.port}/mcp  protocol={PROTOCOL_VERSION}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd._mcp_executor.shutdown(wait=False)
        httpd.server_close()


if __name__ == "__main__":
    main()
