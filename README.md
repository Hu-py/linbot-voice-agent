# Linbot Voice Agent · 聆灵自然语言陪诊 Agent

v0.2：**LLM 理解多轮需求 + 医院知识检索 + 受控工具 + 虚拟医院事件闭环**。

这里是软件原型，所有默认医院/患者数据均为虚构。导航、乘梯、求助均在模拟器内；未接入真实机器人、医院系统或医护联络服务。

## VBOT EDU 接入检查

接入依据改为官方 [vbot-lab](https://github.com/VitaDynamics/vbot-lab)。在运行项目的 Ubuntu 中执行：

```bash
python3 -m linbot_voice_agent --vbot-check
```

该命令仅输出当前机器的 Python、SDK 包版本和工具路径，不连接机器人、不调用模型、不安装依赖。
默认档案记录足式 EDU 与用户提供的版本 `v5.0.16-2026070701edu`，应用版本仍为未知。
这串设备版本不能证明与官方文档目标应用 V1.6.0 兼容。自定义档案可通过 `--vbot-profile path/to/profile.json` 传入。
退出码 0 只表示报告生成成功，**不表示真机就绪**；报告中的 `hardware_ready` 始终为 false。

已增加独立的 `FinalAsrFilter`，供后续语音适配器过滤未知来源、空文本和重复投递；它尚未接到 CLI，不能直接订阅机械狗或播放语音。
现有模拟演示保持不变。Aorta、ROS 2、ASR/TTS 与导航的分阶段接入方法见 [真机接入准备](docs/phase2.md)。

## 1. 无密钥先跑完整心内科案例

Python 3.10+，无第三方运行依赖：

```bash
git clone https://github.com/Hu-py/linbot-voice-agent.git
cd linbot-voice-agent
python3 -m linbot_voice_agent --scenario-demo
```

已有仓库先运行 `git pull --ff-only`。

演示：已预约心内科的患者询问下一步 → 提出三楼心内科候诊区导航 → 患者确认 → 返回任务 ID → 服务台 → 电梯厅 → 模拟完成事件 → 更新位置并提示报到。

**无密钥的 `--scenario-demo` 使用固定计划，只验证状态与事件闭环，不代表已验证真实 LLM 理解能力。**

原有离线规则模式仍可使用：

```bash
python3 -m linbot_voice_agent --demo --show-tools
python3 -m linbot_voice_agent
```

## 2. 使用真正的 LLM 自由对话

配置提供 `/chat/completions` 的 HTTPS 模型服务：

```bash
export LINBOT_BASE_URL='https://your-provider.example/v1'
export LINBOT_MODEL='your-model-name'
read -rsp 'API key: ' LINBOT_API_KEY; echo
export LINBOT_API_KEY
python3 -m linbot_voice_agent --mode llm --synthetic --patient patient_01 --show-tools --show-sources
```

以上模型名称和地址为占位符。请填入自己的服务配置，密钥不要提交到 GitHub。`.env.example` 不会自动加载。

可以自然表达：

```text
我已经挂好号了，现在应该去哪儿？
好，带我过去。
```

随后输入三次 `/tick`，模拟器才会走到心内科；不会因为模型说“到了”而修改位置。

```text
/tick
/tick
/tick
/event checked_in
/event consultation_completed
接下来应该做什么？
```

后续可导航到缴费处、注入 `payment_completed`，再前往药房、注入 `medication_collected`。操作员事件是模拟医院反馈，不应由 LLM 自己生成。

也可以尝试“我想先找个地方坐一会儿”“先别走了”“我只是问问位置，不需要带路”。普通提问由 LLM 处理；暂停/取消等少量明确控制语句本地优先处理。导航和恢复仍需明确确认；模型推断的模糊肯定不能单独启动动作。

| 命令 | 作用 |
|---|---|
| `/tick` | 推进一步模拟导航，完成时发出事件 |
| `/patient` | 查看虚拟患者已核实状态 |
| `/next` | 直接调用确定性流程引擎，查看/提出下一步 |
| `/event checked_in` | 模拟报到完成（必须已到预约科室） |
| `/event consultation_completed` | 模拟就诊完成（必须已报到） |
| `/event payment_completed` | 模拟缴费完成（必须满足流程前提） |
| `/event medication_collected` | 模拟领取药品 |
| `/event navigation_failed` | 当前导航失败 |
| `/event emergency` | 中断普通陪诊并进入虚拟紧急状态 |
| `/event clear_emergency` | 模拟操作员解除紧急状态 |
| `/reset` | 清空对话并重置虚拟患者和模拟环境 |
| `/quit` | 退出 |

不加载患者场景也可以使用知识问答：

```bash
python3 -m linbot_voice_agent --mode llm --show-sources
```

## 3. 虚拟医院的规模与规则

| 模块 | 当前内容 |
|---|---|
| 空间 | 3 层、12 地点、连通路网与虚拟电梯边 |
| 服务 | 6 类服务，包含门诊、检验、影像、药房、咨询和无障碍支持 |
| 流程 | 5 条：首次就诊、预约门诊、已缴费取药、已安排检验、人工协助后就诊 |
| 患者 | 12 个无真实身份信息的档案 |
| 导航状态 | idle、running、paused、completed、cancelled、failed、emergency；取消后回 idle |
| 异常 | 导航失败、超时、路径中断、目的地关闭、低电量、紧急状态、解除紧急状态 |

到达不等于报到，报到不等于完成就诊，前往药房不等于已取药。患者自述不会直接修改这些事实。流程规则与模拟事件共同控制状态转换。

## 4. 医院知识与合成对话分开

`seeds/` 是事实、流程和患者状态的唯一可编辑源；`scenarios/` 是测试表达与多轮场景，绝不作为检索知识。

```text
seeds/
  hospital.json             # 地点、路网、服务知识与来源
  workflows.json            # 前置条件、步骤、完成事件
  patient_profiles.json     # 虚拟患者状态组合
  exceptions.json           # 异常事件目录
scenarios/
  seed_dialogues.json       # 12 个单轮语义测试种子
  multi_turn.json           # 6 个多轮场景（含完整就诊、暂停、换目的地等）
```

修改种子后同步安装包内副本：

```bash
python3 scripts/sync_seeds.py
```

开发时也可直接读取源文件：`--seed-dir seeds`。

LLM 可以扩写表达与多轮用户行为，但不能修改事实、规则、操作员事件或预期结果。生成器已实现，需配置密钥后运行：

```bash
python3 -m linbot_voice_agent.synthetic_data generate --output scenarios/generated_dialogues.json --variants 3
python3 -m linbot_voice_agent.synthetic_data generate-multiturn --input scenarios/multi_turn.json --output scenarios/generated_multiturn.json
```

生成结果标记 `needs_human_review`，需检查是否语义漂移。生成器拒绝写入事实种子目录及覆盖现有文件。当前仓库只包含种子，未用真实 LLM 扩写数据。

真实模型评估（会产生 API 请求）：

```bash
python3 -m linbot_voice_agent.synthetic_data evaluate --output scenarios/evaluation.json
python3 -m linbot_voice_agent.synthetic_data evaluate-multiturn --input scenarios/multi_turn.json --output scenarios/evaluation_multiturn.json
```

## 5. 实现方式

- 普通输入首先调用 LLM 规划，携带最近最多 12 条历史消息与当前状态。
- 查询动作先检索本地医院知识，再让 LLM 基于本轮证据生成回答，并校验引用 ID。
- 当前检索为中文双字片段与关键词匹配；规模小，便于检查，不依赖向量数据库。
- 工具层校验白名单、地点有效性、确认令牌和 60 秒有效期。确认一次即失效。
- 模型错误或非法输出时，只回退本地信息；不凭失败输出启动新动作。
- 模拟器发出带事件 ID、任务 ID 的进度和完成事件，忽略过期任务的完成事件和重复事件。
- 固定控制结果由程序生成，避免让模型编造“已到达”“已通知医护”。

## 6. 测试与边界

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖原有规则、模型协议、检索与引用、确认失效、防重复执行、完整就诊流程、12 个患者的流程可完成性、异常中断、非法跳步、数据隔离和种子一致性。模型调用在自动测试中使用 mock；真实模型语义质量、供应商兼容性和现场语音尚未验证。

事实引用 ID 校验不能证明整段回答完全忠于证据；语义理解、临床风险识别和否定处理仍可能出错。原型只用于虚拟场景。真实机器人接入仍需独立异步控制、急停、状态锁与真实反馈验证。

对话仅保留内存，模型模式会把当前输入、最近历史、虚拟患者状态及检索资料发送至所配置服务。每次请求超时 15 秒，问答最多两次请求；当前终端同步等待，不具备模型请求期间实时打断能力。

详细说明：[架构](docs/architecture.md) · [虚拟医院](docs/synthetic_environment.md) · [知识库维护](docs/knowledge.md) · [真机接入准备](docs/phase2.md)
