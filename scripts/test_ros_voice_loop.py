"""Actual ROS 2 pub/sub + service contract test; fake hardware, no real audio.

Requires the test message overlay. Uses localhost and an isolated ROS domain.
Runs the exact production adapter in a separate process and verifies two turns,
duplicate filtering, playback event correlation, and no transcript logs.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ['ROS_LOCALHOST_ONLY'] = '1'
os.environ['ROS_DOMAIN_ID'] = '197'

import rclpy
from rclpy.executors import SingleThreadedExecutor
from aorta_msgs.msg import AsrResult, VoiceEvent
from function_msgs.srv import SetSpeak


def main():
    rclpy.init()
    node = rclpy.create_node('linbot_fake_voice_device')
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    asr = node.create_publisher(AsrResult, '/linbot_test/asr', 10)
    voice = node.create_publisher(VoiceEvent, '/linbot_test/voice', 10)
    requests = []
    scheduled = []

    def stamp(msg):
        msg.has_aorta_header = True
        msg.aorta_header.publisher_node = 'linbot_fake_voice_device'
        msg.aorta_header.publish_stamp_ns = time.time_ns()
        return msg

    def utterance(text, provider=1):
        msg = stamp(AsrResult())
        msg.provider = provider
        msg.text = text
        msg.aorta_header.sequence = msg.aorta_header.publish_stamp_ns
        return msg

    def on_speak(request, response):
        if request.target_state == 1:
            assert request.mode == 1 and not request.pre_check
            assert request.req_id.startswith('linbot-')
            assert request.human_language_text
            requests.append(request)
            start = stamp(VoiceEvent())
            start.session_id, start.generation = 1, len(requests)
            start.payload_type = start.PAYLOAD_TYPE_TTS_START_EVENT
            payload = start.payload_tts_start_event
            payload.text = request.human_language_text
            payload.sn, payload.link, payload.audio_session_id = request.req_id, 4, len(requests)
            end = stamp(VoiceEvent())
            end.session_id, end.generation = start.session_id, start.generation
            end.payload_type = end.PAYLOAD_TYPE_TTS_PLAYBACK_COMPLETE_EVENT
            payload = end.payload_tts_playback_complete_event
            payload.sn, payload.link, payload.audio_session_id = request.req_id, 4, len(requests)
            scheduled.extend([(time.monotonic() + .15, voice, start),
                              (time.monotonic() + .2, asr, utterance('这是扬声器回声')),
                              (time.monotonic() + .4, voice, end)])
        response.success, response.error_code = True, 0
        return response

    node.create_service(SetSpeak, '/linbot_test/speak', on_speak)
    command = [sys.executable, '-m', 'linbot_voice_agent.ros_voice', '--execute',
               '--asr-topic', '/linbot_test/asr', '--voice-topic', '/linbot_test/voice',
               '--tts-service', '/linbot_test/speak', '--duration', '8', '--playback-timeout', '3']
    process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first_at = None
    second_sent = False
    try:
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
            now = time.monotonic()
            if first_at is None and asr.get_subscription_count() and voice.get_subscription_count():
                first_at = now
                asr.publish(utterance('不应接受', provider=0))
                message = utterance('心内科在哪里')
                asr.publish(message)
                scheduled.append((now + .1, asr, message))
            if first_at and not second_sent and len(requests) == 1 and now - first_at > 2:
                asr.publish(utterance('那药房呢'))
                second_sent = True
            for event in list(scheduled):
                at, publisher, message = event
                if at <= now:
                    if isinstance(message, VoiceEvent):
                        stamp(message)
                    publisher.publish(message)
                    scheduled.remove(event)
        if process.poll() is None:
            process.kill()
            raise AssertionError('Adapter did not terminate within deadline')
        output, errors = process.communicate(timeout=3)
        print(output, end='')
        if errors:
            print(errors, file=sys.stderr)
        assert process.returncode == 0, process.returncode
        records = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        assert len(requests) == 2, f'Expected two speech requests, got {len(requests)}'
        assert len([r for r in records if r['event'] == 'playback_completed']) == 2
        assert '心内科在哪里' not in output and '这是扬声器回声' not in output
        print(json.dumps({'result': 'PASS', 'turns': 2, 'real_ros_transport': True,
                          'real_audio': False, 'robot_contacted': False}))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
