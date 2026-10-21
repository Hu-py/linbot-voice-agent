"""Map-bound plans resolve known destination IDs; never accept model coordinates."""
from dataclasses import dataclass
from ..world.spatial_map import Pose2D


@dataclass(frozen=True)
class NavigationPlan:
    destination: str
    map_id: str
    coordinate_frame: str
    nodes: tuple[str, ...]
    poses: tuple[Pose2D, ...]
    cost: float


class Planner:
    def __init__(self, world):
        self.world = world

    def plan(self, destination):
        world = self.world
        if not isinstance(destination, str) or destination not in world.map.goals:
            raise ValueError('本轮仅支持一楼已配置地点，禁止跨楼层导航')
        if not world.knowledge.destination_available(destination):
            raise ValueError('目的地知识缺失或已过期')
        if world.knowledge.places[destination]['floor'] != 1:
            raise ValueError('本轮仅支持一楼')
        if world.robot.pose.distance(world.map.nodes[world.robot.node]) > .15:
            raise ValueError('机器人停在路段中，需要核验位置后重新规划；不自动吸附到路网节点')
        nodes, cost = world.map.route(world.robot.node, destination)
        return NavigationPlan(destination, world.map.map_id, world.map.frame,
                              tuple(nodes), tuple(world.map.nodes[n] for n in nodes), cost)
