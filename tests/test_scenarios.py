import json
from pathlib import Path
import unittest
from linbot_voice_agent.natural_agent import NaturalAgent
from linbot_voice_agent.simulator import SyntheticEnvironment, FAILURES
from linbot_voice_agent.synthetic_data import expand
from linbot_voice_agent.llm import ModelError

ROOT=Path(__file__).resolve().parents[1]

class Planner:
    decision={'action':'next_step','destination':None,'query':''}
    def plan(self,*args):return dict(self.decision)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.env=SyntheticEnvironment(ROOT/'seeds')
        self.planner=Planner()
        self.a=NaturalAgent(self.planner,environment=self.env)
    def go(self,target=None):
        if target:self.planner.decision={'action':'navigate','destination':target,'query':''}
        else:self.planner.decision={'action':'next_step','destination':None,'query':''}
        self.a.respond('请带路')
        self.a.respond('好，带我过去')
    def arrive(self):
        for _ in range(20):
            if self.a.robot.status!='running':break
            reply=self.a.advance()
        self.assertEqual(self.a.robot.status,'completed')
        return reply
    def test_heart_closed_loop(self):
        reply=self.a.respond('我已经挂好号了，现在应该去哪儿？')
        self.assertIn('3楼A区',reply.text)
        start=self.a.respond('好，带我过去。')
        self.assertTrue(start.events[-1]['task_id'])
        self.assertEqual(self.env.patient.current_location,'entrance')
        reply=self.arrive()
        self.assertIn('报到',reply.text)
        self.assertEqual(self.env.patient.current_location,'cardiology')
        self.assertFalse(self.env.patient.facts['checked_in'])
        self.a.inject('checked_in')
        self.assertTrue(self.env.patient.facts['checked_in'])
    def test_full_visit_payment_pickup(self):
        self.go();self.arrive()
        self.a.inject('checked_in');self.a.inject('consultation_completed')
        self.go();self.arrive()
        self.assertEqual(self.env.patient.current_location,'payment')
        self.a.inject('payment_completed')
        self.go();self.arrive()
        self.a.inject('medication_collected')
        self.assertEqual(self.env.workflow.next_step(self.env.patient)['kind'],'complete')
    def test_illegal_transition_rejected(self):
        for event in ['checked_in','consultation_completed','payment_completed','medication_collected']:
            self.assertEqual(self.a.inject(event).intent,'rejected_event')
        self.assertFalse(self.env.patient.facts['paid'])
    def test_pause_freezes_time(self):
        self.go();self.a.respond('暂停')
        for _ in range(5):self.a.advance()
        self.assertEqual(self.env.patient.current_location,'entrance')
        self.a.respond('继续');self.a.respond('确认');self.arrive()
    def test_destination_switch_new_task_id(self):
        self.go()
        old=self.a.robot.task_id
        self.go('restroom')
        self.assertNotEqual(self.a.robot.task_id,old)
        self.arrive()
        self.assertEqual(self.env.patient.current_location,'restroom')
        self.assertNotIn('cardiology',self.env.patient.visited)
    def test_failures_do_not_arrive(self):
        for failure in FAILURES:
            with self.subTest(failure=failure):
                self.setUp();self.go()
                reply=self.a.inject(failure)
                self.assertIn('任务未完成',reply.text)
                self.assertEqual(self.a.robot.status,'failed')
                self.assertEqual(self.env.patient.current_location,'entrance')
                self.assertFalse(self.env.patient.facts['checked_in'])
    def test_emergency_blocks_new_navigation(self):
        self.go();self.a.inject('emergency')
        self.go('pharmacy')
        self.assertNotEqual(self.a.robot.status,'running')
        self.assertTrue(self.env.patient.emergency)
        self.a.inject('clear_emergency')
        self.go('pharmacy')
        self.assertEqual(self.a.robot.status,'running')
    def test_natural_emergency_enters_simulator_state(self):
        self.go();self.a.respond('有人晕倒了')
        self.assertTrue(self.env.patient.emergency)
        self.assertNotEqual(self.a.robot.status,'running')
    def test_stale_completion_ignored_after_cancel(self):
        self.go()
        for _ in range(3):self.env.robot.tick()
        self.a.respond('取消')
        self.env.consume()
        self.assertNotIn('cardiology',self.env.patient.visited)
    def test_duplicate_event_id_ignored(self):
        self.go();self.arrive()
        last=self.env.robot._event('completed')
        self.env.consume()
        self.env.robot.queue.append(last)
        self.assertFalse(self.env.consume())
    def test_registration_cannot_be_inferred_from_arrival(self):
        env=SyntheticEnvironment(ROOT/'seeds','patient_02')
        a=NaturalAgent(Planner(),environment=env)
        a.next_step();a.respond('确认')
        for _ in range(10):
            if a.robot.status!='running':break
            a.advance()
        self.assertEqual(env.patient.current_location,'registration')
        self.assertFalse(env.patient.facts['registered'])
        a.inject('registration_completed')
        self.assertTrue(env.patient.facts['registered'])
    def test_seed_profiles_and_graph(self):
        profiles=json.loads((ROOT/'seeds/patient_profiles.json').read_text())['profiles']
        self.assertEqual(len(profiles),12)
        self.assertEqual(len(self.env.knowledge.places),12)
        for profile in profiles:
            env=SyntheticEnvironment(ROOT/'seeds',profile['id'])
            self.assertIn(env.workflow.next_step(env.patient)['kind'],{'navigate','event'})
        for target in self.env.knowledge.places:
            self.assertTrue(self.env.robot.route('entrance',target))
    def test_packaged_seeds_match(self):
        for p in (ROOT/'seeds').glob('*.json'):
            self.assertEqual(p.read_text(),(ROOT/'linbot_voice_agent/data/seeds'/p.name).read_text())
    def test_reset_restarts_patient_and_environment(self):
        self.go();self.arrive();self.a.inject('checked_in');self.a.reset()
        self.assertEqual(self.env.patient.current_location,'entrance')
        self.assertFalse(self.env.patient.facts['checked_in'])
        self.assertIs(self.a.robot,self.env.robot)
    def test_workflow_event_requires_current_location(self):
        self.go();self.arrive();self.go('restroom');self.arrive()
        self.assertEqual(self.a.inject('checked_in').intent,'rejected_event')
        self.planner.decision={'action':'next_step','destination':None,'query':''}
        self.a.respond('下一步')
        self.assertEqual(self.a.tools.pending.destination,'cardiology')


