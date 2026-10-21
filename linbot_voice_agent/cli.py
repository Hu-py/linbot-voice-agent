import argparse
import json
from importlib.resources import files
from .agent import Agent
from .knowledge import Knowledge
from .llm import ChatModel
from .natural_agent import NaturalAgent
from .simulator import SyntheticEnvironment

DEMO = ['心内科在哪里','怎么过去','那药房呢','带我去药房','好','等一下','继续','到了吗','取消']


def main():
    parser=argparse.ArgumentParser(description='Linbot v0.2：自然语言陪诊 Agent 与虚拟医院')
    parser.add_argument('--mode',choices=('offline','llm'),default='offline')
    parser.add_argument('--hospital',help='独立医院 JSON（不与 --synthetic 同用）')
    parser.add_argument('--synthetic',action='store_true',help='加载虚拟医院、患者和流程')
    parser.add_argument('--seed-dir',default=str(files('linbot_voice_agent').joinpath('data/seeds')))
    parser.add_argument('--patient',default='patient_01')
    parser.add_argument('--scenario-demo',action='store_true',help='心内科事件闭环演示；离线时使用固定计划')
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--show-tools',action='store_true')
    parser.add_argument('--show-sources',action='store_true')
    parser.add_argument('--vbot-check',action='store_true',help='只读检查当前机器环境，不连接机器人或调用模型')
    parser.add_argument('--vbot-profile',help='VBOT 设备档案 JSON，仅与 --vbot-check 同用')
    args=parser.parse_args()
    if args.vbot_profile and not args.vbot_check:
        parser.error('--vbot-profile 需要 --vbot-check')
    if args.vbot_check:
        if args.mode != 'offline' or args.synthetic or args.scenario_demo or args.demo or args.hospital:
            parser.error('--vbot-check 不与对话、模型或演示模式同时使用')
        from pathlib import Path
        from .vbot import readiness_report
        profile_path = Path(args.vbot_profile) if args.vbot_profile else files('linbot_voice_agent').joinpath('data/vbot-profile.json')
        try:
            profile = json.loads(profile_path.read_text(encoding='utf-8'))
            print(json.dumps(readiness_report(profile),ensure_ascii=False,indent=2))
        except (OSError,ValueError) as exc:
            parser.error(str(exc))
        return
    if args.hospital and (args.synthetic or args.scenario_demo): parser.error('--hospital 不能与虚拟场景同时使用')
    if args.synthetic and args.mode!='llm' and not args.scenario_demo:
        parser.error('自由对话的虚拟医院需 --mode llm；无密钥请用 --scenario-demo')
    try:
        model=ChatModel() if args.mode=='llm' else None
        if args.scenario_demo:
            from .demo import run_scenario
            run_scenario(args.seed_dir,args.patient,model)
            return
        if args.synthetic:
            agent=NaturalAgent(model,environment=SyntheticEnvironment(args.seed_dir,args.patient))
        elif model:
            agent=NaturalAgent(model,Knowledge(args.hospital))
        else:
            agent=Agent(Knowledge(args.hospital))
    except (ValueError,KeyError,OSError,TypeError) as exc:
        parser.error(str(exc))
    print('聆灵 Linbot | '+('LLM 自然语言模式' if model else '离线规则模式')+' | '+agent.knowledge.data['name'])
    print('/quit 退出；/reset 清空会话（虚拟场景会重置患者）。对话不写入磁盘。')
    if model: print('模型模式会向配置的服务发送当前输入、最近6轮对话及相关状态和知识；请使用虚拟信息。')
    if args.synthetic: print('/tick 推进一步模拟导航；/event 事件名 注入事件；/patient 查看患者；/next 查询下一步。')
    def display(reply):
        print('聆灵：'+reply.text)
        if args.show_tools:
            print(json.dumps({'intent':reply.intent,'events':reply.events,'degraded':reply.degraded},ensure_ascii=False))
        if args.show_sources:
            for source in reply.sources: print('依据：'+source['title']+' | '+source['source']+' ['+source['id']+']')
    if args.demo:
        for text in DEMO:
            print('你：'+text)
            display(agent.respond(text))
        return
    while True:
        try: text=input('你：').strip()
        except (EOFError,KeyboardInterrupt):
            print('\n再见。')
            return
        if text=='/quit': return
        if text=='/reset':
            agent.reset()
            print('会话已清空，虚拟场景已重置。' if args.synthetic else '会话已清空。')
        elif args.synthetic and text=='/tick': display(agent.advance())
        elif args.synthetic and text=='/next': display(agent.next_step())
        elif args.synthetic and text=='/patient': print(json.dumps(agent.environment.patient.snapshot(),ensure_ascii=False,indent=2))
        elif args.synthetic and text.startswith('/event '): display(agent.inject(text[7:].strip()))
        elif text.startswith('/'): print('未知命令。')
        else: display(agent.respond(text))
