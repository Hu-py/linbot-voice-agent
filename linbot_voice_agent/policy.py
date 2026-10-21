"""Validation independent of prompts; model proposals never directly start motion."""
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

ACTIONS = {'lookup', 'navigate', 'pause', 'resume', 'cancel', 'status', 'help', 'comfort', 'clarify', 'medical', 'confirm', 'next_step'}


class PolicyError(ValueError):
    pass


def validate_plan(value, knowledge):
    if not isinstance(value, dict) or set(value) != {'action', 'destination', 'query'}:
        raise PolicyError('invalid plan schema')
    if not isinstance(value['action'], str) or value['action'] not in ACTIONS:
        raise PolicyError('unknown action')
    destination = value['destination']
    if destination is not None and (not isinstance(destination, str) or destination not in knowledge.places):
        raise PolicyError('unknown destination')
    if not isinstance(value['query'], str) or len(value['query']) > 500:
        raise PolicyError('invalid query')
    if value['action'] == 'navigate' and destination is None:
        raise PolicyError('navigation requires destination')
    if value['action'] not in {'navigate', 'lookup'} and destination is not None:
        raise PolicyError('unexpected destination')
    return dict(value)


@dataclass(frozen=True)
class PendingAction:
    id: str
    action: str
    destination: str | None
    expires_at: float


class ControlledTools:
    """Only a locally issued, unexpired confirmation can navigate or resume."""
    def __init__(self, robot, knowledge, clock=monotonic, ttl=60):
        self.robot, self.knowledge, self.clock, self.ttl = robot, knowledge, clock, ttl
        self.pending = None

    def invalidate(self):
        self.pending = None

    def propose(self, action, destination=None):
        if action not in {'navigate', 'resume'}:
            raise PolicyError('not a motion proposal')
        if action == 'navigate' and not self.knowledge.destination_available(destination):
            raise PolicyError('destination missing or expired')
        if action == 'resume' and destination is not None:
            raise PolicyError('resume does not accept destination')
        self.pending = PendingAction(uuid4().hex, action, destination, self.clock() + self.ttl)
        return self.pending

    def _execute(self, action, destination=None, confirmed=False):
        try:
            execute = self.robot.execute_confirmed if confirmed and hasattr(self.robot, 'execute_confirmed') else self.robot.execute
            event = execute(action, destination)
            if not isinstance(event, dict) or type(event.get('ok')) is not bool or type(event.get('simulated')) is not bool:
                raise ValueError('Invalid tool feedback')
            if event['simulated'] != self.robot.simulated:
                raise ValueError('Adapter feedback provenance mismatch')
            return dict(event)
        except Exception:
            return {'tool': action, 'ok': False, 'simulated': getattr(self.robot, 'simulated', False), 'error': 'adapter_failure'}

    def execute_read_or_stop(self, action):
        if action not in {'pause', 'cancel', 'status', 'request_help'}:
            raise PolicyError('action requires confirmation')
        if action != 'status': self.invalidate()
        return self._execute(action)

    def confirm(self, request_id):
        pending = self.pending
        self.invalidate()  # Consume even when expired or a caller passes a stale token.
        if not pending or pending.id != request_id or self.clock() >= pending.expires_at:
            raise PolicyError('confirmation expired or missing')
        if pending.action == 'navigate':
            if not self.knowledge.destination_available(pending.destination):
                raise PolicyError('destination expired')
            events = [self._execute('cancel')]
            if events[-1]['ok']: events.append(self._execute('navigate', pending.destination, confirmed=True))
            return events
        return [self._execute('resume', confirmed=True)]
