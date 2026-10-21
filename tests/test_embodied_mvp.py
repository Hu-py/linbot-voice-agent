from copy import deepcopy
from dataclasses import asdict
import io
import json
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from linbot_voice_agent.embodied import create_app, run_demo, DemoModel
from linbot_voice_agent.world.spatial_map import SpatialMap, Pose2D
from linbot_voice_agent.safety.motion_guard import MotionGuard, HardwareConditions
from linbot_voice_agent.adapters.vbot_aorta import VbotAortaAdapter
from linbot_voice_agent.adapters.vbot_ros2 import VbotRos2Adapter, verified_planar_pose
from linbot_voice_agent.orchestrator.task_manager import TASK_STATES


class EmbodiedTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.world, self.robot, self.manager, self.agent = create_app(clock=lambda: self.now, timeout=20)

    def confirm(self):
        self.agent.respond('我想去挂号处，应该怎么走？')
        self.agent.respond('能带我过去吗？')
        return self.agent.respond('确认出发。')

    def finish(self):
        for _ in range(20):
            self.manager.tick()
            if self.manager.active.state != 'running':
                break

    def test_full_closed_loop_business_not_inferred(self):
        answer = self.agent.respond('我想去挂号处，应该怎么走？')
        self.assertIn('医院入口 → 服务台 → 挂号处', answer.text)
        self.assertEqual(self.robot.gestures, [])
        self.agent.respond('能带我过去吗？')
        self.assertEqual(self.robot.gestures, [])
        reply = self.agent.respond('确认出发。')
        self.assertEqual(reply.intent, 'navigate')
        self.assertEqual(self.robot.gestures, ['welcome_wave'])
        self.finish()
        self.assertEqual(self.manager.active.state, 'succeeded')
        self.assertIn(self.manager.active.state, TASK_STATES)
        self.assertEqual(self.world.robot.pose, self.world.map.nodes['registration'])
        self.assertIn('registration', self.world.patient.visited)
        self.assertFalse(self.world.patient.facts['registered'])
        self.assertIn('已到达挂号处', self.robot.speech[-1])
        self.world.apply_business_event('registration_completed', 'operator-1', source='operator')
        self.assertTrue(self.world.patient.facts['registered'])
        self.assertFalse(self.world.apply_business_event('registration_completed', 'operator-1', source='operator'))

    def test_nonfinite_timeout_rejected(self):
        for timeout in (float('nan'), float('inf'), 0, -1):
            with self.assertRaises(ValueError):
                create_app(timeout=timeout)

    def test_readonly_samples_are_bounded_and_not_verified_pose(self):
        import subprocess
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            self.assertEqual(kwargs['timeout'], 15)
            return subprocess.CompletedProcess(command, 0, 'sample')
        with patch('shutil.which', return_value='/usr/bin/aorta'):
            result = VbotAortaAdapter().read_status(authorized=True, runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertFalse(result['verified'])
        self.assertIsNone(result['pose'])
        self.assertFalse(result['motion_enabled'])

    def test_direct_execute_without_confirmation_cannot_move(self):
        self.assertFalse(self.manager.execute('navigate', 'registration')['ok'])
        with self.assertRaises(ValueError):
            self.manager.navigate('registration')
        self.assertFalse(self.robot.gestures)

    def test_other_floor_offers_elevator_with_confirmation(self):
        for target in ('missing', 'cardiology'):
            with self.assertRaises(ValueError):
                self.manager.preview(target)
        reply = self.agent.respond('心内科在哪里？')
        self.assertIn('3楼', reply.text)
        self.assertIn('我可以陪你去电梯口', reply.text)
        self.assertFalse(self.robot.gestures)
        reply = self.agent.respond('能带我过去吗？')
        self.assertEqual(reply.intent, 'confirm')
        self.assertEqual(reply.text, '我可以陪你去电梯口。确认出发吗？')
        self.assertFalse(self.robot.gestures)
        self.agent.respond('确认出发。')
        self.finish()
        self.assertEqual(self.manager.active.destination, 'elevator_lobby')
        self.assertEqual(self.manager.active.state, 'succeeded')
        self.assertEqual(self.world.patient.current_location, 'elevator_lobby')
        self.assertNotIn('cardiology', self.world.patient.visited)
        self.assertEqual(self.robot.speech[-1], '到电梯口啦，我陪着你。')

    def test_first_floor_clinic_is_navigable(self):
        self.agent.respond('带我去一楼诊室')
        self.assertFalse(self.robot.gestures)
        self.agent.respond('确认出发。')
        self.finish()
        self.assertEqual(self.manager.active.state, 'succeeded')
        self.assertIn('clinic_1f', self.world.patient.visited)
        self.assertFalse(self.world.patient.facts['consultation_completed'])

    def test_unreachable_rejects_before_gesture(self):
        self.world.map.blocked_edges.add(frozenset(('service_desk', 'registration')))
        self.confirm()
        self.assertIsNone(self.manager.active)
        self.assertFalse(self.robot.gestures)

    def test_cancel_feedback_and_late_arrival(self):
        self.confirm()
        task_id = self.manager.active.task_id
        self.assertTrue(self.manager.cancel_navigation())
        self.assertEqual(self.manager.active.state, 'cancelled')
        self.assertFalse(self.manager.process_event(dict(event_id='late', task_id=task_id, sequence=999,
            kind='arrived', pose=asdict(self.world.map.nodes['registration']), node='registration', simulated=True)))
        self.assertNotIn('registration', self.world.patient.visited)

    def test_cancel_request_not_confirmation(self):
        self.confirm()
        self.robot.confirm_cancel = False
        self.assertFalse(self.manager.cancel_navigation())
        self.assertEqual(self.manager.active.state, 'unknown')
        with self.assertRaises(ValueError):
            self.manager.navigate('registration', confirmed=True)

    def test_timeout_stops_before_next_movement(self):
        self.confirm()
        pose = self.robot.pose
        self.now = 21
        self.manager.tick()
        self.assertEqual(self.manager.active.state, 'failed')
        self.assertEqual(self.manager.active.reason, 'timeout')
        self.assertEqual(self.robot.pose, pose)

    def test_gesture_failure_prevents_navigation(self):
        self.robot.fail_gesture = True
        self.confirm()
        self.assertEqual(self.manager.active.state, 'failed')
        self.assertIsNone(self.robot.plan)

    def test_obstacle_stops_motion(self):
        self.confirm()
        self.manager.tick(.2)
        pose = self.robot.pose
        self.world.map.blocked_edges.add(frozenset(('entrance', 'service_desk')))
        self.manager.tick()
        self.assertEqual(self.manager.active.reason, 'obstacle')
        self.assertEqual(self.robot.pose, pose)
        self.assertNotIn('registration', self.world.patient.visited)

    def test_duplicate_and_old_task_feedback(self):
        self.confirm()
        event = self.world.events.snapshot()[0]
        self.assertFalse(self.manager.process_event(event))
        self.manager.cancel_navigation()
        old_id = self.manager.active.task_id
        self.confirm()
        self.assertNotEqual(old_id, self.manager.active.task_id)
        event['event_id'], event['sequence'] = 'another', 999
        self.assertFalse(self.manager.process_event(event))

    def test_pause_resume_requires_confirmation_and_keeps_task(self):
        self.confirm()
        task_id = self.manager.active.task_id
        self.assertTrue(self.manager.pause_navigation())
        pose = self.robot.pose
        self.manager.tick()
        self.assertEqual(self.robot.pose, pose)
        self.assertFalse(self.manager.execute('resume')['ok'])
        self.agent.respond('继续')
        self.assertEqual(self.manager.status, 'paused')
        self.agent.respond('确认恢复')
        self.assertEqual(self.manager.status, 'running')
        self.assertEqual(task_id, self.manager.active.task_id)

    def test_business_events_not_model_claims(self):
        self.confirm()
        self.finish()
        with self.assertRaises(ValueError):
            self.world.apply_business_event('registration_completed', 'llm-1', source='model')
        self.agent.respond('我已经挂好号了')
        self.assertFalse(self.world.patient.facts['registered'])

    def test_safety_cancel_independent_of_blocked_model(self):
        self.confirm()
        entered, release = threading.Event(), threading.Event()
        original = self.agent.model.plan
        def slow(*args):
            entered.set()
            release.wait(2)
            return original(*args)
        self.agent.model.plan = slow
        worker = threading.Thread(target=self.agent.respond, args=('开放时间是什么',))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.manager.cancel_navigation())
            self.assertEqual(self.manager.active.state, 'cancelled')
        finally:
            release.set()
            worker.join(3)

    def test_arrival_speech_failure_does_not_infer_business(self):
        self.confirm()
        self.robot.fail_speech = True
        self.finish()
        self.assertEqual(self.manager.active.reason, 'arrival_speech_failed')
        self.assertIn('registration', self.world.patient.visited)
        self.assertFalse(self.world.patient.facts['registered'])

    def test_packaged_demo(self):
        with redirect_stdout(io.StringIO()):
            result = run_demo()
        self.assertEqual(result['robot']['node'], 'registration')
        self.assertFalse(result['patient']['facts']['registered'])