class GenerationTests(unittest.TestCase):
    def test_generation_preserves_facts_and_labels(self):
        cases=json.loads((ROOT/'scenarios/seed_dialogues.json').read_text())['cases'][:1]
        class Model:
            def _json(self,*args):return {'utterances':['我挂好号了，接下来做什么？']}
        result=expand(Model(),cases,1)
        case=result['cases'][0]
        self.assertEqual(case['expected_action'],cases[0]['expected_action'])
        self.assertEqual(case['patient_id'],cases[0]['patient_id'])
        self.assertEqual(case['review_status'],'needs_human_review')
    def test_generator_cannot_write_facts(self):
        cases=json.loads((ROOT/'scenarios/seed_dialogues.json').read_text())['cases'][:1]
        class Model:
            def _json(self,*args):return {'utterances':['你好'],'places':[{'name':'造假'}]}
        with self.assertRaises(ModelError):expand(Model(),cases,1)

class MultiTurnGenerationTests(unittest.TestCase):
    def test_multiturn_only_rewrites_user_slots(self):
        from linbot_voice_agent.synthetic_data import expand_multiturn
        scenes=json.loads((ROOT/'scenarios/multi_turn.json').read_text())['scenarios'][:1]
        class Model:
            def _json(self,instruction,payload):
                return {'utterances':[{'index':slot['index'],'text':slot['text']+'请问'} for slot in payload['slots']]}
        result=expand_multiturn(Model(),scenes)['scenarios'][0]
        self.assertEqual(result['expected'],scenes[0]['expected'])
        self.assertEqual(result['steps'][1],scenes[0]['steps'][1])
        self.assertEqual(result['steps'][2],scenes[0]['steps'][2])
        self.assertNotEqual(result['steps'][0]['text'],scenes[0]['steps'][0]['text'])
    def test_all_profile_workflows_reach_completion(self):
        profiles=json.loads((ROOT/'seeds/patient_profiles.json').read_text())['profiles']
        for profile in profiles:
            with self.subTest(patient=profile['id']):
                env=SyntheticEnvironment(ROOT/'seeds',profile['id'])
                a=NaturalAgent(Planner(),environment=env)
                for _ in range(30):
                    step=env.workflow.next_step(env.patient)
                    if step['kind']=='complete':break
                    if step['kind']=='navigate':
                        a.next_step();a.respond('确认')
                        for __ in range(20):
                            if a.robot.status!='running':break
                            a.advance()
                    elif step['kind']=='event':
                        self.assertNotEqual(a.inject(step['event']).intent,'rejected_event')
                    else:self.fail(step)
                self.assertEqual(env.workflow.next_step(env.patient)['kind'],'complete')

class CompoundPauseTests(unittest.TestCase):
    def test_pause_precedes_new_motion_proposal(self):
        env=SyntheticEnvironment(ROOT/'seeds')
        model=Planner()
        a=NaturalAgent(model,environment=env)
        a.next_step();a.respond('确认')
        model.decision={'action':'navigate','destination':'restroom','query':''}
        reply=a.respond('等一下，我想先去厕所')
        self.assertEqual(a.robot.status,'paused')
        self.assertEqual(reply.events[0]['tool'],'pause')
        self.assertEqual(a.tools.pending.destination,'restroom')
