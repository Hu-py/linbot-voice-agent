# Linbot v0.3：一楼二维陪诊 MVP

## 运行

在 Ubuntu 22.04 / Python 3.10+ 的项目根目录运行，无需 LLM 密钥、ROS 或 SDK：

```bash
python3 -m linbot_voice_agent --embodied-demo --backend mock
python3 -m unittest discover -s tests -v
python3 -m linbot_voice_agent --embodied-demo --backend vbot-aorta
python3 -m linbot_voice_agent --embodied-demo --backend vbot-ros2
```

前一条使用固定 DemoModel，经过既有 NaturalAgent 与受控工具层。它不证明真实 LLM 理解质量；可加 `--mode llm` 使用既有模型配置。后两条仅输出 dry-run，不访问设备、不执行动作。旧 `--demo`、`--scenario-demo`、交互 CLI 保留。

## 完整闭环

固定对话为“我想去挂号处，应该怎么走？”→“能带我过去吗？”→“确认出发。”。第一轮返回知识和入口→服务台→挂号处的二维路线；第二轮生成限时确认请求；确认后进行可达性预检、welcome_wave、读取模拟位姿、重新规划及执行器移动。抵达事件触发播报、机器人位置及患者 visited 更新。`registered` 保持 false，直到独立操作员事件 `registration_completed`。

技能、任务和地图均在 `linbot_voice_agent/` 包内：

- `world/`：HospitalWorld 分开管理知识、患者、机器人、任务；EventStore 去重事件；SpatialMap 检查几何和路网。
- `maps/synthetic_hospital_1f.json`：独立 synthetic 地图，包含坐标系、分辨率、原点、可通行矩形、障碍、路网、导航目标与机器人半径。坐标完全虚构。
- `orchestrator/`：Dijkstra 规划与任务管理。边必须位于可通行区域并避开按机器人半径膨胀的障碍，不能靠目标坐标直接穿墙。
- `skills/`：播报、白名单动作和导航入口。
- `adapters/`：模拟执行器及默认禁止运动的 Aorta/ROS 2 边界。
- `safety/motion_guard.py`：动作白名单、地图来源和硬件条件检查。

位姿只有 `Pose2D(x,y,yaw)`。本轮地图仅一楼；保留原始三层医院种子和旧模拟器，三楼知识仍可查询，但二维任务拒绝跨楼层。没有电梯、楼梯、跟随、三维规划或任意 LLM 坐标/速度入口。

## 生命周期与可信边界

任务唯一 ID；状态为 pending、accepted、running、succeeded、failed、cancelled、unknown。暂停是 running 的 paused 子状态。进度由执行器事件推进；重复 ID、旧序号、旧任务反馈及取消之后的到达事件被丢弃。取消请求未获确认时保持 unknown 并阻止新任务，超时同样先取消；暂停时间计入总超时。安全暂停/取消调用不等待模型。

到达判定检查目标节点、位置误差和可通行性；播报失败则任务失败，但已确认的到达事实保留。患者业务事件不暴露为模型工具，只通过独立操作员入口提交；这是可信调用方接口，不是网络鉴权系统。事件日志目前在内存，重启不恢复任务。中途取消后若处于边上，拒绝从节点假定重启；需要重新定位确认，而不是偷偷吸附到节点。

## 真实适配器与未验证项

依据官方 [vbot-lab 固定版本](https://github.com/VitaDynamics/vbot-lab/tree/26bd51d9397a46ed3cc008abeb46c8b9f36f3279) 检查协议。用户报告版本 `v5.0.16-2026070701edu`；应用版本与 SDK 的现场匹配尚未确认。

`VbotAortaAdapter.inspect(authorized=True)` 可限时读取 RCP/状态 schema；`read_status(authorized=True)` 采样 `/slam/status`、`/odometry`，返回原始文本，不把它冒充经过验证的机器人定位。ROS 2 适配器支持有界 topic/service/action 发现；未猜测 Nav2 或 odometry 主位姿就是机身位姿。`verified_planar_pose` 仅接受调用方已经核对的非 synthetic 地图、坐标系与机身二维位姿。

`/slam/get_path_to_target` 是路径查询，不是导航执行。真实路径请求、约束跟踪、障碍反馈与抵达需在实机确认接口后连接同一任务状态机；当前没有真实运动执行器，所有运动方法拒绝执行。`welcome_wave` 对应候选 RCP `actions/WAVE.json`，需要 LIGHT、SCREEN、LEGS、HEAD，AUDIO_OUT 可选；尚未执行验证。语音可验证接口实现见 [语音闭环](voice-loop.md)。

即使提供所有授权条件，synthetic 地图也不能用于真机。实际启用需要真实测绘/SLAM 地图、目标核对、版本与控制权限、资源空闲、现场监护和停止手段。本轮不修改系统、固件、网络、服务，不启动 Zenoh；软件 safe-stop 不代替硬件急停。

## 本轮验证

在 Ubuntu 22.04 aarch64 / Python 3.10.12 完成单元与集成回归、入口到挂号处 demo。覆盖未确认、无目标、跨楼层、断路、取消及未确认取消、超时、欢迎失败、障碍、重复/旧反馈、暂停恢复、独立业务事件、SDK 缺失及真实地图限制。ROS Humble 下另以本机隔离域验证真实 pub/sub/service 通信：语音成功两轮、服务拒绝、播放超时。设备模拟不涉及实际声音。

无机械狗接入，未验证真实麦克风、扬声器、动作、SLAM、路径跟踪或真实模型服务。测试通过不能视为真机验收。