class SpatialTests(unittest.TestCase):
    def setUp(self):
        self.world, _, _, _ = create_app()

    def test_wall_crossing_edge_rejected(self):
        data = deepcopy(self.world.map.data)
        data['edges'].append({'from': 'registration', 'to': 'payment', 'weight': 1})
        with self.assertRaises(ValueError):
            SpatialMap(data)

    def test_dijkstra_uses_weights_and_valid_edges(self):
        data = deepcopy(self.world.map.data)
        data['edges'].append({'from': 'entrance', 'to': 'registration', 'weight': 100})
        spatial = SpatialMap(data)
        path, cost = spatial.route('entrance', 'registration')
        self.assertEqual(path, ['entrance', 'service_desk', 'registration'])
        self.assertLess(cost, 100)

    def test_invalid_geometry_rejected(self):
        for patch in ({'floor_id': 3}, {'resolution': 0}, {'robot_radius': -1}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                SpatialMap({**self.world.map.data, **patch})
        with self.assertRaises(ValueError):
            Pose2D(float('nan'), 1, 0)
        with self.assertRaises(TypeError):
            Pose2D(x=0, y=0, yaw=0, z=1)

    def test_segment_cannot_cross_gap_in_walkable_union(self):
        spatial = self.world.map
        spatial.walkable = [[0, 0, 3, 10], [7, 0, 12, 10]]
        self.assertFalse(spatial.segment_clear(Pose2D(2, 2, 0), Pose2D(8, 2, 0)))


class HardwareBoundaryTests(unittest.TestCase):
    def test_synthetic_map_denied_even_when_conditions_claimed(self):
        world, _, manager, _ = create_app()
        all_yes = HardwareConditions(**{name: True for name in HardwareConditions.__dataclass_fields__})
        with self.assertRaises(ValueError):
            MotionGuard().check('navigate', simulated=False, spatial_map=world.map, conditions=all_yes)
        for adapter in (VbotAortaAdapter(), VbotRos2Adapter()):
            result = adapter.dry_run(manager.preview('registration'), world.map)
            self.assertFalse(result['would_execute'])
            self.assertFalse(adapter.execute('navigate', 'registration')['ok'])
            self.assertFalse(adapter.gesture('welcome_wave'))

    def test_unavailable_sdk_and_explicit_inspection_gate(self):
        adapter = VbotAortaAdapter()
        with self.assertRaises(PermissionError):
            adapter.inspect()
        with patch('shutil.which', return_value=None):
            self.assertEqual(adapter.inspect(authorized=True)['reason'], 'sdk_cli_unavailable')

    def test_pose_requires_real_verified_map(self):
        world, _, _, _ = create_app()
        with self.assertRaises(ValueError):
            verified_planar_pose(x=1, y=1, yaw=0, map_id=world.map.map_id,
                                 frame=world.map.frame, expected_map=world.map, verified=True)
