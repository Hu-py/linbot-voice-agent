# ROS 2 语音闭环

Ubuntu 中先 source ROS Humble 及与设备匹配的消息工作空间，然后运行：

```bash
python3 -m linbot_voice_agent.ros_voice --check
python3 -m linbot_voice_agent.ros_voice --execute --provider body --duration 300
```

检查要求发现 `/speech/asr_result` (`aorta_msgs/msg/AsrResult`)、`/voice/event` (`aorta_msgs/msg/VoiceEvent`)、`/set_speak` (`function_msgs/srv/SetSpeak`)。缺少 SDK/消息或接口即失败。`--check` 只发现接口；`--execute` 会请求真实扬声器，需要现场选择。默认 offline 使用旧规则 Agent；`--mode llm` 使用已有模型配置。运动始终是旧 MockRobot；二维具身 demo 是单独入口，当前不将 ASR 连接真机导航。

输入只接受指定 provider 的新鲜最终识别结果，按消息标识去重。单工作线程处理输入，处理及播放期间丢弃 ASR，播放结束后保持 0.8 秒保护。模型输出不能直接发送机器人速度。TTS 服务接受仅为受理；必须收到匹配文本的开始事件以及同一 session/generation/sn/link/audio_session_id 的完成事件才算播放完成。协议没有可靠 req_id 回传映射，因此要求本程序独占语音输出；并发其他 TTS 发布者仍需现场验证。播放中断/超时锁定故障，需人工重启，不继续监听。日志只记录事件元数据，不记录原始识别文本。

当前半双工不支持语音打断。硬件停止必须独立于语音/LLM。程序退出时停止请求只是尽力尝试，不宣称已确认停止。

## 不接机器人测试

下面脚本仅在新建的用户目录构建官方固定版本的最小消息依赖，不安装系统包、不覆盖厂家 SDK；要求已安装 ROS Humble 的 colcon、ament_cmake、rosidl 生成工具。脚本参数见 `--help`：

```bash
python3 scripts/prepare_voice_test_ws.py --help
```

构建后 source 其 `install/setup.bash`，在项目目录运行：

```bash
python3 scripts/test_ros_voice_loop.py --scenario success
python3 scripts/test_ros_voice_loop.py --scenario reject
python3 scripts/test_ros_voice_loop.py --scenario timeout
```

测试使用 ROS_LOCALHOST_ONLY=1、独立 domain 197、`/linbot_test/*` 话题和模拟设备服务，启动生产语音模块独立进程。成功场景核验两轮播放、重复输入过滤、未知 provider 拒绝、回声过滤；失败场景核验服务拒绝与播放超时锁定。Ubuntu 22.04 ARM64 / ROS Humble 上三场景通过。没有采集或播放真实声音，没有连接机器人；测试消息 overlay 不证明用户设备 SDK 兼容。
