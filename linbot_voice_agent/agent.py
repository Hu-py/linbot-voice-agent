from dataclasses import dataclass, field
from .knowledge import Knowledge
from .robot import MockRobot
from .llm import ModelError

@dataclass
class Reply:
    text: str
    intent: str
    events: list = field(default_factory=list)
    degraded: bool = False
    sources: list = field(default_factory=list)

class Agent:
    def __init__(self, knowledge=None, robot=None, model=None):
        self.knowledge = knowledge or Knowledge()
        self.robot = robot or MockRobot()
        self.model = model
        self.destination = None
        self.pending = None
        self.registered = False

    def reset(self):
        self.robot.execute("cancel")
        self.destination = self.pending = None
        self.registered = False

    def _tool(self, action, destination=None):
        return self.robot.execute(action, destination)

    def respond(self, text):
        text = text.strip()
        if not text: return Reply("请告诉我您需要什么帮助。", "unknown")
        if len(text) > 2000: return Reply("这段内容较长，请分成较短的问题。", "unknown")
        # Deterministic priority rules. Conservative prototype, not a clinical classifier.
        if any(w in text for w in ("晕倒", "救命", "呼吸困难", "急救", "有人倒下", "胸痛")):
            self.pending = None
            events = [self._tool("pause"), self._tool("request_help")]
            return Reply("请立即联系现场医护人员。本演示仅模拟暂停和人工求助，尚未通知任何真实人员。", "help", events)
        if text in ("取消", "取消导航", "不用了", "不去了", "不要", "算了"):
            self.pending = None
            return Reply("已取消模拟任务及待确认请求。", "cancel", [self._tool("cancel")])
        if text in ("暂停", "停一下", "等一下", "停止", "停下"):
            self.pending = None
            event = self._tool("pause")
            return Reply("模拟任务已暂停。" if event["ok"] else "当前没有正在执行的任务。", "pause", [event])
        if text in ("继续", "继续导航", "恢复"):
            event = self._tool("resume")
            return Reply("模拟任务已恢复。" if event["ok"] else "当前没有可恢复的暂停任务。", "resume", [event])
        if any(w in text for w in ("任务状态", "到哪了", "到了吗", "进度")):
            event = self._tool("status")
            return Reply(f"模拟任务状态：{event['status']}；目的地：{event['destination'] or '无'}。尚无真实到达反馈。", "status", [event])
        if any(w in text for w in ("吃什么药", "用药", "剂量", "诊断", "什么病", "治疗", "药怎么吃")):
            self.pending = None
            return Reply("病情判断和用药问题请咨询医护人员。我可以帮助查询医院地点和就诊流程。", "medical_boundary")
        if any(w in text for w in ("不要去", "不想去", "别去", "不去", "不要带", "不需要", "不用带")):
            self.pending = None
            return Reply("好的，不启动新的导航。若需停止当前任务，请说取消或暂停。", "cancel_request")
        if text in ("好", "好的", "是", "确认", "可以", "好，带我过去", "带我过去", "现在出发"):
            if self.pending:
                target, self.pending = self.pending, None
                events = [self._tool("cancel")]
                if not events[-1]["ok"]:
                    return Reply("取消上一任务失败，未启动新任务。", "tool_error", events)
                events.append(self._tool("navigate", target))
                if not events[-1]["ok"]:
                    return Reply("模拟导航未能启动，请稍后重试。", "tool_error", events)
                return Reply("已启动模拟导航，目的地：" + self.knowledge.places[target]["name"] + "。机器狗不会实际移动。", "navigate", events)
            if text == "带我过去" and self.destination:
                self.pending = self.destination
                return Reply("是否确认启动前往" + self.knowledge.places[self.pending]["name"] + "的模拟导航？", "confirm")
            return Reply("目前没有待确认的导航请求，请先告诉我目的地。", "clarify")
        if any(w in text for w in ("已经挂号", "挂好号", "挂完号")):
            self.registered = True
            if self.pending:
                return Reply("好的，已记录您完成挂号。是否确认前往" + self.knowledge.places[self.pending]["name"] + "？", "confirm")
        matches = self.knowledge.match(text)
        # A different or ambiguous utterance must not leave a stale confirmation armed.
        self.pending = None
        paused_events = []
        if "等一下" in text or "暂停" in text:
            paused_events = [self._tool("pause")]
        if len(matches) > 1:
            return Reply("您提到了多个地点，请先选择一个目的地。", "clarify")
        target = matches[0] if matches else None
        intent = "unknown"
        if any(w in text for w in ("紧张", "害怕", "担心")):
            intent = "comfort"
        elif any(w in text for w in ("下一步", "流程", "先做什么", "挂完号", "已经挂号")):
            intent = "flow"
        elif any(w in text for w in ("带我", "我想去", "我要去", "导航", "先去")):
            intent = "navigate"
        elif any(w in text for w in ("怎么走", "怎么过去", "怎么去")):
            intent = "route"
        elif target:
            intent = "location"
        elif any(w in text for w in ("哪里", "几楼")):
            intent = "location"
        degraded = False
        if self.model and intent == "unknown":
            try:
                decision = self.model.classify(text, {"destination":self.destination,"registered":self.registered}, self.knowledge)
                intent, target = decision["intent"], decision["destination"]
            except ModelError:
                degraded = True
        # Resolve anaphora only for explicitly referential phrases, never arbitrary unknown places.
        if target is None and any(w in text for w in ("那里", "那边", "那个地方", "怎么过去", "带我过去")):
            target = self.destination
        if target: self.destination = target
        prefix = self.knowledge.prefix
        if intent in ("location", "route", "navigate"):
            if target not in self.knowledge.places:
                return Reply("我还不能确认这个地点。请提供具体目的地；未录入的地点请咨询服务台。", "clarify", degraded=degraded)
            place = self.knowledge.places[target]
            if intent == "navigate":
                self.pending = target
                return Reply(prefix + place["name"] + "位于" + place["location"] + "。是否确认启动前往这里的模拟导航？", "confirm", paused_events, degraded=degraded)
            answer = place["route"] if intent == "route" else place["name"] + "位于" + place["location"]
            return Reply(prefix + answer + "。", intent, degraded=degraded)
        if intent == "flow": return Reply(prefix + self.knowledge.data["flow"], intent, degraded=degraded)
        if intent == "comfort": return Reply("就诊前感到紧张是可以理解的。您想先了解就诊流程，还是找个休息区坐一会儿？", intent, degraded=degraded)
        return Reply(("模型暂不可用，已回退离线模式。" if degraded else "") + "我可以查询医院地点、说明就诊流程、模拟带路或陪您聊聊就诊时的感受。请具体说说您的需求。", "unknown", degraded=degraded)
