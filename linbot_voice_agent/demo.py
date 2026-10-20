"""Deterministic infrastructure demo, NOT evidence of an LLM's language accuracy."""
from .natural_agent import NaturalAgent
from .simulator import SyntheticEnvironment


class DemoPlanner:
    def plan(self, *args):
        return {'action':'next_step','destination':None,'query':''}


def run_scenario(seed_dir, patient_id, model=None):
    agent = NaturalAgent(model or DemoPlanner(), environment=SyntheticEnvironment(seed_dir,patient_id))
    print('真实 LLM 场景演示' if model else '固定计划场景演示：未调用 LLM；用于验证流程与模拟事件闭环。')
    def say(text):
        print('患者：'+text)
        reply=agent.respond(text)
        print('聆灵：'+reply.text)
        for e in reply.events:
            if e.get('task_id'): print('任务 ID：'+e['task_id'])
        return reply
    say('我已经挂好号了，现在应该去哪儿？')
    say('好，带我过去。')
    for _ in range(20):
        if agent.robot.status != 'running': break
        print('聆灵：'+agent.advance().text)
    print('患者状态：'+str(agent.environment.patient.snapshot()))
    return agent
