"""Deterministic workflow rules, separate from dialogue generation."""
from .patient_state import FACTS


class WorkflowEngine:
    def __init__(self, data, knowledge):
        self.workflows = {w['id']: w for w in data['workflows']}
        if len(self.workflows) != len(data['workflows']): raise ValueError('重复流程 ID')
        for workflow in self.workflows.values():
            ids = set()
            for step in workflow['steps']:
                if step['id'] in ids: raise ValueError('重复步骤 ID')
                ids.add(step['id'])
                if step['kind'] not in {'navigate','event'}: raise ValueError('未知步骤类型')
                if step['completion'] not in FACTS | {'visited'}: raise ValueError('未知完成状态')
                for target in (step.get('target'), step.get('location')):
                    if target is not None and target != '$appointment' and target not in knowledge.places:
                        raise ValueError('流程引用未知地点')
                if any(f not in FACTS for f in step.get('requires', [])): raise ValueError('未知前置条件')

    @staticmethod
    def resolve(value, patient):
        return patient.appointment_department if value == '$appointment' else value

    def next_step(self, patient):
        if patient.emergency:
            return {'kind':'blocked','instruction':'紧急状态下暂停普通陪诊，等待现场人员处理。'}
        for step in self.workflows[patient.workflow_id]['steps']:
            target = self.resolve(step.get('target'), patient)
            completed = target in patient.visited if step['completion'] == 'visited' else patient.facts[step['completion']]
            if completed: continue
            unmet = [key for key in step.get('requires', []) if not patient.facts[key]]
            if unmet:
                return {'kind':'blocked','instruction':'尚未满足流程前置条件：' + ', '.join(unmet)}
            result = dict(step)
            if target: result['target'] = target
            if step.get('location'): result['location'] = self.resolve(step['location'],patient)
            return result
        return {'kind':'complete','instruction':'当前虚拟就诊流程已完成。'}

    def apply_event(self, patient, name):
        step = self.next_step(patient)
        if step['kind'] != 'event' or step['event'] != name:
            raise ValueError('此事件不是当前可执行的流程步骤')
        if patient.current_location != step['location']:
            raise ValueError('尚未到达该步骤要求的地点')
        patient.facts[step['completion']] = True
        return step
