"""Synthetic patient state; factual transitions come from simulator/workflow events."""
from dataclasses import dataclass, field

FACTS = {'registered', 'checked_in', 'consultation_completed', 'paid', 'medication_collected',
         'has_lab_order', 'lab_completed', 'assistance_arranged'}


@dataclass
class PatientState:
    id: str
    name: str
    workflow_id: str
    appointment_department: str
    current_location: str
    needs_accessibility: bool = False
    facts: dict = field(default_factory=dict)
    visited: set = field(default_factory=set)
    emergency: bool = False

    @classmethod
    def from_dict(cls, value):
        if value.get('synthetic') is not True: raise ValueError('只接受明确标记的虚拟患者档案')
        facts = value.get('facts', {})
        if any(key not in FACTS or type(v) is not bool for key, v in facts.items()):
            raise ValueError('无效患者状态字段')
        state = cls(**{key: value[key] for key in ('id','name','workflow_id','appointment_department','current_location')},
                    needs_accessibility=value.get('needs_accessibility', False), facts={key:facts.get(key,False) for key in FACTS})
        state.visited.add(state.current_location)
        return state

    def snapshot(self):
        return {'id': self.id, 'synthetic': True, 'workflow_id': self.workflow_id,
                'appointment_department': self.appointment_department, 'current_location': self.current_location,
                'needs_accessibility': self.needs_accessibility, 'facts': dict(self.facts),
                'emergency': self.emergency}
