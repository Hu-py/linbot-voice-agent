"""Confirmed task lifecycle driven only by executor feedback."""
from dataclasses import dataclass
from math import isfinite
from time import monotonic
from uuid import uuid4

from ..safety.motion_guard import MotionGuard
from ..skills.speak import speak
from ..skills.gesture import gesture
from ..skills.navigate import navigate
from ..world.spatial_map import Pose2D
from .planner import Planner

TASK_STATES = frozenset({'pending', 'accepted', 'running', 'succeeded', 'failed', 'cancelled', 'unknown'})


@dataclass
class Task:
    task_id: str
    destination: str
    state: str = 'pending'
    paused: bool = False
    progress: float = 0.
    reason: str | None = None
    deadline: float = 0.
    last_sequence: int = 0
    cancel_requested: bool = False


class TaskManager:
    def __init__(self, world, adapter, clock=monotonic, timeout=60):
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError('Timeout must be positive')
        self.world, self.adapter = world, adapter
        self.simulated = adapter.simulated
        self.clock, self.timeout = clock, timeout
        self.planner, self.guard = Planner(world), MotionGuard()
        self.active = None
        self.plan = None

    @property
    def status(self):
        return self.world.robot.motion

    def preview(self, destination):
        with self.world.lock:
            return self.planner.plan(destination)

    def navigate(self, destination_id, *, confirmed=False):
        with self.world.lock:
            if not confirmed:
                raise ValueError('导航需要明确确认')
            if self.status in {'running', 'paused', 'unknown'}:
                raise ValueError('执行资源仍被占用或取消未确认')
            # Preflight before gesture, then re-read pose and replan afterward.
            self.planner.plan(destination_id)
            task = Task(uuid4().hex, destination_id, deadline=self.clock()+self.timeout)
            self.world.tasks[task.task_id] = task
            self.active = task
            self.world.robot.task_id = task.task_id
            try:
                if not gesture(self.adapter, 'welcome_wave', self.guard, self.world.map):
                    raise ValueError('welcome_gesture_failed')
                observed = self.adapter.get_robot_status()
                self.world.robot.pose = observed['pose']
                self.world.robot.node = observed['node']
                self.plan = self.planner.plan(destination_id)
                if not navigate(self.adapter, self.plan, task.task_id, self.guard, self.world.map):
                    raise ValueError('navigation_rejected')
                task.state = 'accepted'
                self.world.robot.motion = 'running'
                self.poll()
            except (ValueError, RuntimeError):
                task.state, task.reason = 'failed', 'gesture_or_navigation_failed'
                self.world.robot.motion = 'idle'
            return task

    def get_robot_status(self):
        return self.world.snapshot()['robot']

    def pause_navigation(self):
        with self.world.lock:
            ok = self.adapter.pause_navigation()
            self.poll()
            return ok and self.status == 'paused'

    def resume_navigation(self, *, confirmed=False):
        with self.world.lock:
            if not confirmed:
                raise ValueError('恢复需要确认')
            ok = self.adapter.resume_navigation()
            self.poll()
            return ok and self.status == 'running'

    def cancel_navigation(self, reason=None):
        with self.world.lock:
            task = self.active
            if not task or task.state in {'succeeded', 'failed', 'cancelled'}:
                return True
            task.cancel_requested = True
            task.reason = reason
            task.state = 'unknown'
            self.world.robot.motion = 'unknown'
            self.adapter.cancel_navigation()
            self.poll(check_timeout=False)
            return task.state in {'cancelled', 'failed'}

    def process_event(self, event):
        """Trusted adapter ingress; never register this method as a model tool."""
        with self.world.lock:
            task = self.active
            if not task or event.get('task_id') != task.task_id or task.state in {'succeeded', 'failed', 'cancelled'}:
                return False
            sequence = event.get('sequence')
            if type(sequence) is not int or sequence <= task.last_sequence or event.get('simulated') is not self.simulated:
                return False
            kind = event.get('kind')
            if kind not in {'started', 'progress', 'arrived', 'paused', 'resumed', 'cancelled', 'failed'}:
                return False
            if task.cancel_requested and kind != 'cancelled':
                return False
            try:
                pose = Pose2D(**event['pose'])
                node = event['node']
                if node not in self.plan.nodes or not self.world.map.segment_clear(self.world.robot.pose, pose):
                    return False
                if kind == 'arrived' and (node != self.plan.nodes[-1] or pose.distance(self.plan.poses[-1]) > .15):
                    return False
                if not self.world.events.append(event, 'executor'):
                    return False
            except (ValueError, TypeError, KeyError):
                return False
            task.last_sequence = sequence
            self.world.robot.pose, self.world.robot.node = pose, node
            if kind in {'started', 'progress', 'resumed'}:
                task.state, task.paused = 'running', False
                self.world.robot.motion = 'running'
                task.progress = self.plan.nodes.index(node) / max(1, len(self.plan.nodes)-1)
            elif kind == 'paused':
                task.paused = True
                self.world.robot.motion = 'paused'
            elif kind == 'cancelled':
                task.state = 'failed' if task.reason == 'timeout' else 'cancelled'
                self.world.robot.motion = 'idle'
                task.paused = False
            elif kind == 'failed':
                task.state, task.reason = 'failed', event.get('reason', 'executor_failure')
                self.world.robot.motion = 'idle'
                task.paused = False
            elif kind == 'arrived':
                self.world.robot.motion = 'idle'
                self.world.patient.current_location = task.destination
                self.world.patient.visited.add(task.destination)
                name = self.world.knowledge.places[task.destination]['name']
                message = '到电梯口啦，我陪着你。' if task.destination == 'elevator_lobby' else f'【二维模拟】已到达{name}。请独立办理业务；到达不代表挂号或缴费完成。'
                if speak(self.adapter, message):
                    task.state, task.progress = 'succeeded', 1.
                else:
                    task.state, task.reason = 'failed', 'arrival_speech_failed'
            return True

    def poll(self, check_timeout=True):
        with self.world.lock:
            events = self.adapter.drain_events()
            for event in events:
                self.process_event(event)
            task = self.active
            if check_timeout and task and task.state in {'accepted', 'running'} and self.clock() >= task.deadline:
                self.cancel_navigation(reason='timeout')
            return events

    def tick(self, dt=1.):
        with self.world.lock:
            self.poll()
            self.adapter.tick(dt)
            return self.poll()

    def _result(self, action, ok):
        return {'tool': action, 'ok': bool(ok), 'simulated': self.simulated, 'status': self.status,
                'task_id': self.active.task_id if self.active else None,
                'task_state': self.active.state if self.active else None,
                'destination': self.active.destination if self.active else None}

    def execute_confirmed(self, action, destination=None):
        """Called only by ControlledTools after consuming its confirmation token."""
        if action == 'navigate':
            try:
                task = self.navigate(destination, confirmed=True)
                return self._result(action, task.state in {'accepted', 'running', 'succeeded'})
            except ValueError:
                return self._result(action, False)
        if action == 'resume':
            return self._result(action, self.resume_navigation(confirmed=True))
        return self.execute(action, destination)

    def execute(self, action, destination=None):
        if action == 'status':
            self.poll()
            return self._result(action, True)
        if action == 'cancel':
            return self._result(action, self.cancel_navigation())
        if action == 'pause':
            return self._result(action, self.pause_navigation())
        return self._result(action, False)
