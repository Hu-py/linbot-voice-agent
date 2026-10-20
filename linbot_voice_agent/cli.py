import argparse
import json
from .agent import Agent
from .knowledge import Knowledge
from .llm import ChatModel

DEMO = ["心内科在哪里", "怎么过去", "那药房呢", "带我去药房", "好", "等一下", "继续", "到了吗", "取消"]

def main():
    parser = argparse.ArgumentParser(description="Linbot 第一阶段：文本 Agent + 模拟机器人")
    parser.add_argument("--mode", choices=("offline", "llm"), default="offline")
    parser.add_argument("--hospital", help="自定义医院 JSON 路径")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--show-tools", action="store_true")
    args = parser.parse_args()
    try:
        agent = Agent(Knowledge(args.hospital), model=ChatModel() if args.mode == "llm" else None)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        parser.error(str(exc))
    print("聆灵 Linbot | 文本演示 | 模拟机器人 | " + agent.knowledge.data["name"])
    print("/quit 退出；/reset 清空当前会话并取消模拟任务。对话不写入磁盘。")
    if args.mode == "llm": print("LLM 模式会将未被本地规则识别的输入及最小会话状态发送到所配置的模型服务。请使用模拟数据。")
    def turn(text):
        reply = agent.respond(text)
        print("聆灵：" + reply.text)
        if args.show_tools:
            print(json.dumps({"intent":reply.intent,"events":reply.events,"degraded":reply.degraded},ensure_ascii=False))
    if args.demo:
        for text in DEMO:
            print("你：" + text)
            turn(text)
        return
    while True:
        try: text = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if text == "/quit": return
        if text == "/reset":
            agent.reset()
            print("会话已清空。")
        else: turn(text)
