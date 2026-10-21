# 设备端 HTTP MCP 部署说明（hospital-companion）

> 阶段目标：让 S100 内置 Agent（`vbot-agent-harness`）通过 HTTP MCP 调用本服务的三个只读工具，
> 配合 `hospital-companion` Skill 使用虚拟医院知识与服务端患者状态。
> 本阶段**不连接机械狗、不执行真实导航、不修改设备系统服务、不上传任何内容到设备**。

## 1. 三层边界（不要混淆）

| 层 | 角色 | 本项目对应 |
|---|---|---|
| 设备 Agent（`vbot-agent-harness`） | 对话主体，作为 MCP **客户端**发现并调用工具；通过内容 API 加载 Skill/AGENTS.md | 设备运行时，不在本仓库 |
| HTTP MCP | Agent 与开发者工具之间的 JSON-RPC 通道；本服务是**被调用方** | `linbot_voice_agent/mcp_server.py` |
| Aorta / ROS 2 | 机器人原生能力层（传感器、运动、语音、导航），与本 MCP 服务无关 | 本服务不 import SDK、不建会话、不发运动 |

MCP 工具只访问本地虚拟医院（Knowledge / WorkflowEngine / PatientState / SyntheticEnvironment），
不调用 Aorta，也不发布任何 ROS 话题。

## 2. 固定地址与协议（官方 EDU 契约）

| 项 | 值 |
|---|---|
| MCP 端点（设备） | `http://192.168.127.2:18080/mcp` |
| 内容 API（Skill/AGENTS.md 注入） | `http://192.168.127.10:8787` |
| 传输 | HTTP POST，JSON-RPC 2.0 |
| 协议版本 | `2025-06-18`（请求头 `MCP-Protocol-Version: 2025-06-18`） |
| Accept | `application/json, text/event-stream` |
| 会话 | `initialize` 返回的 `Mcp-Session-Id` 后续携带；通知 `notifications/initialized` 返回 202 空体 |

- `18080`（MCP）与 `8787`（内容 API）是设备内网两个独立服务，**不要**把 JSON-RPC 发给 8787，也不要把内容 API 当 MCP。
- 两个端口都只在可信机器人内网使用，禁止转发到校园公网或互联网。
- 本地协议测试用 `127.0.0.1` 临时端口，不改变设备约定地址。

## 3. MCP 工具清单（第一阶段，全部只读）

| 工具 | 参数 | 说明 |
|---|---|---|
| `hospital_search` | `query`(必填, string≤200), `limit`(1-8, 默认4) | 检索地点/科室/药房/流程知识 |
| `patient_status` | 无 | 读取服务端会话内虚拟患者快照与模拟导航状态 |
| `hospital_next_step` | 无 | 流程引擎给出下一步/阻塞原因 |

错误统一走 `result.isError=true` + `content[0].text` 里的 `error_code`
（`invalid_arguments` / `unknown_tool` / `unknown_session` / `tool_timeout` / `tool_error`）；
HTTP 200 不代表工具成功，必须看 `isError`。

## 4. 在 S100 上运行服务（真机阶段执行，本阶段不做）

以 `vbot` 身份，开发者目录 `/userdata/vbot` 下：

```bash
# 依赖：仅 Python 标准库；复用仓库种子数据
python3 -m linbot_voice_agent.mcp_server \
  --host 192.168.127.2 --port 18080 \
  --patient patient_01
```

- 健康检查（可选）：`GET /healthz` 返回 `{"status":"ok"}`，它不是 MCP 方法，也不证明 Agent 已发现工具。
- 工具执行有界超时（默认 5s），超时返回 `tool_timeout`，不做不安全重试。
- 每个 `initialize` 建立独立会话与独立 `SyntheticEnvironment`，TTL 过期自动清理；动态患者状态只存在服务端。
- 不要为抢占 18080 去 kill 未知监听或改系统服务；先确认端口占用。

## 5. 注入 Skill（真机阶段执行，本阶段不做）

内容 API 基址 `http://192.168.127.10:8787`，`workingDir=/app/vbot-agent-harness`：

