"""Explicit, deterministic event source. Advancing time is a simulator operation."""
import json
from collections import deque
from pathlib import Path
from uuid import uuid4
from .knowledge import Knowledge
from .patient_state import PatientState
from .robot import MockRobot
from .workflow import WorkflowEngine

FAILURES = {'navigation_failed','timeout','blocked','destination_closed','low_battery'}


class HospitalSimulator(MockRobot):
    def __init__(self, knowledge, start):
        super().__init__()
        self.knowledge = knowledge
        self.position = start
        self.task_id = None
        self.path = []
        self.closed = set()
        self.emergency = False
        self.graph = {p: set() for p in knowledge.places}
        if start not in self.graph: raise ValueError('未知起点')
        for a, b in knowledge.data['edges']:
            if a not in self.graph or b not in self.graph: raise ValueError('路网引用未知地点')
            self.graph[a].add(b)
            self.graph[b].add(a)
        self.queue = deque()

    def route(self, start, target):
        queue = deque([(start,[start])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            if node == target: return path
            for neighbor in sorted(self.graph[node]):
                if neighbor not in seen and neighbor not in self.closed:
                    seen.add(neighbor)
                    queue.append((neighbor,path+[neighbor]))
        return None

    def _event(self, kind, **extra):
        event = {'event_id': uuid4().hex, 'task_id':self.task_id,'kind':kind,
                 'status':self.status,'destination':self.destination,'position':self.position,
                 'simulated':True, **extra}
        self.queue.append(event)
        return event

    def execute(self, action, destination=None):
        ok = True
        if action == 'navigate':
            path = self.route(self.position, destination) if destination in self.graph and destination not in self.closed else None
            ok = bool(path) and not self.emergency and self.status != 'running' and self.status != 'paused'
            if ok:
                self.task_id, self.destination = uuid4().hex, destination
                self.path, self.status = path[1:], 'running'
                self._event('started')
        elif action == 'pause':
            ok = self.status == 'running'
            if ok:
                self.status = 'paused'
                self._event('paused')
        elif action == 'resume':
            ok = self.status == 'paused' and not self.emergency
            if ok:
                self.status = 'running'
                self._event('resumed')
        elif action == 'cancel':
            if self.status in {'running','paused'}:
                self.status = 'cancelled'
                self._event('cancelled')
            self.path = []
            self.task_id = self.destination = None
            self.status = 'idle'
        elif action == 'request_help':
            self._event('help_requested')
        elif action != 'status':
            raise ValueError('未知模拟工具')
        result = {'tool':action,'ok':bool(ok),'task_id':self.task_id,'status':self.status,
                  'destination':self.destination,'position':self.position,'simulated':True}
        self.events.append(result)
        return result

    def tick(self):
        if self.status != 'running': return None
        if self.destination in self.closed or (self.path and self.path[0] in self.closed):
            return self.inject('blocked')
        if self.path: self.position = self.path.pop(0)
        if not self.path:
            self.status = 'completed'
            return self._event('completed')
        return self._event('progress')

    def inject(self, kind):
        if kind in FAILURES:
            if self.status not in {'running','paused'}: raise ValueError('没有可注入故障的活动任务')
            if kind == 'destination_closed': self.closed.add(self.destination)
            self.status = 'failed'
            self.path = []
            return self._event('failed', reason=kind)
        if kind == 'emergency':
            self.emergency = True
            self.status = 'emergency'
            self.path = []
            return self._event('emergency')
        if kind == 'clear_emergency':
            if not self.emergency: raise ValueError('当前不处于紧急状态')
            self.emergency = False
            self.status = 'idle'
            self.task_id = self.destination = None
            return self._event('emergency_cleared')
        raise ValueError('未知异常事件')

    def drain_events(self):
        events = list(self.queue)
        self.queue.clear()
        return events


class SyntheticEnvironment:
    def __init__(self, seed_dir, patient_id='patient_01'):
        seed_dir = Path(seed_dir)
        self.knowledge = Knowledge(seed_dir/'hospital.json')
        if self.knowledge.data.get('synthetic') is not True: raise ValueError('需要虚拟医院种子')
        self.workflow = WorkflowEngine(json.loads((seed_dir/'workflows.json').read_text()), self.knowledge)
        profiles = json.loads((seed_dir/'patient_profiles.json').read_text())['profiles']
        if len({p['id'] for p in profiles}) != len(profiles): raise ValueError('重复患者 ID')
        self.profile = next((p for p in profiles if p['id'] == patient_id),None)
        if self.profile is None: raise ValueError('未知患者 ID')
        self.reset()

    def reset(self):
        self.patient = PatientState.from_dict(self.profile)
        if self.patient.workflow_id not in self.workflow.workflows: raise ValueError('未知就诊流程')
        if self.patient.appointment_department not in self.knowledge.places: raise ValueError('未知预约科室')
        self.robot = HospitalSimulator(self.knowledge,self.patient.current_location)
        self.seen_events = set()

    def consume(self):
        """Only drain simulator-owned events, reject stale task completions."""
        accepted = []
        for event in self.robot.drain_events():
            if event['event_id'] in self.seen_events: continue
            self.seen_events.add(event['event_id'])
            kind = event['kind']
            if kind in {'progress','completed'}:
                if event['task_id'] != self.robot.task_id: continue
                self.patient.current_location = event['position']
                if kind == 'completed': self.patient.visited.add(event['destination'])
            if kind == 'emergency': self.patient.emergency = True
            if kind == 'emergency_cleared': self.patient.emergency = False
            accepted.append(event)
        return accepted

    def tick(self):
        self.robot.tick()
        return self.consume()

    def inject(self, name):
        if name in FAILURES | {'emergency','clear_emergency'}:
            self.robot.inject(name)
            return self.consume()
        if self.robot.status in {'running','paused'}: raise ValueError('请先结束当前导航再确认就诊步骤')
        step = self.workflow.apply_event(self.patient,name)
        return [{'event_id':uuid4().hex,'kind':'workflow_completed','step_id':step['id'],
                 'simulated':True,'patient_id':self.patient.id}]
