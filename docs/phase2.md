# VBOT EDU 接入：版本检查、语音输入与真实任务边界

## 当前实现与设备记录

设备为 VBOT EDU 足式版（`foot_quadruped`），用户提供版本 `v5.0.16-2026070701edu`。
暂未验证该字符串所属组件、设备应用版本或接口可用性。官方文档目标为应用 **V1.6.0**，
不能用系统镜像构建号替代应用版本，也不能认为安装了 ROS 2 Humble 就已经具备机器人接口。

本项目已实现模拟陪诊、离线环境报告、最终识别文本过滤器，以及 ROS 2 ASR/TTS 语音闭环入口。
[可运行语音闭环](voice-loop.md) 包含启动和测试命令；真机语音尚待现场验收。
**尚未实现原生 Aorta 传输、真实导航或 MCP 服务。** `NaturalAgent` 的 MockRobot 限制继续保留。

官方依据（后续接入时再次核对版本）：

- [兼容矩阵](https://github.com/VitaDynamics/vbot-lab/blob/main/docs/compatibility.md)
- [Python SDK](https://github.com/VitaDynamics/vbot-lab/blob/main/packages/aorta/python/README.md)
- [Aorta / ROS 2](https://github.com/VitaDynamics/vbot-lab/blob/main/docs/interfaces/aorta-ros2.md)
- [语音输入](https://github.com/VitaDynamics/vbot-lab/blob/main/docs/interfaces/audio.md)
- [设备 Agent 集成](https://github.com/VitaDynamics/vbot-lab/blob/main/docs/interfaces/agent/README.md)

## 1. Ubuntu 本地环境报告

在 Ubuntu 项目目录执行，命令不联系机器人：

```bash
python3 -m linbot_voice_agent --vbot-check
```

默认档案为安装包中的 `data/vbot-profile.json`。如需单独保存现场信息，使用相同的三个字段：

```json
{
  "robot_type": "foot_quadruped",
  "reported_version": "v5.0.16-2026070701edu",
  "application_version": null
}
```

```bash
python3 -m linbot_voice_agent --vbot-check --vbot-profile path/to/profile.json
```

只记录非敏感版本信息；不要放密钥、会话字典或患者数据。程序拒绝额外字段。
报告仅检查包元数据和工具可发现性，不加载原生 SDK，不证明 ABI、会话、桥接或设备连接正常。
退出码 0 表示报告输出成功；`hardware_ready` 始终为 false。

Ubuntu ARM64 必须选择匹配的 `linux_aarch64` SDK wheel，而不是 Mac 或 x86_64 wheel。
文档参考 Release `edu-sdk-2026.9.24` 的配对为 `aorta-sdk==2026.9.23`、
`aorta-msgs==2026.9.23`、`vbot-edu-msgs==2026.9.24`、`flatbuffers==25.12.19`。
安装前以该 Release 的 manifest 和校验和为准；诊断命令不会自动安装或升级。
普通 `pip install .` 仍不引入硬件依赖，离线演示继续可用。

## 2. 先确认设备能力，再选择传输

Aorta 是原生通信层；ROS 2 是部分兼容映射。两条路径分开核验：

- Aorta：核验设备交付的会话配置、路由权限和 Schema，使用匹配的 SDK。不要从名字推断权限。
- ROS 2：核验实际运行的桥接、可发现的路由、消息包与 QoS。`rclpy` 可导入只证明 Python 环境的一部分。

旧配置可能静默丢弃不允许的路由，表现为超时；不能把所有超时都归因于 Python 安装失败。
在正确加载环境、可访问机器人接口的 Ubuntu/设备终端，先只读查看元数据：

```bash
timeout 15s ros2 topic list --no-daemon -t
timeout 15s ros2 service list -t
timeout 15s ros2 action list -t
timeout 15s ros2 interface show aorta_msgs/msg/AsrResult
```

上面命令需要现场显式执行；`--vbot-check` 不会执行它们。列表有名称仍不证明有实时发布者。
不要通过向 `/joy` 或 `/vel_cmd` 发布测试值来检查环境；本应用不使用底层速度作为导航替代品。

## 3. 最终识别文本优先，按来源过滤

官方 `/speech/asr_result` 是最终文本：Aorta 类型 `aorta.topic.speech.AsrResult`，
ROS 2 类型 `aorta_msgs/msg/AsrResult`。仍需现场确认。`provider` 的 `1/BODY` 表示机身麦克风，
`2/TAG` 表示遥控器 UWB 输入；`0/UNKNOWN` 不能自动归为任一来源。
UWB 输入传输结束不等于 ASR 完成，也不等于 TTS 播放结束。

`linbot_voice_agent.voice_input.FinalAsrFilter` 是无 SDK 依赖的输入组件：

```python
from linbot_voice_agent.voice_input import FinalAsrFilter

gate = FinalAsrFilter(providers=(1,))  # 只接受机身麦克风
# 以下是适配器构造的数据示例，不是 SDK 调用或已核验的消息字段。
transcript = gate.accept(provider=1, text="心内科在哪里", event_id="session-a:event-1")
```

适配器必须根据现场实际消息提供稳定、含会话范围的事件 ID；重投递不能产生新 ID。
不要用文本内容做 ID，否则两次真实的“确认”会互相覆盖。缓存容量和有效期有限，
不提供持久重放防护，重连后过时消息必须在适配器层丢弃。
当前过滤器只处理最终文本，**不要将 `/voice/event` 的联合类型载荷直接传入**。
如果选择事件流，需要先核验 `payload_type`、`is_final`、`is_reject` 和会话标识。
不要同时消费两路输入来触发同一任务，除非已经实现跨流事件关联。

当前 ROS 2 适配器使用单任务工作线程，忙碌时直接丢弃新输入，不积压过时语音。
它采用半双工，暂不支持语音打断；Ctrl+C 退出时会尝试停止当前播报，但不声称停止已确认。
双工打断、重新连接及真实现场回声效果仍需单独实现和验证。
过滤器不授予运动权限，也不绕过现有确认令牌。

## 4. 真实任务需要独立生命周期

在移除 MockRobot 限制前实现：实际地图/楼层/点位映射、任务 ID、执行反馈、
超时、取消请求与取消确认、断连状态，以及独立于模型请求的停止路径。
任务受理不等于到达；只有关联当前任务的真实完成反馈才能更新患者位置。
模型不得生成真实坐标、底层速度或虚构完成事件。未提供的乘梯、医护联络能力应明确不可用。
硬件急停独立于模型和网络。真实求助只有服务确认后才能播报“已通知”。

如以后复用设备内置 Agent，可把医院知识查询/流程工具作为开发者 MCP 服务提供。
这与 Aorta 硬件接口不同，也不意味着把 Python 文件上传后就自动成为设备 Skill。
在确认设备应用版本及接口契约前，不注入设备 AGENTS.md 或替换设备服务。

## 5. 验证分层

GitHub Actions 在 Ubuntu 22.04 上验证 Python 单元测试、模拟演示和离线诊断；
额外的 `scripts/test_ros_voice_loop.py` 在加载消息包的 ROS 2 环境验证真实通信闭环。
不连接设备的测试通过不代表 ARM64 SDK、真实声音或真机动作通过。
现场先验证语音问答与模拟任务，最后再单独验收真实导航。