```bash
VBOT_API_URL=http://192.168.127.10:8787
# 先备份 GET /api/skills?workingDir=... 与 GET /api/agents-md
jq -n --rawfile content device-skills/hospital-companion/SKILL.md \
  --arg workingDir /app/vbot-agent-harness \
  '{target:"user",workingDir:$workingDir,content:$content}' | \
curl -fsS -X PUT "$VBOT_API_URL/api/skills/hospital-companion" \
  -H 'Content-Type: application/json' --data-binary @-
# 再 GET /api/skills/hospital-companion 做 readback 比对；新 Turn 验证激活
```

- Skill 名 `hospital-companion` 与路由一致，仅小写字母/数字/连字符。
- PUT 整体替换 `SKILL.md`，不支持上传附属资源；删除用 `DELETE /api/skills/hospital-companion?target=user`（破坏性，先备份）。
- 行为必须在**新 Turn** 里验证；保存文本不代表激活，运行中的 Turn 不热加载。

## 6. 兼容性前提（真机前必须核对）

- 仅支持 `foot_quadruped` EDU，目标应用版本 **V1.6.0**；`v5.0.16-2026070701edu` 是系统镜像/固件串，不能代替应用版本。
- 先确认交付镜像的 harness 支持 `18080` MCP 与 `8787` 内容 API；不支持则联系平台，不要改服务配置。
- 本服务不依赖 Aorta SDK、ROS 桥接或实时样本，因此 MCP 工具本身的协议测试可离线完成；但“Agent 是否真的调用、Skill 是否激活”必须在真机新 Turn 验证。

## 7. 服务加固（0.2.0）

- 默认仅监听 `127.0.0.1`；绑定非回环地址时打印可信内网警告，不暴露公网。
- `initialize` 校验 `protocolVersion` 与 `clientInfo` 类型；`tools/list`、`tools/call` 要求有效会话；`params`/`arguments` 非对象直接拒绝。
- 最大会话数（默认 32）、TTL 过期清理、请求体上限 64KiB；超限返回 JSON-RPC 错误，不无限建会话。
- 工具执行改用共享有界线程池并带超时，超时返回 `tool_timeout`，不再每次请求新建池，避免线程泄漏。
- 畸形 JSON、错误 id/params、未知方法均返回 JSON-RPC 错误信封，不抛出未捕获异常。

## 8. Ubuntu 白天自由问答（外部 LLM + MCP，非真机）

终端 1：

```bash
python3 -m linbot_voice_agent.mcp_server --host 127.0.0.1 --port 18080
```

终端 2（兼容 Chat Completions）：

```bash
OPENAI_BASE_URL=https://端点/v1 OPENAI_MODEL=模型 OPENAI_API_KEY=key \
python3 -m linbot_voice_agent.llm_mcp_agent \
  --mcp-url http://127.0.0.1:18080/mcp --interactive
```

模型不可用会明确报错；`--fallback-rule` 仅为显式离线演示，输出带 `[规则兜底，非 LLM]`，不计入模型测试。

## 9. 已实现 vs 待真机验证

### 已在本地实现并测试
- HTTP MCP 生命周期：initialize / notifications/initialized / tools/list / tools/call，含版本/会话/参数校验与资源限制。
- 三个只读工具、`isError` 错误契约、会话隔离、工具超时、最大会话与过期清理。
- 独立 MCP 客户端测试（多轮、重新初始化、错误恢复、Skill 名称参数一致性）。
- LLM+MCP 闭环：模型自主决策工具调用、参数校验、结果回喂、多轮历史与工具日志；模型缺失明确报错。
- `hospital-companion/SKILL.md`（只写规则，不含动态患者事实）；原有模拟医院与全部测试保留。

### 必须连上 VBOT 真机后才能验证
- harness 是否在 `192.168.127.2:18080` 发现并成功调用工具（外部 LLM 模拟通过 ≠ 设备 Agent 通过）。
- Skill 通过 8787 注入后的实际激活与新 Turn 行为。
- 应用版本 V1.6.0、路由/ACL、harness 兼容性；MCP 与 Aorta/语音导航的后续衔接。
- 逐项只读检查见 `docs/device-readiness-checklist.md`。
