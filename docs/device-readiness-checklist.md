# 晚上真机联调只读检查清单（VBOT EDU / S100）

> 原则：本清单全部为**只读**检查。不上传 Skill、不写 AGENTS.md、不改设备系统服务、
> 不杀未知端口监听、不执行任何运动或导航。发现不匹配就停止并记录，不要自行修复设备。
> 依据官方 vbot-lab：`docs/interfaces/agent/http-mcp.md`、`content-api.md`、
> `docs/guides/agent-integration.md`、`docs/compatibility.md`。

## 0. 先明确两种验证，不能混为一谈

| 结论 | 含义 | 本仓库当前状态 |
|---|---|---|
| **外部 LLM 模拟 Agent 验证通过** | 在 Ubuntu 用兼容 Chat Completions 的外部模型 + 本地 MCP 服务跑通工具决策闭环 | 已由脚本化/真实模型测试覆盖；**不等于**设备 Agent 可用 |
| **官方设备 Agent 真机验证通过** | S100 内置 `vbot-agent-harness` 在新 Turn 里真实发现并调用三个医院工具 | **必须连真机后逐项验证**，本地无法证明 |

curl 能打通 18080 只证明开发者服务协议正确，**不证明** harness 发现或调用了工具。

## 1. 应用版本与 Harness 可用性（只读）

- [ ] 记录机器人类型为 `foot_quadruped` EDU；目标应用版本 **V1.6.0**（仅目标，不是兼容范围承诺）。
- [ ] `cat /etc/version` 只记录**系统镜像**串（本机 `v5.0.16-2026070701edu`），**不要**把它当应用版本。
- [ ] 通过设备“关于/设置”或平台确认**应用版本**；`aorta --version` / `zenohd --version` 是组件版本，`0.0.0+unstamped` 无法识别发布。
- [ ] 新开一个 Turn，问一个无害的日常问题（不涉及工具/运动），确认内置 Agent 能自然语言对话；记录是否有回复、是否卡顿。
- [ ] 若应用版本不是 V1.6.0 或 Harness 无对话能力：停止后续写操作，记录并联系平台，不要改服务凑合用。

## 2. 只读核验内容 API（8787，不写）

基址 `http://192.168.127.10:8787`，`workingDir=/app/vbot-agent-harness`。**只 GET，不 PUT/DELETE。**

```bash
VBOT_API_URL=http://192.168.127.10:8787
WD=/app/vbot-agent-harness
curl --noproxy '*' -fsS --connect-timeout 3 --max-time 10 \
  --get "$VBOT_API_URL/api/skills" --data-urlencode "workingDir=$WD" | jq .
curl --noproxy '*' -fsS --connect-timeout 3 --max-time 10 \
  --get "$VBOT_API_URL/api/agents-md" --data-urlencode "workingDir=$WD" | jq '{path,exists}'
```

- [ ] 两个 GET 都有响应；记录现有 Skill 清单、来源层与 `shadowedByProject`，避免后续覆盖他人内容。
- [ ] 记录返回路径形态；本阶段**不写入**。GET `exists=true` 可能是兼容回退，不证明物理用户文件存在。
- [ ] 非 2xx / 非 JSON / `extension_failed`：记录状态码与正文，不重试轰炸。

## 3. MCP 服务内网位置与协议（部署后才做）

约定：MCP `http://192.168.127.2:18080/mcp`，协议 `2025-06-18`；与 8787 内容 API 是两个目的地。
本晚上若尚未把服务部署到设备，本节记为“待部署”，不要用公网地址诊断。

部署后在 S100 上只读冒烟（官方脚本形态）：initialize → 202 空通知 → tools/list →
一次无害 tools/call；断言 `isError=false`、`content[].text` 可解析。
- [ ] initialize 返回 `protocolVersion=2025-06-18` 与 `serverInfo`。
- [ ] `notifications/initialized` 返回 **202 且空体**。
- [ ] tools/list 出现 `hospital_search` / `patient_status` / `hospital_next_step`。
- [ ] 一次只读调用 `isError=false`；HTTP 200 不等于成功，必须看 `isError`。

## 4. 验证三个工具被官方 Agent 真实调用

协议冒烟通过后，**新开 Turn**（运行中的 Turn 不热加载），用自然语言分别触发：

- [ ] 位置/流程：“心内科在哪里？” → 期望 Agent 调用 `hospital_search`。
- [ ] 个人进度：“我现在报到了吗/到哪一步了？” → 期望 `patient_status`。
- [ ] 下一步：“接下来该做什么？” → 期望 `hospital_next_step`。

判定证据（两者都要有才算真机通过）：
- [ ] Agent 回答基于工具结果，不编造地点/患者事实。
- [ ] 开发者服务端日志能看到对应 `tools/call` 记录（名称、参数、`isError=false`）。

## 5. 白天 Ubuntu 自由问答启动方式（非真机）

终端 1 起本地 MCP：

```bash
python3 -m linbot_voice_agent.mcp_server --host 127.0.0.1 --port 18080
```

终端 2 接外部 Chat Completions 模型自由问答：

```bash
OPENAI_BASE_URL=https://你的兼容端点/v1 \
OPENAI_MODEL=你的模型 \
OPENAI_API_KEY=你的key \
python3 -m linbot_voice_agent.llm_mcp_agent \
  --mcp-url http://127.0.0.1:18080/mcp --interactive
```

- 未配置模型会明确报 `LlmUnavailable`，不会用规则结果冒充 LLM。
- 显式离线演示才加 `--fallback-rule`，其输出带 `[规则兜底，非 LLM]` 前缀，**不计入模型测试**。

## 6. 停止条件

- 应用版本/Harness 不匹配、8787 异常、18080 不通或 Agent 不调用工具：记录现象、状态码与日志，
  不修改设备服务、不强制写 Skill、不执行运动。
