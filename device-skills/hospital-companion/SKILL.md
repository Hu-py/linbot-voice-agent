---
name: hospital-companion
description: Use when the user asks about hospital locations, departments, pharmacy, route guidance, the booked patient's current status, or the next step in the visit. Read-only simulated-hospital companion; never moves the robot.
---

# Hospital Companion（只读陪诊工具规则）

本 Skill 只规定**何时调用、如何解读** MCP 工具，不存放虚拟患者的动态事实。
患者状态由 Python 服务端按会话保存，必须通过工具实时读取，不要在回复里编造。

## 触发门控：点名“聆灵”才调用工具

- 仅当用户在本轮**称呼/呼唤“聆灵”**（如“聆灵，心内科在哪”“聆灵，我报到了吗”），
  或明确向聆灵提出医院相关问题时，才允许调用下面三个工具。
- 用户未点名、只是自言自语、闲聊或与他人交谈时，**不要调用任何医院工具**，
  也不要主动报楼层/状态/下一步；可简短应答或保持安静。
- 判断依据是本轮输入是否指向聆灵；历史对话里曾点名不代表本轮也要调用工具。
- 物理唤醒词（说“聆灵”才收音）由设备语音/唤醒配置负责，不在本 Skill 范围内；
  本规则只约束进入对话后是否调用 MCP 工具。

## 可用工具（均为只读）

| 工具 | 何时调用 | 返回 |
|---|---|---|
| `hospital_search` | 用户问地点、科室、药房、服务位置或一般就诊流程/路线说明 | 命中的知识条目（标题、正文、来源） |
| `patient_status` | 用户问“我现在到哪了/报到了吗/缴费了吗/当前状态”等个人进度 | 服务端保存的患者快照与模拟导航状态 |
| `hospital_next_step` | 用户问“接下来该做什么/下一步去哪” | 流程引擎给出的下一步：导航目标、待办事件或阻塞原因 |

## 使用规则

1. **先过点名门控**：本轮未称呼“聆灵”、也未明确向聆灵提问时，不调用任何工具；满足门控后再按下面规则调用。
2. **位置与流程问答**先调用 `hospital_search`，基于返回文本回答；不要凭常识杜撰楼层、科室或药房位置。
3. **个人进度**一律先调用 `patient_status`，按返回的 `facts`、`current_location`、`navigation.status` 回答；不要把示例或记忆中的状态当成当前事实。
4. **下一步**调用 `hospital_next_step`；返回 `blocked` 时把阻塞原因如实告知用户，返回 `complete` 时说明流程已完成。
5. 工具返回 `isError=true` 时，保留错误信息并说明暂时无法获取，不要假装成功或编造结果。
6. 这三个工具**只读**：不启动导航、不修改患者事实、不控制机器人。真实移动、乘梯、求助通知不在本 Skill 范围内。
7. 同一问题不要重复调用同一工具刷结果；检索无命中时如实说明知识库未覆盖。

## 边界

- 本场景为虚构模拟医院，所有患者与地点均为示例数据。
- 本 Skill 不授予运动权限，也不替代设备安全急停与现场确认。
