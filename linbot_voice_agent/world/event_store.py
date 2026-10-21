"""In-memory event journal, isolated from model outputs."""
from copy import deepcopy
import threading


class EventStore:
    def __init__(self):
        self._events, self._ids = [], set()
        self._lock = threading.Lock()

    def append(self, event, source):
        if source not in {'executor', 'operator', 'orchestrator'}:
            raise ValueError('Untrusted event source')
        if not isinstance(event.get('event_id'), str) or not event['event_id']:
            raise ValueError('Missing event identity')
        with self._lock:
            if event['event_id'] in self._ids:
                return False
            self._ids.add(event['event_id'])
            self._events.append({**deepcopy(event), 'source': source})
            return True

    def snapshot(self):
        with self._lock:
            return deepcopy(self._events)

    def contains(self, event_id):
        with self._lock:
            return event_id in self._ids
