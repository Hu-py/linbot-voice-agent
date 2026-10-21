"""Deterministic planar executor with feedback, not an immediate-success stub."""
import math
from uuid import uuid4
from ..world.spatial_map import Pose2D


class MockEmbodiedRobot:
    simulated = True

    def __init__(self, spatial_map, pose, node):
        self.map, self.pose, self.node = spatial_map, pose, node
        self.task_id = None
        self.motion = 'idle'
        self.plan = None
        self.index = 1
        self.sequence = 0
        self.queue, self.speech, self.gestures = [], [], []
        self.fail_gesture = self.fail_speech = False
        self.confirm_cancel = True

    def speak(self, text):
        if self.fail_speech:
            return False
        self.speech.append(text)
        return True

    def gesture(self, preset):
        if preset != 'welcome_wave' or self.fail_gesture:
            return False
        self.gestures.append(preset)
        return True

    def get_robot_status(self):
        return {'pose': self.pose, 'node': self.node, 'motion': self.motion, 'task_id': self.task_id}

    def _emit(self, kind, **extra):
        self.sequence += 1
        event = {'event_id': uuid4().hex, 'task_id': self.task_id, 'sequence': self.sequence,
                 'kind': kind, 'pose': {'x': self.pose.x, 'y': self.pose.y, 'yaw': self.pose.yaw},
                 'node': self.node, 'simulated': True, **extra}
        self.queue.append(event)
        return event

    def navigate(self, plan, task_id):
        if self.motion in {'running', 'paused', 'unknown'}:
            return False
        if plan.map_id != self.map.map_id or self.pose.distance(plan.poses[0]) > .15:
            return False
        self.plan, self.task_id, self.index, self.sequence = plan, task_id, 1, 0
        self.motion = 'running'
        self._emit('started')
        return True

    def pause_navigation(self):
        if self.motion != 'running':
            return False
        self.motion = 'paused'
        self._emit('paused')
        return True

    def resume_navigation(self):
        if self.motion != 'paused':
            return False
        self.motion = 'running'
        self._emit('resumed')
        return True

    def cancel_navigation(self):
        if self.motion not in {'running', 'paused', 'unknown'}:
            return True
        self.motion = 'idle' if self.confirm_cancel else 'unknown'
        if self.confirm_cancel:
            self._emit('cancelled')
        return True

    def tick(self, dt=1., speed=1.):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (dt, speed)):
            raise ValueError('Invalid simulation step')
        if self.motion != 'running':
            return
        if self.index >= len(self.plan.nodes):
            self.pose = self.plan.poses[-1]
            self.motion = 'idle'
            self._emit('arrived')
            return
        a, b = self.plan.nodes[self.index-1:self.index+1]
        target = self.plan.poses[self.index]
        if not self.map.edge_open(a, b) or not self.map.segment_clear(self.pose, target):
            self.motion = 'idle'
            self._emit('failed', reason='obstacle')
            return
        distance = self.pose.distance(target)
        ratio = min(1., speed * dt / max(distance, 1e-12))
        yaw = math.atan2(target.y-self.pose.y, target.x-self.pose.x)
        self.pose = Pose2D(self.pose.x+(target.x-self.pose.x)*ratio,
                           self.pose.y+(target.y-self.pose.y)*ratio, yaw)
        if ratio == 1:
            self.node = b
            self.index += 1
        if self.index >= len(self.plan.nodes):
            self.pose, self.motion = target, 'idle'
            self._emit('arrived')
        else:
            self._emit('progress', next_node=self.plan.nodes[self.index])

    def drain_events(self):
        result, self.queue = self.queue, []
        return result
