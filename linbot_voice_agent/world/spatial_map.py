"""Validated single-floor geometric graph. Edges never imply wall traversal."""
from dataclasses import dataclass
import heapq
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float

    def __post_init__(self):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (self.x, self.y, self.yaw)):
            raise ValueError('Pose2D requires finite x, y, yaw')

    def distance(self, other):
        return math.hypot(self.x - other.x, self.y - other.y)


def segment_interval(a, b, rect):
    """Closed segment/rectangle intersection as a parameter interval."""
    low, high = 0., 1.
    for origin, delta, minimum, maximum in ((a.x, b.x-a.x, rect[0], rect[2]),
                                            (a.y, b.y-a.y, rect[1], rect[3])):
        if abs(delta) < 1e-12:
            if not minimum <= origin <= maximum:
                return None
        else:
            left, right = sorted(((minimum-origin)/delta, (maximum-origin)/delta))
            low, high = max(low, left), min(high, right)
            if low > high:
                return None
    return low, high


class SpatialMap:
    def __init__(self, data):
        self.data = json.loads(json.dumps(data))
        data = self.data
        for field in ('map_id', 'coordinate_frame'):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise ValueError('Missing map identity/frame')
        if data.get('floor_id') != 1 or type(data.get('synthetic')) is not bool:
            raise ValueError('Only explicitly labelled floor 1 maps are supported')
        self.map_id, self.frame = data['map_id'], data['coordinate_frame']
        self.synthetic = data['synthetic']
        self.resolution = data['resolution']
        if type(self.resolution) not in (int, float) or not math.isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError('Invalid map resolution')
        self.origin = Pose2D(**data['origin'])
        self.clearance = data.get('robot_radius', .2)
        if type(self.clearance) not in (int, float) or not math.isfinite(self.clearance) or self.clearance < 0:
            raise ValueError('Invalid robot radius')
        self.walkable = self._rectangles(data['walkable_regions'])
        self.obstacles = self._rectangles(data['obstacle_regions'])
        if not self.walkable:
            raise ValueError('No walkable region')
        self.nodes = {name: Pose2D(**pose) for name, pose in data['nodes'].items()}
        self.goals = dict(data['navigation_goals'])
        if not self.nodes or any(node not in self.nodes for node in self.goals.values()):
            raise ValueError('Unknown navigation goal node')
        self.graph = {name: {} for name in self.nodes}
        self.blocked_edges = set()
        for node in self.nodes.values():
            if not self.segment_clear(node, node):
                raise ValueError('Node lies in non-traversable space')
        for edge in data['edges']:
            a, b, weight = edge['from'], edge['to'], edge['weight']
            if a not in self.nodes or b not in self.nodes or a == b:
                raise ValueError('Invalid graph edge')
            if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                raise ValueError('Invalid edge weight')
            if b in self.graph[a] or not self.segment_clear(self.nodes[a], self.nodes[b]):
                raise ValueError('Duplicate edge or edge crossing wall')
            self.graph[a][b] = self.graph[b][a] = weight

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding='utf-8')))

    @staticmethod
    def _rectangles(values):
        for r in values:
            if len(r) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in r):
                raise ValueError('Invalid rectangle')
            if r[0] >= r[2] or r[1] >= r[3]:
                raise ValueError('Degenerate rectangle')
        return [list(r) for r in values]

    def segment_clear(self, a, b):
        radius = self.clearance
        for x0, y0, x1, y1 in self.obstacles:
            if segment_interval(a, b, (x0-radius, y0-radius, x1+radius, y1+radius)) is not None:
                return False
        intervals = []
        for x0, y0, x1, y1 in self.walkable:
            if x0+radius > x1-radius or y0+radius > y1-radius:
                continue
            intersection = segment_interval(a, b, (x0+radius, y0+radius, x1-radius, y1-radius))
            if intersection:
                intervals.append(intersection)
        covered = 0.
        for start, end in sorted(intervals):
            if start > covered + 1e-10:
                return False
            covered = max(covered, end)
        return covered >= 1.

    def edge_open(self, a, b):
        return b in self.graph.get(a, {}) and frozenset((a, b)) not in self.blocked_edges and self.segment_clear(self.nodes[a], self.nodes[b])

    def route(self, start, destination):
        if start not in self.nodes or destination not in self.goals:
            raise ValueError('Unknown or unsupported floor/destination')
        goal = self.goals[destination]
        queue, best = [(0., start, [start])], {start: 0.}
        while queue:
            cost, node, path = heapq.heappop(queue)
            if cost != best[node]:
                continue
            if node == goal:
                return path, cost
            for neighbor, weight in sorted(self.graph[node].items()):
                candidate = cost + weight
                if self.edge_open(node, neighbor) and candidate < best.get(neighbor, math.inf):
                    best[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor, path + [neighbor]))
        raise ValueError('Destination unreachable')
