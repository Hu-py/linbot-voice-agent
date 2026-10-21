"""ROS bridge metadata inspection; no Nav2 assumption or speed publisher."""
import shutil
import subprocess
from .vbot_aorta import VbotAortaAdapter
from ..world.spatial_map import Pose2D


def verified_planar_pose(*, x, y, yaw, map_id, frame, expected_map, verified=False):
    """Accept only a caller-verified body pose, already projected into 2D.

    ROS /odometry's primary pose is NOT assumed to be the body pose. The caller
    must verify the body transform, map identity/origin and fresh localization.
    """
    if not verified or map_id != expected_map.map_id or frame != expected_map.frame or expected_map.synthetic:
        raise ValueError('Real pose requires verified body/map/frame provenance')
    pose = Pose2D(x, y, yaw)
    if not expected_map.segment_clear(pose, pose):
        raise ValueError('Pose outside verified free space')
    return pose


class VbotRos2Adapter(VbotAortaAdapter):
    def read_status(self, *, authorized=False, runner=subprocess.run):
        # Discovery is supported; do not guess an installed odometry message type.
        return {**self.inspect(authorized=authorized, runner=runner),
                'pose': None, 'verified': False, 'reason': 'body_pose_mapping_not_verified'}

    def inventory(self):
        return {'backend': 'ros2', 'cli': shutil.which('ros2'), 'motion_enabled': False,
                'application_version_verified': False, 'nav2_assumed': False}

    def inspect(self, *, authorized=False, runner=subprocess.run):
        if not authorized:
            raise PermissionError('需要明确选择只读发现')
        if not shutil.which('ros2'):
            return {'ok': False, 'reason': 'ros2_unavailable', 'device_contacted': False}
        results = []
        for command in (['ros2', 'topic', 'list', '--no-daemon', '-t'],
                        ['ros2', 'service', 'list', '-t'], ['ros2', 'action', 'list', '-t']):
            try:
                result = runner(command, capture_output=True, text=True, timeout=10, check=False)
                results.append({'kind': command[1], 'ok': result.returncode == 0,
                                'description': result.stdout[:12000]})
            except (OSError, subprocess.TimeoutExpired):
                results.append({'kind': command[1], 'ok': False})
        return {'checks': results, 'device_contacted': True, 'motion_enabled': False}
