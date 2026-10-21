from typing import Protocol, runtime_checkable

@runtime_checkable
class RobotPort(Protocol):
    status: str
    simulated: bool
    def execute(self, action: str, destination: str | None = None) -> dict: ...

class MockRobot:
    """Only simulated lifecycle events. Does not connect to ROS or hardware."""
    simulated = True
    def __init__(self):
        self.status = "idle"
        self.destination = None
        self.events = []

    def execute(self, action, destination=None):
        allowed = {"navigate", "pause", "resume", "cancel", "status", "request_help"}
        if action not in allowed:
            raise ValueError("Unsupported tool")
        ok = True
        if action == "navigate":
            ok = bool(destination) and self.status == "idle"
            if ok:
                self.status, self.destination = "running", destination
        elif action == "pause":
            ok = self.status == "running"
            if ok: self.status = "paused"
        elif action == "resume":
            ok = self.status == "paused"
            if ok: self.status = "running"
        elif action == "cancel":
            self.status, self.destination = "idle", None
        event = {"tool": action, "destination": self.destination, "status": self.status, "ok": ok, "simulated": True}
        self.events.append(event)
        return event
