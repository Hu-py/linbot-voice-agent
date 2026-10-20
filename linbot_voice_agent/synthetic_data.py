"""Paraphrase fixed scenario seeds without changing hospital facts or expected labels."""
import json
from pathlib import Path
from .llm import ChatModel, ModelError
from .simulator import SyntheticEnvironment


def expand(model, cases, variants=3):
    if not 1 <= variants <= 5: raise ValueError('每个种子支持1至5条变体')
    generated = []
    for case in cases:
        result = model._json('''你是陪诊对话测试数据生成器。只改写用户的自然语言表达，保留给定种子的动作、否定、目的地、条件和歧义。
不生成医院事实、病情、治疗方案、患者真实身份、工具反馈或预期状态。禁止增加未给定的科室或导航授权。
只返回 JSON：{"utterances":["表达1","表达2"]}，数量严格等于 count。这是测试对话，不是知识库。''',
                             {'seed':case['utterance'],'constraint':case['constraint'],'count':variants})
        values = result.get('utterances')
        if set(result) != {'utterances'} or not isinstance(values,list) or len(values) != variants or not all(isinstance(s,str) and 1 <= len(s.strip()) <= 300 for s in values):
            raise ModelError('合成表达格式不正确')
        if len(set(values)) != len(values): raise ModelError('合成表达存在重复')
        for number, value in enumerate(values,1):
            generated.append({**case,'id':case['id']+f'_v{number}','seed_id':case['id'],
                              'utterance':value.strip(),'review_status':'needs_human_review'})
    return {'synthetic':True,'kind':'evaluation_only_not_knowledge','cases':generated}


def evaluate(model, cases, seed_dir):
    """Live semantic evaluation: reports mismatches; never moves a robot."""
    results = []
    for case in cases:
        env = SyntheticEnvironment(seed_dir,case['patient_id'])
        context = {'patient':env.patient.snapshot(), 'workflow_next':env.workflow.next_step(env.patient),
                   'pending':None,'destination':None,'robot_status':'idle',**case.get('context',{})}
        try:
            plan = model.plan(case['utterance'],context,[],env.knowledge)
            ok = plan['action'] == case['expected_action'] and plan['destination'] == case['expected_destination']
            results.append({'id':case['id'],'passed':ok,'actual':plan})
        except ModelError:
            results.append({'id':case['id'],'passed':False,'error':'model_error'})
    return {'kind':'live_model_semantic_evaluation','passed':sum(r['passed'] for r in results),
            'total':len(results),'results':results}



def expand_multiturn(model, scenarios):
    """Rewrite user slots only. Operator events, confirmations and outcomes stay fixed."""
    import copy
    output = copy.deepcopy(scenarios)
    for scenario in output:
        slots = [{'index':i, 'text':step['text'], 'constraint':step['constraint']}
                 for i,step in enumerate(scenario['steps']) if step['kind']=='user' and step.get('editable',True)]
        response=model._json('只改写多轮陪诊场景中的用户表达；保留原意、否定和歧义。不要添加医院事实或更改模拟事件。返回 JSON：{"utterances":[{"index":0,"text":"改写"}]}，逐一对应给定 slots 的 index，不增加或遗漏。',
                             {'slots':slots,'scenario':scenario})
        items=response.get('utterances')
        if set(response)!={'utterances'} or not isinstance(items,list) or len(items)!=len(slots):
            raise ModelError('多轮表达格式无效')
        expected={slot['index'] for slot in slots}
        seen=set()
        for item in items:
            if not isinstance(item,dict) or set(item)!={'index','text'}:
                raise ModelError('多轮表达字段无效')
            i=item['index']
            if type(i) is not int or i not in expected or i in seen or not isinstance(item['text'],str) or not 1<=len(item['text'].strip())<=300:
                raise ModelError('多轮表达索引或文本无效')
            seen.add(i)
            scenario['steps'][i]['text']=item['text'].strip()
        scenario['review_status']='needs_human_review'
    return {'synthetic':True,'kind':'evaluation_only_not_knowledge','scenarios':output}


def evaluate_multiturn(model, scenarios, seed_dir):
    from .natural_agent import NaturalAgent
    rows=[]
    for scenario in scenarios:
        agent=NaturalAgent(model,environment=SyntheticEnvironment(seed_dir,scenario['patient_id']))
        turns=[]
        for step in scenario['steps']:
            if step['kind']=='user':
                reply=agent.respond(step['text'])
                turns.append({'expected':step['expected_intent'],'actual':reply.intent,
                              'passed':reply.intent==step['expected_intent'] and not reply.degraded})
            elif step['kind']=='event':
                reply=agent.inject(step['name'])
                turns.append({'event':step['name'],'passed':reply.intent!='rejected_event'})
            elif step['kind']=='tick':
                for _ in range(min(step.get('count',1),20)):agent.advance()
            elif step['kind']=='tick_until_done':
                for _ in range(min(step.get('max_ticks',20),20)):
                    if agent.robot.status!='running':break
                    agent.advance()
            else:raise ValueError('未知场景步骤')
        expected=scenario['expected']
        patient=agent.environment.patient
        state_ok=(patient.current_location==expected['location'] and
                  all(patient.facts[k]==v for k,v in expected.get('facts',{}).items()) and
                  agent.robot.status==expected.get('status',agent.robot.status) and
                  patient.emergency==expected.get('emergency',patient.emergency))
        rows.append({'id':scenario['id'],'passed':state_ok and all(t['passed'] for t in turns),
                     'state_ok':state_ok,'turns':turns})
    return {'kind':'live_multiturn_evaluation','passed':sum(r['passed'] for r in rows),
            'total':len(rows),'results':rows}

def main():
    import argparse
    parser = argparse.ArgumentParser(description='扩写合成测试表达或运行真实模型语义评估；不会写入知识库')
    parser.add_argument('operation',choices=['generate','evaluate','generate-multiturn','evaluate-multiturn'])
    parser.add_argument('--input',default='scenarios/seed_dialogues.json')
    parser.add_argument('--seed-dir',default='seeds')
    parser.add_argument('--output',required=True)
    parser.add_argument('--variants',type=int,default=3)
    args=parser.parse_args()
    try:
        output=Path(args.output)
        if output.exists(): raise ValueError('输出文件已存在，请使用新文件名')
        seed_root=Path(args.seed_dir).resolve()
        if output.resolve().is_relative_to(seed_root): raise ValueError('测试对话不能写入事实种子目录')
        corpus=json.loads(Path(args.input).read_text())
        model=ChatModel()
        if args.operation=='generate': result=expand(model,corpus['cases'],args.variants)
        elif args.operation=='evaluate': result=evaluate(model,corpus['cases'],args.seed_dir)
        elif args.operation=='generate-multiturn': result=expand_multiturn(model,corpus['scenarios'])
        else: result=evaluate_multiturn(model,corpus['scenarios'],args.seed_dir)
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
        print(f'已保存 {output}')
    except (ValueError,ModelError,KeyError,OSError) as exc:
        parser.error(str(exc))

if __name__=='__main__': main()
