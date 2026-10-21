import threading
import time
import unittest
from types import SimpleNamespace

from linbot_voice_agent.voice_loop import PlaybackTracker, VoiceLoop


class VoiceLoopTests(unittest.TestCase):
    def make_loop(self, speaker=None):
        self.replies = []
        self.events = []
        class Agent:
            def respond(inner, text):
                self.replies.append(text)
                return SimpleNamespace(text='这是测试回答', intent='lookup')
        class Speaker:
            def speak(inner, text, request_id):
                pass
            def close(inner):
                pass
        return VoiceLoop(Agent(), speaker or Speaker(), emit=self.events.append)

    def submit(self, loop, identity='one', **overrides):
        args = dict(provider=1, text='心内科在哪里', event_id=identity, captured_ns=time.time_ns())
        args.update(overrides)
        return loop.submit(**args)

    def test_full_loop_and_duplicate(self):
        loop = self.make_loop()
        self.assertTrue(self.submit(loop))
        self.assertTrue(loop.wait_idle(1))
        self.assertEqual(self.events[-1]['event'], 'playback_completed')
        loop._listen_after = 0
        self.assertFalse(self.submit(loop))
        self.assertEqual(len(self.replies), 1)
        loop.close()

    def test_rejects_stale_unknown_and_missing_identity(self):
        loop = self.make_loop()
        self.assertFalse(self.submit(loop, captured_ns=0))
        self.assertFalse(self.submit(loop, provider=0))
        self.assertFalse(self.submit(loop, identity=''))
        self.assertEqual(self.replies, [])
        loop.close()

    def test_busy_rejects_echo_without_queueing(self):
        entered, release = threading.Event(), threading.Event()
        class Speaker:
            def speak(self, text, request_id):
                entered.set()
                release.wait(2)
            def close(self):
                release.set()
        loop = self.make_loop(Speaker())
        self.assertTrue(self.submit(loop))
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.submit(loop, identity='echo'))
        release.set()
        self.assertTrue(loop.wait_idle(1))
        self.assertEqual(len(self.replies), 1)
        loop.close()

    def test_playback_failure_latches_fault(self):
        class Speaker:
            def speak(self, text, request_id):
                raise TimeoutError()
            def close(self):
                pass
        loop = self.make_loop(Speaker())
        self.assertTrue(self.submit(loop))
        self.assertTrue(loop.wait_idle(1))
        self.assertTrue(loop.faulted)
        self.assertFalse(self.submit(loop, identity='two'))
        self.assertFalse(any(e['event'] == 'playback_completed' for e in self.events))
        loop.close()

    def test_close_during_dialogue_never_speaks(self):
        entered, release = threading.Event(), threading.Event()
        class Agent:
            def respond(self, text):
                entered.set()
                release.wait(2)
                return SimpleNamespace(text='answer', intent='lookup')
        class Speaker:
            calls = 0
            def speak(self, text, request_id):
                self.calls += 1
            def close(self):
                release.set()
        speaker = Speaker()
        loop = VoiceLoop(Agent(), speaker)
        self.assertTrue(self.submit(loop))
        self.assertTrue(entered.wait(1))
        loop.close()
        self.assertEqual(speaker.calls, 0)


class PlaybackTrackerTests(unittest.TestCase):
    def test_only_correlated_complete_finishes(self):
        tracker = PlaybackTracker()
        tracker.begin('answer')
        tracker.start('other', ('old',))
        tracker.finish(('old',), False)
        self.assertFalse(tracker.done.is_set())
        tracker.start('answer', ('current',))
        tracker.finish(('other',), False)
        self.assertFalse(tracker.done.is_set())
        tracker.finish(('current',), False)
        tracker.wait(.01)

    def test_interrupted_and_timeout_not_success(self):
        tracker = PlaybackTracker()
        tracker.begin('answer')
        with self.assertRaises(TimeoutError):
            tracker.wait(.001)
        tracker.start('answer', (1,))
        tracker.finish((1,), True)
        with self.assertRaises(RuntimeError):
            tracker.wait(.01)
