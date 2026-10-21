"""One-at-a-time, half-duplex speech loop; no robot motion transport."""
import threading
import time
import uuid

from .voice_input import FinalAsrFilter


class VoiceLoop:
    def __init__(self, agent, speaker, providers=(1,), emit=None):
        self.agent = agent
        self.speaker = speaker
        self.filter = FinalAsrFilter(providers=providers)
        self.emit = emit or (lambda event: None)
        self._lock = threading.Lock()
        self._worker = None
        self._closed = False
        self._fault = False
        self._busy = False
        self._listen_after = 0

    def submit(self, *, provider, text, event_id, captured_ns):
        # Transport must supply the actual publication timestamp, never arrival time.
        now = time.time_ns()
        if type(captured_ns) is not int or not 0 <= now - captured_ns <= 10_000_000_000:
            self.emit({'event': 'input_rejected', 'reason': 'stale_or_invalid_timestamp'})
            return False
        with self._lock:
            if self._closed or self._fault or self._busy or captured_ns <= self._listen_after:
                return False
            accepted = self.filter.accept(provider=provider, text=text, event_id=event_id)
            if accepted is None:
                return False
            self._busy = True
            self._worker = threading.Thread(target=self._respond, args=(accepted,), daemon=True)
            self._worker.start()
            return True

    def _respond(self, transcript):
        request_id = 'linbot-' + uuid.uuid4().hex
        try:
            self.emit({'event': 'input_accepted', 'request_id': request_id,
                       'provider': transcript['provider']})
            reply = self.agent.respond(transcript['text'])
            with self._lock:
                if self._closed:
                    return
            # Playback success is based on correlated completion, not service acceptance.
            self.speaker.speak(reply.text, request_id)
            self.emit({'event': 'playback_completed', 'request_id': request_id,
                       'intent': reply.intent, 'motion_backend': 'mock'})
        except Exception:
            # A timeout may leave audio running. Fail closed until operator restart.
            with self._lock:
                self._fault = True
            self.emit({'event': 'voice_fault', 'request_id': request_id,
                       'reason': 'dialogue_or_playback_failed', 'restart_required': True})
        finally:
            with self._lock:
                self._listen_after = time.time_ns() + 800_000_000
                self._busy = False

    @property
    def faulted(self):
        with self._lock:
            return self._fault

    def wait_idle(self, timeout=35):
        worker = self._worker
        if worker:
            worker.join(timeout)
            return not worker.is_alive()
        return True

    def close(self):
        with self._lock:
            self._closed = True
        # The transport cancels waits; an in-flight model call is allowed to finish
        # but the closed check prevents a subsequent playback request.
        self.speaker.close()
        self.wait_idle(1)


class PlaybackTracker:
    """Associate exact utterance start with completion by session + audio identity.

    Requires a single active TTS owner. It intentionally times out if a device
    rewrites/splits the start text or does not publish the documented events.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.done = threading.Event()
        self.text = None
        self.identity = None
        self.error = None

    def begin(self, text):
        with self._lock:
            self.text, self.identity, self.error = text, None, None
            self.done.clear()

    def start(self, text, identity):
        with self._lock:
            if self.text == text and self.identity is None and not self.done.is_set():
                self.identity = identity

    def finish(self, identity, interrupted):
        with self._lock:
            if self.identity is not None and identity == self.identity:
                self.error = 'interrupted' if interrupted else None
                self.done.set()

    def abort(self):
        with self._lock:
            self.error = 'closed'
            self.done.set()

    def wait(self, timeout):
        if not self.done.wait(timeout):
            raise TimeoutError('No correlated TTS playback completion')
        if self.error:
            raise RuntimeError('Playback did not complete')
