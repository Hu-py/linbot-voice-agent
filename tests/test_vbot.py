import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from linbot_voice_agent.cli import main
from linbot_voice_agent.vbot import SDK_PAIRING, readiness_report, validate_profile
from linbot_voice_agent.voice_input import FinalAsrFilter


class VbotReadinessTests(unittest.TestCase):
    def setUp(self):
        self.profile = dict(robot_type='foot_quadruped',
                            reported_version='v5.0.16-2026070701edu',
                            application_version=None)
        self.host = dict(system='Linux', architecture='aarch64', python='3.10.12',
                         python_supported=True, rclpy_discoverable=True,
                         tools={'ros2': '/opt/ros/humble/bin/ros2'}, packages={})

    def test_system_version_does_not_establish_application_compatibility(self):
        report = readiness_report(self.profile, self.host)
        self.assertFalse(report['hardware_ready'])
        self.assertFalse(report['device_contacted'])
        self.assertTrue(any('reported_version' in issue for issue in report['issues']))

    def test_installed_sdk_and_claimed_application_are_not_live_validation(self):
        self.profile['application_version'] = 'V1.6.0'
        self.host['packages'] = dict(SDK_PAIRING)
        report = readiness_report(self.profile, self.host)
        self.assertFalse(report['hardware_ready'])
        self.assertTrue(any('实时样本' in issue for issue in report['issues']))

    def test_profile_rejects_malformed_and_extra_configuration(self):
        for profile in ([], {}, {**self.profile, 'application_version': 1},
                        {**self.profile, 'robot_type': ''}, {**self.profile, 'api_key': 'secret'}):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_profile(profile)

    def test_cli_offline_check_never_constructs_model_or_agent(self):
        output = io.StringIO()
        with patch('sys.argv', ['linbot', '--vbot-check']), \
             patch('linbot_voice_agent.cli.ChatModel') as model, \
             patch('linbot_voice_agent.cli.Agent') as agent, \
             patch('linbot_voice_agent.vbot.inventory', return_value=self.host), \
             redirect_stdout(output):
            main()
        model.assert_not_called()
        agent.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['scope'], 'offline_inventory')

    def test_cli_rejects_ambiguous_modes(self):
        for options in (['--vbot-check', '--mode', 'llm'], ['--vbot-profile', 'x.json'],
                        ['--vbot-check', '--scenario-demo']):
            with patch('sys.argv', ['linbot', *options]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)


class FinalAsrFilterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.filter = FinalAsrFilter(providers=(1,), max_entries=2, ttl_seconds=10,
                                     clock=lambda: self.now)

    def test_rejects_unknown_wrong_or_boolean_provider(self):
        for provider in (0, 2, 3, None, 'BODY', True):
            self.assertIsNone(self.filter.accept(provider=provider, text='确认', event_id='one'))

    def test_requires_valid_text_and_identity(self):
        for text, event_id in (('', 'one'), ('  ', 'one'), ('a' * 2001, 'one'),
                               ('确认', ''), ('确认', None)):
            self.assertIsNone(self.filter.accept(provider=1, text=text, event_id=event_id))

    def test_redelivery_filtered_but_new_utterance_with_same_text_allowed(self):
        first = self.filter.accept(provider=1, text=' 确认 ', event_id='session1:one')
        self.assertEqual(first['text'], '确认')
        self.assertIsNone(self.filter.accept(provider=1, text='确认', event_id='session1:one'))
        self.assertIsNotNone(self.filter.accept(provider=1, text='确认', event_id='session1:two'))

    def test_bounded_window_and_expiry(self):
        for event_id in ('a', 'b', 'c'):
            self.filter.accept(provider=1, text='查询', event_id=event_id)
        self.assertEqual(len(self.filter._seen), 2)
        self.assertIsNone(self.filter.accept(provider=1, text='查询', event_id='c'))
        self.now = 10
        self.assertIsNotNone(self.filter.accept(provider=1, text='查询', event_id='c'))
        self.assertEqual(len(self.filter._seen), 1)

    def test_sources_do_not_collide(self):
        gate = FinalAsrFilter()
        self.assertIsNotNone(gate.accept(provider=1, text='查询', event_id='one'))
        self.assertIsNotNone(gate.accept(provider=2, text='查询', event_id='one'))

    def test_invalid_limits(self):
        for options in ({'providers': (0,)}, {'providers': (True,)}, {'max_entries': 0},
                        {'ttl_seconds': 0}, {'ttl_seconds': float('nan')}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FinalAsrFilter(**options)
