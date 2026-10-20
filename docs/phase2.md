# 第二阶段：VBot 语音闭环接入准备

参考工程：
- https://github.com/VitaDynamics/vbot_ros2_msgs
- https://github.com/fang2544/-shenicest-fission-Linbot-a-robot-dog-for-AI-Patient-Companion

本次仅实现独立文本核心，未复制上述仓库代码，未核验当前固件接口。不要根据消息类型名假定实际话题或服务名。

## 1. 采集真机接口（先只读）

在已正确加载机器人 ROS 环境的终端运行：

```bash
ros2 topic list -t
ros2 service list -t
ros2 action list -t
ros2 node list
```

确认实际语音识别话题后，再运行 `ros2 topic info -v <实际话题>` 与 `ros2 interface show <实际类型>`，记录消息字段、QoS、来源、置信度和拒绝标志。相同方式核验 TTS 服务及完成/打断反馈。请勿将尖括号占位符直接执行。

## 2. 先做纯语音问答

新增独立 ament_python 适配包，依赖此核心。订阅已验证的 ASR 话题，将最终识别结果入队；工作线程调用 Agent，单一 TTS 适配器播报。消息去重、低置信度澄清、播报回声过滤、唤醒会话超时和打断需分别验证。

## 3. 继续使用 MockRobot

语音阶段先保留模拟导航，测试“查询 → 追问 → 请求 → 确认 → 暂停 → 取消”。未核验真机接口前不注册任何真实运动工具。

## 4. 再接任务服务

使用已有高层导航能力；校验实际点位 ID、地图、当前楼层和可达性。不要用模型产生坐标或底层运动指令。补充异步任务 ID、超时、取消确认和真实完成/失败反馈。硬件急停独立于模型和网络。

实际医院部署前，还需核实医院知识、乘梯能力、现场人员联络方式和数据授权。求助结果只有真实服务确认成功后才能播报“已通知”。
