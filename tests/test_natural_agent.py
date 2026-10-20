import io
import json
import os
import unittest
from unittest.mock import patch
from linbot_voice_agent.knowledge import Knowledge
from linbot_voice_agent.llm import ChatModel, ModelError
from linbot_voice_agent.natural_agent import NaturalAgent
from linbot_voice_agent.policy import ControlledTools, PolicyError, validate_plan
from linbot_voice_agent.robot import MockRobot


def plan(action='lookup',destination=None,query='报到'):
    return dict(action=action,destination=destination,query=query)


class FakeModel:
    def __init__(self, decision=None):
        self.decision=decision or plan()
        self.calls=[]
        self.response=None
    def plan(self,text,state,history,knowledge):
        self.calls.append((text,state,history))
        if isinstance(self.decision,Exception): raise self.decision
        return self.decision
    def answer(self,text,history,evidence,kind):
        if self.response is not None: return self.response
        return {'text':evidence[0]['text'] if evidence else '我在这里陪着您。',
                'source_ids':[evidence[0]['id']] if evidence else []}


class NaturalTests(unittest.TestCase):
    def setUp(self):
        self.model=FakeModel()
        self.a=NaturalAgent(self.model)
    def propose(self):
        self.model.decision=plan('navigate','pharmacy','')
        self.a.respond('请领我去拿药')
    def test_known_phrase_goes_to_llm(self):
        self.a.respond('心内科在哪里')
        self.assertEqual(len(self.model.calls),1)
    def test_history_passed_and_bounded(self):
        for _ in range(10): self.a.respond('挂号后怎样报到')
        self.assertEqual(len(self.a.history),12)
        self.assertTrue(self.model.calls[-1][2])
    def test_grounded_sources(self):
        reply=self.a.respond('怎么报到')
        self.assertTrue(reply.sources)
        self.assertEqual(reply.events[0]['tool'],'search_hospital')
    def test_fabricated_citation_falls_back(self):
        self.model.response={'text':'药房凌晨开放','source_ids':['invented']}
        reply=self.a.respond('怎么报到')
        self.assertTrue(reply.degraded)
        self.assertNotIn('凌晨',reply.text)
    def test_missing_citation_falls_back(self):
        self.model.response={'text':'自编内容','source_ids':[]}
        self.assertTrue(self.a.respond('怎么报到').degraded)
    def test_false_tool_claim_rejected(self):
        self.model.response={'text':'已通知医护','source_ids':['flow:general']}
        self.assertTrue(self.a.respond('怎么报到').degraded)
    def test_no_evidence_no_generation(self):
        self.model.decision=plan(query='zxqvvv')
        self.assertFalse(self.a.respond('未知').sources)
    def test_motion_requires_user_confirmation(self):
        self.propose()
        self.assertEqual(self.a.robot.status,'idle')
        self.a.respond('确认')
        self.assertEqual(self.a.robot.status,'running')
    def test_semantic_confirm_cannot_execute(self):
        self.propose()
        self.model.decision=plan('confirm',None,'')
        self.a.respond('大概可以吧')
        self.assertEqual(self.a.robot.status,'idle')
    def test_old_confirmation_invalidated(self):
        self.propose()
        self.model.decision=plan('clarify',None,'')
        self.a.respond('我只是问问，别走')
        self.a.respond('确认')
        self.assertEqual(self.a.robot.status,'idle')
    def test_model_error_fails_closed(self):
        self.propose()
        self.model.decision=ModelError()
        reply=self.a.respond('还是去心内科吧')
        self.assertTrue(reply.degraded)
        self.assertIsNone(self.a.tools.pending)
        self.assertEqual(self.a.robot.status,'idle')
    def test_unknown_or_extra_tool_rejected(self):
        for decision in [plan('shell'),plan('navigate','not_a_place'),{**plan(),'command':'move'}]:
            self.model.decision=decision
            self.assertTrue(self.a.respond('执行').degraded)
        self.assertFalse(self.a.robot.events)
    def test_pause_independent_of_model(self):
        self.propose();self.a.respond('确认')
        self.model.decision=ModelError()
        self.a.respond('暂停')
        self.assertEqual(self.a.robot.status,'paused')
    def test_resume_requires_confirmation(self):
        self.propose();self.a.respond('确认');self.a.respond('暂停')
        self.a.respond('继续')
        self.assertEqual(self.a.robot.status,'paused')
        self.a.respond('确认')
        self.assertEqual(self.a.robot.status,'running')
    def test_expiry(self):
        now=[0]
        a=NaturalAgent(self.model,clock=lambda:now[0])
        self.model.decision=plan('navigate','pharmacy','')
        a.respond('带路')
        now[0]=61
        self.model.decision=plan('confirm',None,'')
        a.respond('确认')
        self.assertEqual(a.robot.status,'idle')
    def test_token_replay(self):
        gate=ControlledTools(MockRobot(),Knowledge())
        token=gate.propose('navigate','pharmacy')
        gate.confirm(token.id)
        with self.assertRaises(PolicyError):gate.confirm(token.id)
    def test_model_cannot_directly_navigate(self):
        gate=ControlledTools(MockRobot(),Knowledge())
        with self.assertRaises(PolicyError):gate.execute_read_or_stop('navigate')
    def test_reset(self):
        self.propose();self.a.reset()
        self.assertFalse(self.a.history)
        self.assertIsNone(self.a.tools.pending)
    def test_expired_documents_excluded(self):
        kb=Knowledge()
        kb.documents['place:pharmacy']['expires_on']='2000-01-01'
        self.assertFalse(kb.destination_available('pharmacy'))
        self.assertNotIn('place:pharmacy',[d['id'] for d in kb.search('药房',destination='pharmacy')])


class TransportTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ,{'LINBOT_API_KEY':'fake','LINBOT_MODEL':'fake','LINBOT_BASE_URL':'https://example.invalid/v1'}):
            self.m=ChatModel()
    def response(self,obj):
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':json.dumps(obj)}}]}).encode())
    def test_planner_transport_and_history(self):
        with patch('linbot_voice_agent.llm.urlopen',return_value=self.response(plan('navigate','pharmacy',''))) as call:
            self.assertEqual(self.m.plan('带我去拿药',{},[{'role':'user','content':'上文'}],Knowledge())['action'],'navigate')
            request=call.call_args.args[0]
            payload=json.loads(request.data)
            self.assertIn('上文',payload['messages'][1]['content'])
    def test_bad_plan_rejected(self):
        with patch('linbot_voice_agent.llm.urlopen',return_value=self.response(plan('shell'))):
            with self.assertRaises(ModelError):self.m.plan('x',{},[],Knowledge())
    def test_oversized_response(self):
        with patch('linbot_voice_agent.llm.urlopen',return_value=io.BytesIO(b'x'*65537)):
            with self.assertRaises(ModelError):self.m._json('x',{})
