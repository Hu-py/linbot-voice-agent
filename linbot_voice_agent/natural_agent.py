"""LLM-first companion with local evidence and an independent motion gate."""
import re
from collections import deque
from .agent import Reply
from .knowledge import Knowledge
from .llm import ModelError
from .policy import ControlledTools, PolicyError, validate_plan
from .robot import MockRobot

URGENT = ('晕倒', '救命', '呼吸困难', '急救', '有人倒下', '胸痛')
MEDICAL = ('吃什么药', '用药', '剂量', '诊断', '什么病', '治疗', '药怎么吃')
CONFIRMATIONS = {'好', '好的', '确认', '确认出发', '确认恢复', '是的', '可以', '开始吧', '走吧', '好，带我过去'}
LOCAL_ACTIONS = {'暂停': 'pause', '停一下': 'pause', '等一下': 'pause', '停下': 'pause', '停止': 'pause',
                 '取消': 'cancel', '取消导航': 'cancel', '不用了': 'cancel', '不去了': 'cancel',
                 '继续': 'resume', '继续导航': 'resume', '恢复': 'resume', '任务状态': 'status', '到了吗': 'status'}


def normalized(text):
    return text.strip().rstrip('。！？!?.')


class NaturalAgent:
    def __init__(self, model, knowledge=None, robot=None, clock=None, environment=None):
        self.model = model
        self.environment = environment
        if environment:
            knowledge, robot = environment.knowledge, environment.robot
        self.knowledge = knowledge or Knowledge()
        self.robot = robot or MockRobot()
        # Hardware adapters are intentionally unavailable in this release.
        if not isinstance(self.robot, MockRobot):
            raise ValueError('v0.2 只允许 MockRobot；真实硬件接入需单独实现异步安全层')
        self.tools = ControlledTools(self.robot, self.knowledge, **({'clock': clock} if clock else {}))
        self.history = deque(maxlen=12)
        self.destination = None

    def reset(self):
        event = self.tools.execute_read_or_stop('cancel')
        self.history.clear()
        self.destination = None
        if self.environment:
            self.environment.reset()
            self.robot = self.environment.robot
            self.tools = ControlledTools(self.robot, self.knowledge, clock=self.tools.clock)
        return event

    def _state(self):
        pending = self.tools.pending
        if pending and self.tools.clock() >= pending.expires_at:
            self.tools.invalidate()
            pending = None
        state = {'destination': self.destination,
                'pending': {'action': pending.action, 'destination': pending.destination} if pending else None,
                'robot_status': self.robot.status, 'simulated': True}
        if self.environment:
            state['patient'] = self.environment.patient.snapshot()
            state['workflow_next'] = self.environment.workflow.next_step(self.environment.patient)
        return state

    def respond(self, text):
        text = text.strip()
        if not text: return Reply('请告诉我您需要什么帮助。', 'clarify')
        if len(text) > 2000:
            self.tools.invalidate()
            return Reply('这段内容较长，请分成较短的问题。', 'clarify')
        preempted = []
        if self.robot.status == 'running' and text.startswith(('等一下，', '等一下,', '先停一下，', '先停一下,')):
            preempted = [self.tools.execute_read_or_stop('pause')]
        reply = self._respond(text)
        reply.events = preempted + reply.events
        self.history.append({'role': 'user', 'content': text})
        self.history.append({'role': 'assistant', 'content': reply.text})
        return reply

    def _respond(self, text):
        command = normalized(text)
        state = self._state()
        if any(w in text for w in URGENT): return self._action('help')
        if command in LOCAL_ACTIONS:
            return self._action(LOCAL_ACTIONS[command])
        if any(w in text for w in MEDICAL): return self._action('medical')
        if command in CONFIRMATIONS and self.tools.pending:
            return self._confirm()
        try:
            # Every ordinary utterance uses the LLM, including known place names.
            plan = validate_plan(self.model.plan(text, state, list(self.history), self.knowledge), self.knowledge)
        except (ModelError, PolicyError, TypeError, ValueError):
            self.tools.invalidate()
            return self._fallback(text)
        if plan['action'] == 'confirm':
            # Semantic confirmation alone cannot authorize physical/motion operations.
            if self.tools.pending:
                return Reply('为避免误操作，请回复“确认”或“取消”这项模拟任务。', 'confirm')
            return Reply('目前没有待确认的任务，请先告诉我您想去哪里。', 'clarify')
        self.tools.invalidate()  # Any new intent invalidates the old action token.
        destination = plan['destination']
        if destination: self.destination = destination
        action = plan['action']
        if action == 'lookup':
            evidence = self.knowledge.search(plan['query'] or text, destination)
            if not evidence:
                return Reply('现有医院知识中没有找到足够信息，请向现场服务台核实。', 'lookup',
                             events=[{'tool': 'search_hospital', 'source_ids': [], 'ok': True}])
            reply = self._answer(text, evidence)
            reply.events.insert(0, {'tool': 'search_hospital', 'source_ids': [d['id'] for d in evidence], 'ok': True})
            return reply
        if action == 'comfort': return self._answer(text, [], 'comfort')
        return self._action(action, destination)

    def _action(self, action, destination=None):
        if action == 'next_step':
            return self.next_step()
        if action in {'navigate', 'resume'}:
            if self.environment and self.environment.patient.emergency:
                self.tools.invalidate()
                return Reply('紧急状态尚未解除，不能启动普通陪诊任务。', 'blocked')

            if action == 'resume' and self.robot.status != 'paused':
                self.tools.invalidate()
                return Reply('当前没有可恢复的暂停任务。', 'clarify')
            try:
                proposal = self.tools.propose(action, destination)
            except PolicyError:
                self.tools.invalidate()
                return Reply('目的地没有有效资料，无法启动导航；请向服务台核实。', 'clarify')
            target = self.knowledge.places[destination]['name'] if destination else '当前任务'
            verb = '前往' if action == 'navigate' else '恢复'
            location = ('，位置为' + self.knowledge.places[destination]['location']) if destination else ''
            return Reply(f'{self.knowledge.prefix}准备{verb}{target}{location}。这是模拟任务；请在60秒内回复“确认”，或回复“取消”。',
                         'confirm', [{'tool': 'propose_' + action, 'destination': destination,
                                      'request_id': proposal.id, 'executed': False, 'simulated': True}])
        self.tools.invalidate()
        if action == 'help':
            environment_events = self.environment.inject('emergency') if self.environment else []
            events = [self.tools.execute_read_or_stop('pause'), self.tools.execute_read_or_stop('request_help')]
            return Reply('请立即联系现场医护人员。本程序仅尝试模拟暂停和求助，尚未通知任何真实人员。', 'help', environment_events + events)
        if action == 'medical':
            return Reply('这个问题需要医护人员结合您的实际情况判断。我可以帮助查询就诊流程或联系服务台的方式。', 'medical_boundary')
        if action in {'pause', 'cancel', 'status'}:
            event = self.tools.execute_read_or_stop(action)
            if not event['ok']:
                return Reply('操作未完成，或当前任务状态不支持此操作。', 'tool_error', [event])
            if action == 'status':
                names = {'idle': '空闲', 'running': '进行中', 'paused': '已暂停'}
                status = names.get(event.get('status'), '未知')
                message = f'模拟任务状态：{status}。尚无真实到达反馈。'
            else:
                message = '模拟任务已' + ('暂停。' if action == 'pause' else '取消。')
            return Reply(message, action, [event])
        return Reply('您想了解就诊流程、查询地点，还是需要我模拟带路？请再具体说一下。', 'clarify')

    def _confirm(self):
        pending = self.tools.pending
        try:
            events = self.tools.confirm(pending.id)
        except PolicyError:
            return Reply('确认已失效，请重新提出任务。', 'clarify')
        if not events or not events[-1]['ok']:
            return Reply('任务操作未完成，没有确认成功启动。', 'tool_error', events)
        target = self.knowledge.places[pending.destination]['name'] if pending.destination else '当前任务'
        return Reply(f'已{"启动前往" if pending.action == "navigate" else "恢复"}{target}的模拟任务，机器狗不会实际移动。', pending.action, events)

    def _answer(self, text, evidence, kind='lookup'):
        sources = {d['id']: d for d in evidence}
        try:
            result = self.model.answer(text, list(self.history), evidence, kind)
            if not isinstance(result, dict) or set(result) != {'text', 'source_ids'}:
                raise ModelError()
            answer, ids = result['text'], result['source_ids']
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 1200:
                raise ModelError()
            if not isinstance(ids, list) or not all(isinstance(i, str) and i in sources for i in ids):
                raise ModelError()
            if kind == 'lookup' and not ids: raise ModelError()
            if kind == 'comfort' and ids: raise ModelError()
            # Defense in depth, not a claim of full semantic/clinical verification.
            if re.search(r'已(?:经)?(?:通知|联系|呼叫|到达|启动|开始导航)|建议服用|推荐服用|每天.*(?:毫克|mg)|确诊为', answer):
                raise ModelError()
            citations = [sources[i] for i in dict.fromkeys(ids)]
            return Reply((self.knowledge.prefix if kind == 'lookup' else '') + answer.strip(), kind,
                         sources=[{'id': d['id'], 'title': d['title'], 'source': d['source'],
                                   'reviewed_at': d.get('reviewed_at')} for d in citations])
        except (ModelError, TypeError, ValueError):
            if kind == 'comfort':
                return Reply('我在这里陪着您。您想先了解就诊流程，还是找个地方休息？', kind, degraded=True)
            return Reply(self.knowledge.prefix + '暂时无法生成可靠回答，以下是检索到的资料，可能只回答了部分问题：\n' +
                         '\n'.join(d['text'] for d in evidence[:2]), kind, degraded=True,
                         sources=[{'id': d['id'], 'title': d['title'], 'source': d['source'],
                                   'reviewed_at': d.get('reviewed_at')} for d in evidence[:2]])

    def _fallback(self, text):
        # Fail closed for model errors: local information only, never infer a new motion.
        evidence = self.knowledge.search(text, limit=2)
        if evidence:
            return Reply(self.knowledge.prefix + '模型暂不可用，以下为本地检索结果，可能只回答了部分问题；未启动新任务：\n' +
                         '\n'.join(d['text'] for d in evidence), 'fallback', degraded=True,
                         sources=[{'id': d['id'], 'title': d['title'], 'source': d['source'],
                                   'reviewed_at': d.get('reviewed_at')} for d in evidence])
        return Reply('模型暂不可用，未启动新任务。您仍可以说“暂停”“取消”或“任务状态”。', 'fallback', degraded=True)


    def next_step(self):
        """Workflow proposals are deterministic; patient claims cannot skip steps."""
        if not self.environment:
            return Reply('当前未加载虚拟患者，请提供已核实的预约信息，或查询一般就诊流程。', 'clarify')
        patient = self.environment.patient
        step = self.environment.workflow.next_step(patient)
        if step['kind'] == 'navigate':
            reply = self._action('navigate', step['target'])
        elif step['kind'] == 'event' and patient.current_location != step['location']:
            reply = self._action('navigate', step['location'])
        else:
            self.tools.invalidate()
            reply = Reply('【虚拟就诊流程】' + step['instruction'], 'next_step')
        reply.events.insert(0, {'tool':'get_next_step', 'step':step, 'simulated':True})
        return reply

    def _event_reply(self, events):
        messages = []
        for event in events:
            kind = event['kind']
            if kind == 'completed':
                place = self.knowledge.places[event['destination']]['name']
                messages.append('【模拟完成事件】已到达' + place + '。')
                step = self.environment.workflow.next_step(self.environment.patient)
                if step['kind'] == 'event' and step['location'] == self.environment.patient.current_location:
                    messages.append(step['instruction'])
            elif kind == 'progress':
                messages.append('【模拟进度】当前位置：' + self.knowledge.places[event['position']]['name'] + '。')
            elif kind == 'failed':
                self.tools.invalidate()
                messages.append('【模拟故障】任务未完成：' + event['reason'] + '。请联系现场工作人员，或重新选择目的地。')
            elif kind in {'emergency','emergency_cleared'}:
                self.tools.invalidate()
                messages.append('【模拟事件】' + ('已进入紧急状态，普通陪诊停止。' if kind == 'emergency' else '操作员已解除虚拟紧急状态。'))
            elif kind == 'workflow_completed':
                messages.append('【模拟流程事件】已确认步骤：' + event['step_id'] + '。')
        text = ' '.join(messages) or '没有新的模拟进度。'
        if messages: self.history.append({'role':'assistant','content':text})
        return Reply(text, 'simulator_event', events)

    def advance(self):
        if not self.environment: return Reply('请先加载虚拟医院环境。', 'clarify')
        return self._event_reply(self.environment.tick())

    def inject(self, name):
        if not self.environment: return Reply('请先加载虚拟医院环境。', 'clarify')
        try:
            events = self.environment.inject(name)
        except ValueError as exc:
            return Reply('模拟事件被拒绝：' + str(exc), 'rejected_event')
        return self._event_reply(events)
