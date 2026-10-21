"""Floor-one embodied orchestration layered onto the existing NaturalAgent."""
import json
from .agent import Reply
from .natural_agent import NaturalAgent
from .world.hospital_world import HospitalWorld
from .adapters.mock_robot import MockEmbodiedRobot
from .orchestrator.task_manager import TaskManager


class DemoModel:
    """Fixed offline semantic fixture, NOT evidence of real LLM understanding."""
    def plan(self, text, state, history, knowledge):
        matches = knowledge.match(text)
        target = matches[0] if len(matches) == 1 else state.get('destination')
        action = 'navigate' if '带我' in text else 'lookup'
        if target is None:
            action = 'clarify'
        return {'action': action, 'destination': target if action in {'navigate', 'lookup'} else None,
                'query': text}

    def answer(self, text, history, evidence, kind='lookup'):
        return {'text': evidence[0]['text'] if evidence else '请告诉我目的地。',
                'source_ids': [evidence[0]['id']] if evidence else []}


class EmbodiedAgent(NaturalAgent):
    def __init__(self, model, world, manager):
        self.world, self.manager = world, manager
        super().__init__(model, world.knowledge, manager)

    def _action(self, action, destination=None):
        if action == 'navigate':
            place = self.knowledge.places.get(destination, {})
            if place.get('floor', 1) > 1:
                destination = 'elevator_lobby'
            try:
                self.manager.preview(destination)
            except ValueError as exc:
                self.tools.invalidate()
                return Reply(str(exc) + '。仍可查询该地点的医院知识。', 'blocked')
        reply = super()._action(action, destination)
        if action == 'navigate' and destination == 'elevator_lobby' and reply.intent == 'confirm':
            reply.text = '我可以陪你去电梯口。确认出发吗？'
        return reply

    def _answer(self, text, evidence, kind='lookup'):
        reply = super()._answer(text, evidence, kind)
        if kind == 'lookup' and self.destination:
            try:
                plan = self.manager.preview(self.destination)
                names = [self.knowledge.places[n]['name'] for n in plan.nodes]
                reply.text += '\n【二维模拟路线】' + ' → '.join(names) + f'；路网代价 {plan.cost:.2f}。尚未执行。'
                reply.events.append({'tool': 'plan_2d_route', 'nodes': list(plan.nodes),
                                     'map_id': plan.map_id, 'executed': False, 'simulated': True})
            except ValueError:
                if self.knowledge.places.get(self.destination, {}).get('floor', 1) > 1:
                    # Keep the location fact; do not recite cross-floor route instructions.
                    place = self.knowledge.places[self.destination]
                    reply.text = f'{place["name"]}在{place["location"]}。我可以陪你去电梯口。'
                else:
                    reply.text += '\n此目的地当前不可导航。'
        return reply


def create_app(model=None, map_path=None, clock=None, timeout=60):
    world = HospitalWorld(map_path=map_path)
    # Derive a first-floor knowledge view without changing the original seed files.
    for destination in world.map.goals:
        place = world.knowledge.places[destination]
        route = '本轮仅使用一楼二维路网，路线以实时规划结果为准'
        place['route'] = route
        world.knowledge.documents['place:' + destination]['text'] = place['name'] + '位于' + place['location'] + '。' + route + '。'
    robot = MockEmbodiedRobot(world.map, world.robot.pose, world.robot.node)
    manager = TaskManager(world, robot, timeout=timeout, **({'clock': clock} if clock else {}))
    agent = EmbodiedAgent(model or DemoModel(), world, manager)
    return world, robot, manager, agent


def run_demo(backend='mock', map_path=None, model=None):
    world, robot, manager, agent = create_app(model, map_path)
    if backend != 'mock':
        from .adapters.vbot_aorta import VbotAortaAdapter
        from .adapters.vbot_ros2 import VbotRos2Adapter
        adapter = VbotAortaAdapter() if backend == 'vbot-aorta' else VbotRos2Adapter()
        print(json.dumps(adapter.dry_run(manager.preview('registration'), world.map), ensure_ascii=False, indent=2))
        return world.snapshot()
    print('Linbot v0.3 | 一楼二维模拟 | ' + ('真实模型配置' if model else '固定 MockModel；未验证真实 LLM 理解') + ' | 无真实动作')
    for text in ('我想去挂号处，应该怎么走？', '能带我过去吗？', '确认出发。'):
        print('患者：' + text)
        reply = agent.respond(text)
        print('Agent：' + reply.text)
    if not manager.active or manager.active.state != 'running':
        raise RuntimeError('Demo did not start a confirmed navigation task')
    print('【模拟欢迎动作】' + ','.join(robot.gestures))
    for _ in range(100):
        manager.tick()
        pose = world.robot.pose
        print(f'【执行器反馈】{manager.active.state} Pose2D({pose.x:.2f}, {pose.y:.2f}, {pose.yaw:.2f})')
        if manager.active.state in {'succeeded', 'failed', 'cancelled', 'unknown'}:
            break
    if manager.active.state != 'succeeded':
        raise RuntimeError('Demo failed to reach destination')
    print('【模拟播报】' + robot.speech[-1])
    snapshot = world.snapshot()
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print('等待独立 registration_completed 事件；registered 仍为 false。')
    return snapshot
