# Linbot Voice Agent · 聆灵陪诊语音交互

第一阶段可运行原型：**文本输入 → 意图与会话状态 → 医院知识查询 / 模拟工具 → 文字回复**。

当前仅有文本与模拟机器人，未连接 ASR、TTS、ROS 2 或真实导航。所有内置医院地点和路线都是虚构示例，不能用于实际导诊。

## 快速开始（Python 3.10+）

不需要模型密钥或第三方运行依赖：

```bash
git clone https://github.com/Hu-py/linbot-voice-agent.git
cd linbot-voice-agent
python3 -m linbot_voice_agent --demo --show-tools
python3 -m linbot_voice_agent
```

可选安装命令行入口：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
linbot --demo
```

交互试试：

```text
心内科在哪里
怎么过去
那药房呢
带我去药房
好
等一下，我想先去厕所
好
到了吗
取消
```

`/reset` 清空会话并取消模拟任务；`/quit` 退出。`--show-tools` 展示意图与模拟工具事件。不会自动生成到达反馈。

## 已实现

- 医院地点问答、路线说明和示例就诊流程查询。
- 会话内目的地记忆、代词引用、挂号状态记录、导航二次确认。
- 导航、暂停、恢复、取消、状态查询、人工求助的模拟接口。
- 未知目的地澄清、多地点澄清、部分否定表达识别和失效确认清理。
- 紧急关键词优先处理，明确说明求助未实际发出；用药及诊断类关键词转介医护。
- 可选模型 API 意图分类、输出校验、超时回退。
- 20 项自动测试和 GitHub Actions 配置。

离线模式使用规则和模板，不是通用大模型。模型模式对规则未覆盖的表达进行意图分类，回复仍以受控模板和本地知识为准；暂未实现开放式生成聊天、RAG、语音打断或完整临床风险识别。

## 可选模型模式

支持提供 `/chat/completions` 的 HTTPS 服务，返回文本需为 JSON。具体模型的兼容性需用真实账号验证。密钥仅从环境变量读取，不要提交到仓库。

```bash
export LINBOT_BASE_URL='https://your-provider.example/v1'
export LINBOT_MODEL='your-model-name'
read -rsp 'API key: ' LINBOT_API_KEY; echo
export LINBOT_API_KEY
python3 -m linbot_voice_agent --mode llm --show-tools
```

`.env.example` 仅作配置说明，不会自动加载。请求超时为 15 秒；格式错误、未知地点 ID、未知意图和请求失败均回退。对话默认只存在内存中；模型模式会将未被本地规则识别的输入及最小会话状态发送至配置的服务，请先用模拟信息测试。

## 修改医院数据

复制 `linbot_voice_agent/data/hospital.example.json`，修改 `name`、`places` 和 `flow`，再执行：

```bash
python3 -m linbot_voice_agent --hospital /path/to/hospital.json
```

地点需要唯一 `id`、名称 `name`、别名列表 `aliases`、位置 `location`、文字路线 `route`。只有经医院核实的数据才应设 `is_example: false`。当前路线是固定服务台起点的文字说明，不根据机器人实时位置规划路径；楼层信息不意味着具备乘梯能力。

## 代码结构

```text
linbot_voice_agent/
  agent.py          # 会话、规则、确认与意图路由
  knowledge.py      # 加载、校验、匹配医院信息
  llm.py            # 可选模型意图分类及输出校验
  robot.py          # RobotPort 接口与 MockRobot
  cli.py            # 交互终端、演示与工具事件展示
  data/             # 虚构医院数据
 tests/             # 行为与模型响应测试
 docs/              # 架构说明和第二阶段接入清单
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖上下文、确认、否定、任务切换、未知地点、模型超时/非法输出、模拟动作失败及求助场景。模型请求测试使用 mock；本版本未用真实密钥调用外部模型，也未测试真机。

下一步见 [第二阶段接入清单](docs/phase2.md) 和 [架构说明](docs/architecture.md)。
