"""ROS 2 VBOT speech adapter. Run with python3 -m linbot_voice_agent.ros_voice.

Only subscribes to ASR/voice events and calls SetSpeak; no motion publisher,
navigation client, hardware robot adapter or dynamic tool execution exists here.
"""
import argparse
import json
import threading
import time

from .voice_loop import PlaybackTracker, VoiceLoop


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


class RosSpeaker:
    def __init__(self, node, service_type, service, timeout):
        self.node = node
        self.service_type = service_type
        self.client = node.create_client(service_type, service)
        self.timeout = timeout
        self.tracker = PlaybackTracker()
        self._closed = threading.Event()
        self._started_ns = 0
        self._send_lock = threading.Lock()
        self._active_request = None

    def on_event(self, msg):
        if not msg.has_aorta_header or msg.aorta_header.publish_stamp_ns < self._started_ns:
            return
        if msg.payload_type == msg.PAYLOAD_TYPE_TTS_START_EVENT:
            payload = msg.payload_tts_start_event
            if not payload.sn or not payload.audio_session_id:
                return
            identity = (msg.session_id, msg.generation, payload.sn, payload.link, payload.audio_session_id)
            self.tracker.start(payload.text, identity)
        elif msg.payload_type == msg.PAYLOAD_TYPE_TTS_PLAYBACK_COMPLETE_EVENT:
            payload = msg.payload_tts_playback_complete_event
            identity = (msg.session_id, msg.generation, payload.sn, payload.link, payload.audio_session_id)
            self.tracker.finish(identity, payload.interrupted)

    def _wait_response(self, future, timeout):
        deadline = time.monotonic() + timeout
        while not future.done():
            if self._closed.wait(0.02) or time.monotonic() >= deadline:
                future.cancel()
                raise TimeoutError('TTS service response unavailable')
        response = future.result()
        if response is None or not response.success or response.error_code != 0:
            raise RuntimeError('TTS service rejected request')

    def speak(self, text, request_id):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError('Invalid speech text')
        if not self.client.service_is_ready():
            raise RuntimeError('TTS service unavailable')
        request = self.service_type.Request()
        request.target_state = 1
        request.mode = request.HUMAN_VOICE
        request.req_id = request_id
        request.pre_check = False
        request.human_language_text = text
        with self._send_lock:
            if self._closed.is_set():
                raise RuntimeError('Speaker closed')
            self.tracker.begin(text)
            self._started_ns = time.time_ns()
            self._active_request = request_id
            future = self.client.call_async(request)
        self._wait_response(future, min(10, self.timeout))
        emit({'event': 'tts_accepted', 'request_id': request_id})
        self.tracker.wait(self.timeout)
        with self._send_lock:
            self._active_request = None

    def close(self):
        with self._send_lock:
            self._closed.set()
            self.tracker.abort()
            if self._active_request and self.client.service_is_ready():
                request = self.service_type.Request()
                request.target_state = 0
                request.mode = request.HUMAN_VOICE
                request.req_id = self._active_request
                self.client.call_async(request)
                emit({'event': 'tts_stop_requested', 'confirmed': False})
            self._active_request = None


def main(argv=None):
    parser = argparse.ArgumentParser(description='VBOT ROS 2 半双工语音闭环（运动始终模拟）')
    parser.add_argument('--execute', action='store_true', help='实际订阅语音并调用播报服务')
    parser.add_argument('--check', action='store_true', help='只检查消息包和可发现接口，不订阅内容或播报')
    parser.add_argument('--mode', choices=('offline', 'llm'), default='offline')
    parser.add_argument('--provider', choices=('body', 'tag'), default='body')
    parser.add_argument('--asr-topic', default='/speech/asr_result')
    parser.add_argument('--voice-topic', default='/voice/event')
    parser.add_argument('--tts-service', default='/set_speak')
    parser.add_argument('--discovery-timeout', type=float, default=10)
    parser.add_argument('--playback-timeout', type=float, default=60)
    parser.add_argument('--duration', type=float, default=300, help='本次实验最长秒数')
    args = parser.parse_args(argv)
    if args.execute == args.check:
        parser.error('选择 --check 或 --execute 其中一个')
    if not all(0 < value <= 3600 for value in (args.discovery_timeout, args.playback_timeout, args.duration)):
        parser.error('时间参数必须在 (0, 3600] 秒内')
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from aorta_msgs.msg import AsrResult, VoiceEvent
        from function_msgs.srv import SetSpeak
    except ImportError:
        emit({'event': 'startup_failed', 'reason': 'missing_ros_or_vbot_message_packages',
              'hint': '加载 ROS 2 Humble 和匹配的 aorta_msgs/function_msgs 工作空间。'})
        return 2

    rclpy.init(args=[])
    node = rclpy.create_node('linbot_voice')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    loop = speaker = None
    try:
        deadline = time.monotonic() + args.discovery_timeout
        requirements = {args.asr_topic: 'aorta_msgs/msg/AsrResult',
                        args.voice_topic: 'aorta_msgs/msg/VoiceEvent'}
        while True:
            topics = dict(node.get_topic_names_and_types())
            services = dict(node.get_service_names_and_types())
            available = all(kind in topics.get(name, []) and node.count_publishers(name) > 0
                            for name, kind in requirements.items())
            available = available and 'function_msgs/srv/SetSpeak' in services.get(args.tts_service, [])
            if available or time.monotonic() >= deadline:
                break
            executor.spin_once(timeout_sec=0.1)
        emit({'event': 'interface_check', 'available': bool(available),
              'asr_topic': args.asr_topic, 'voice_topic': args.voice_topic,
              'tts_service': args.tts_service, 'playback_verified': False})
        if not available:
            return 2
        if args.check:
            return 0
        from .agent import Agent
        if args.mode == 'llm':
            from .natural_agent import NaturalAgent
            from .llm import ChatModel
            agent = NaturalAgent(ChatModel())
        else:
            agent = Agent()
        speaker = RosSpeaker(node, SetSpeak, args.tts_service, args.playback_timeout)
        if not speaker.client.wait_for_service(timeout_sec=args.discovery_timeout):
            raise RuntimeError('TTS service unavailable')
        loop = VoiceLoop(agent, speaker, providers=(1 if args.provider == 'body' else 2,), emit=emit)
        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT,
                         durability=DurabilityPolicy.VOLATILE)
        def on_asr(msg):
            if not msg.has_aorta_header or not msg.aorta_header.publisher_node:
                emit({'event': 'input_rejected', 'reason': 'missing_source_header'})
                return
            header = msg.aorta_header
            identity = f'{header.publisher_node}:{header.publish_stamp_ns}:{header.sequence}'
            loop.submit(provider=int(msg.provider), text=msg.text, event_id=identity,
                        captured_ns=int(header.publish_stamp_ns))
        node.create_subscription(AsrResult, args.asr_topic, on_asr, qos)
        node.create_subscription(VoiceEvent, args.voice_topic, speaker.on_event, qos)
        emit({'event': 'listening', 'mode': args.mode, 'provider': args.provider,
              'half_duplex': True, 'motion_backend': 'mock'})
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and not loop.faulted:
            executor.spin_once(timeout_sec=0.1)
        return 1 if loop.faulted else 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        emit({'event': 'startup_or_runtime_failed', 'error_type': type(exc).__name__})
        return 2
    finally:
        if loop:
            loop.close()
        elif speaker:
            speaker.close()
        # Allow a pending stop request to leave the process; do not claim success.
        if rclpy.ok():
            executor.spin_once(timeout_sec=0.2)
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
