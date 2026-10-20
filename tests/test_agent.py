import io
import json
import os
import unittest
from unittest.mock import patch
from linbot_voice_agent.agent import Agent
from linbot_voice_agent.llm import ChatModel, ModelError
from linbot_voice_agent.robot import MockRobot

class AgentTests(unittest.TestCase):
    def setUp(self): self.a = Agent()
    def start(self):
        self.a.respond('带我去药房')
        self.a.respond('好')
    def test_context(self):
        self.assertIn('三楼', self.a.respond('心内科在哪里').text)
        self.assertIn('三楼', self.a.respond('怎么过去').text)
        self.assertIn('一楼 B', self.a.respond('那药房呢').text)
    def test_confirmation(self):
        self.assertEqual(self.a.respond('带我去药房').intent, 'confirm')
        self.assertEqual(self.a.robot.status, 'idle')
        self.a.respond('好')
        self.assertEqual(self.a.robot.destination, 'pharmacy')
        self.assertTrue(all(e['simulated'] for e in self.a.robot.events))
    def test_anaphoric_request(self):
        self.a.respond('心内科在哪里')
        self.assertEqual(self.a.respond('带我过去').intent, 'confirm')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_pause_resume_cancel(self):
        self.start()
        self.a.respond('等一下')
        self.assertEqual(self.a.robot.status, 'paused')
        self.a.respond('继续')
        self.assertEqual(self.a.robot.status, 'running')
        self.a.respond('取消')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_unknown_does_not_reuse_target(self):
        self.a.respond('心内科在哪里')
        self.assertEqual(self.a.respond('带我去火星科').intent, 'clarify')
        self.a.respond('好')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_multiple_destinations(self):
        self.assertEqual(self.a.respond('带我去药房然后去心内科').intent, 'clarify')
        self.assertIsNone(self.a.pending)
    def test_negative_clears_confirmation(self):
        self.a.respond('带我去药房')
        self.a.respond('不要带我去心内科')
        self.a.respond('好')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_unrelated_input_disarms_confirmation(self):
        self.a.respond('带我去药房')
        self.a.respond('今天天气不错')
        self.a.respond('好')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_emergency_pauses_and_is_honest(self):
        self.start()
        reply = self.a.respond('有人晕倒了')
        self.assertEqual(self.a.robot.status, 'paused')
        self.assertIn('尚未通知', reply.text)
        self.assertEqual(reply.events[-1]['tool'], 'request_help')
    def test_medical_boundary(self):
        self.assertEqual(self.a.respond('我应该吃什么药').intent, 'medical_boundary')
        self.assertFalse(self.a.robot.events)
    def test_reset(self):
        self.start()
        self.a.reset()
        self.assertIsNone(self.a.destination)
        self.assertEqual(self.a.robot.status, 'idle')
    def test_registration_followup(self):
        self.a.respond('我想去心内科')
        self.assertEqual(self.a.respond('已经挂号了').intent, 'confirm')
        self.assertTrue(self.a.registered)
    def test_switch_requires_new_confirmation(self):
        self.start()
        reply = self.a.respond('等一下，我想先去厕所')
        self.assertEqual(self.a.robot.status, 'paused')
        self.assertEqual(reply.intent, 'confirm')
        self.assertEqual(self.a.robot.destination, 'pharmacy')
        self.a.respond('好')
        self.assertEqual(self.a.robot.destination, 'restroom')
    def test_provider_failure_fallback(self):
        class Broken:
            def classify(self, *args): raise ModelError()
        self.a.model = Broken()
        reply = self.a.respond('我想询问一些事情')
        self.assertTrue(reply.degraded)
        self.assertFalse(self.a.robot.events)
    def test_llm_navigation_also_needs_confirmation(self):
        class Model:
            def classify(self, *args): return {'intent':'navigate','destination':'pharmacy'}
        self.a.model = Model()
        self.assertEqual(self.a.respond('请领路取处方').intent, 'confirm')
        self.assertEqual(self.a.robot.status, 'idle')
    def test_no_false_arrival(self):
        self.start()
        self.assertIn('尚无真实到达反馈', self.a.respond('到了吗').text)
    def test_rejected_robot_action_not_reported_as_success(self):
        class Failed(MockRobot):
            def execute(self, action, destination=None):
                if action == 'navigate': return {'ok':False, 'simulated':True}
                return super().execute(action,destination)
        self.a.robot = Failed()
        self.a.respond('带我去药房')
        self.assertEqual(self.a.respond('好').intent, 'tool_error')

class ModelTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {'LINBOT_API_KEY':'test-key','LINBOT_BASE_URL':'https://example.invalid/v1','LINBOT_MODEL':'test'}):
            self.model = ChatModel()
    def response(self, content):
        return io.BytesIO(json.dumps({'choices':[{'message':{'content':content}}]}).encode())
    def test_valid_json(self):
        with patch('linbot_voice_agent.llm.urlopen', return_value=self.response('{"intent":"navigate","destination":"pharmacy"}')):
            self.assertEqual(self.model.classify('测试',{},Agent().knowledge)['destination'], 'pharmacy')
    def test_invalid_model_payloads(self):
        for value in ['not json', '[]', '{"intent":"shell","destination":null}', '{"intent":"navigate","destination":"unknown"}', '{"intent":"navigate","destination":"pharmacy","command":"move"}']:
            with self.subTest(value=value), patch('linbot_voice_agent.llm.urlopen', return_value=self.response(value)):
                with self.assertRaises(ModelError): self.model.classify('测试',{},Agent().knowledge)
    def test_timeout(self):
        with patch('linbot_voice_agent.llm.urlopen', side_effect=TimeoutError):
            with self.assertRaises(ModelError): self.model.classify('测试',{},Agent().knowledge)

if __name__ == '__main__': unittest.main()
