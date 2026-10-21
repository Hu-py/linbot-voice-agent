"""Transport-independent filtering for final /speech/asr_result transcripts.

This does not subscribe to VBOT, call an LLM, or authorize motion. A future
transport adapter must provide a stable event identity, scoped to its session.
Do not synthesize a new identity for each redelivery or use text as identity.
"""
from collections import OrderedDict
import math
import threading
import time


class FinalAsrFilter:
    """Bounded, thread-safe deduplication; rejects unspecified input sources.

    BODY=1 and TAG=2 follow vbot-lab's AsrResult provider contract. This class
    accepts only final-text messages, not tagged /voice/event payloads. Use one
    input stream; cross-topic correlation requires a separate verified adapter.
    """

    def __init__(self, providers=(1, 2), max_entries=256, ttl_seconds=30, clock=time.monotonic):
        providers = tuple(providers)
        if not providers or any(type(p) is not int or p not in (1, 2) for p in providers):
            raise ValueError('providers must contain BODY=1 and/or TAG=2')
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError('max_entries must be positive')
        if isinstance(ttl_seconds, bool) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError('ttl_seconds must be finite and positive')
        self.providers = frozenset(providers)
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._seen = OrderedDict()
        self._lock = threading.Lock()

    def accept(self, *, provider, text, event_id):
        """Return normalized transcript metadata, or None for rejected input.

        The cache is a delivery filter within a bounded window, not durable
        replay protection. Reconnects must discard obsolete messages upstream.
        """
        if type(provider) is not int or provider not in self.providers:
            return None
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
            return None
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            return None
        key = (provider, event_id)
        with self._lock:
            now = self.clock()
            if not math.isfinite(now):
                raise ValueError('clock must be finite')
            while self._seen and next(iter(self._seen.values())) <= now:
                self._seen.popitem(last=False)
            if key in self._seen:
                return None
            self._seen[key] = now + self.ttl_seconds
            while len(self._seen) > self.max_entries:
                self._seen.popitem(last=False)
        return {'provider': provider, 'text': text.strip(), 'event_id': event_id}
