"""Hospital facts, patient facts, robot state and task state remain separate."""
from dataclasses import asdict, dataclass
from importlib.resources import files
import json
from pathlib import Path
import threading

from ..knowledge import Knowledge
from ..patient_state import PatientState
from ..workflow import WorkflowEngine
from .event_store import EventStore
from .spatial_map import Pose2D, SpatialMap


@dataclass
class RobotState:
    pose: Pose2D
    node: str
    motion: str = 'idle'
    task_id: str | None = None


class HospitalWorld:
    def __init__(self, seed_dir=None, map_path=None, patient_id='patient_02'):
        root = files('linbot_voice_agent')
        seed_dir = Path(seed_dir) if seed_dir else root.joinpath('data/seeds')
        self.knowledge = Knowledge(seed_dir / 'hospital.json')
        self.workflow = WorkflowEngine(json.loads((seed_dir / 'workflows.json').read_text()), self.knowledge)
        profiles = json.loads((seed_dir / 'patient_profiles.json').read_text())['profiles']
        self.patient = PatientState.from_dict(next(p for p in profiles if p['id'] == patient_id))
        map_path = map_path or root.joinpath('maps/synthetic_hospital_1f.json')
        self.map = SpatialMap.load(map_path)
        for destination in self.map.goals:
            if self.knowledge.places.get(destination, {}).get('floor') != 1:
                raise ValueError('Map goal must be a known first-floor place')
        start = self.map.goals[self.patient.current_location]
        self.robot = RobotState(self.map.nodes[start], start)
        self.tasks = {}
        self.events = EventStore()
        self.lock = threading.RLock()

    def apply_business_event(self, name, event_id, *, source):
        """Operator ingress only; never exposed as an LLM tool."""
        if source != 'operator':
            raise ValueError('Business facts require independent operator evidence')
        with self.lock:
            if self.events.contains(event_id):
                return False
            if self.robot.motion in {'running', 'paused', 'unknown'}:
                raise ValueError('Robot is not settled')
            # Validate workflow before recording so invalid events do not poison retries.
            from copy import deepcopy
            patient = deepcopy(self.patient)
            step = self.workflow.apply_event(patient, name)
            if not self.events.append({'event_id': event_id, 'kind': name, 'patient_id': patient.id}, source):
                return False
            self.patient = patient
            return step

    def snapshot(self):
        with self.lock:
            return {'map_id': self.map.map_id, 'floor_id': 1, 'synthetic': self.map.synthetic,
                    'patient': {**self.patient.snapshot(), 'visited': sorted(self.patient.visited)},
                    'robot': asdict(self.robot), 'tasks': {k: asdict(v) for k, v in self.tasks.items()}}
