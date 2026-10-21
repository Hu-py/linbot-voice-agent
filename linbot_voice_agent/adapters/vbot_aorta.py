"""Native VBOT inspection and executable-boundary dry runs. No motion calls."""
import importlib.util
import shutil
import subprocess
from ..safety.motion_guard import MotionGuard


class VbotAortaAdapter:
    simulated = False
    status = 'unknown'
    wave_preset = 'actions/WAVE.json'
    wave_resources = ('LIGHT', 'SCREEN', 'LEGS', 'HEAD')

    def inventory(self):
        return {'backend': 'aorta', 'sdk_discoverable': importlib.util.find_spec('aorta') is not None,
                'cli': shutil.which('aorta'), 'motion_enabled': False,
                'reported_version': 'v5.0.16-2026070701edu', 'application_version_verified': False}

    def dry_run(self, plan, spatial_map):
        reasons = []
        try:
            MotionGuard().check('navigate', simulated=False, spatial_map=spatial_map)
        except ValueError as exc:
            reasons.append(str(exc))
        reasons.append('尚无经过现场验证的导航执行器；路径查询不等于自动行走')
        return {**self.inventory(), 'destination': plan.destination, 'map_id': plan.map_id,
                'synthetic': spatial_map.synthetic, 'would_execute': False, 'reasons': reasons,
                'welcome_alias': 'welcome_wave', 'candidate_preset': self.wave_preset,
                'required_resources': list(self.wave_resources), 'optional_resources': ['AUDIO_OUT'],
                'path_query_route': '/slam/get_path_to_target', 'device_contacted': False}

    def inspect(self, *, authorized=False, runner=subprocess.run):
        """Explicit, bounded metadata inspection in an already configured device shell."""
        if not authorized:
            raise PermissionError('只读设备访问也需要调用方明确选择')
        if not shutil.which('aorta'):
            return {'ok': False, 'reason': 'sdk_cli_unavailable', 'device_contacted': False}
        results = []
        for command in (['aorta', 'action', 'info', '/rcp/execute_task'],
                        ['aorta', 'schema', 'get', '/odometry', '--describe'],
                        ['aorta', 'schema', 'get', '/slam/status', '--describe']):
            try:
                result = runner(command, capture_output=True, text=True, timeout=10, check=False)
                results.append({'route': command[3], 'ok': result.returncode == 0,
                                'description': result.stdout[:12000]})
            except (OSError, subprocess.TimeoutExpired):
                results.append({'route': command[3], 'ok': False, 'reason': 'unavailable_or_timeout'})
        return {'device_contacted': True, 'checks': results, 'motion_enabled': False}

    def execute(self, action, destination=None):
        return {'tool': action, 'ok': False, 'simulated': False, 'status': 'unknown',
                'error': 'hardware_execution_disabled'}

    def read_status(self, *, authorized=False, runner=subprocess.run):
        """Bounded raw samples, never interpreted as verified localization."""
        if not authorized:
            raise PermissionError('需要明确选择只读状态采样')
        if not shutil.which('aorta'):
            return {'ok': False, 'reason': 'sdk_cli_unavailable', 'device_contacted': False}
        samples = {}
        for topic in ('/slam/status', '/odometry'):
            try:
                result = runner(['aorta', 'topic', 'echo', topic, '--count', '1'],
                                capture_output=True, text=True, timeout=15, check=False)
                samples[topic] = {'ok': result.returncode == 0, 'raw': result.stdout[:12000]}
            except (OSError, subprocess.TimeoutExpired):
                samples[topic] = {'ok': False, 'reason': 'unavailable_or_timeout'}
        return {'samples': samples, 'device_contacted': True, 'verified': False,
                'pose': None, 'motion_enabled': False}

    def speak(self, text):
        return False

    def gesture(self, preset):
        return False

    def navigate(self, plan, task_id):
        return False

    def pause_navigation(self):
        return False

    def resume_navigation(self):
        return False

    def cancel_navigation(self):
        # No goal has ever been submitted by this adapter; do not stop another owner.
        return False

    def get_robot_status(self):
        return {'state': 'unknown', 'pose': None, 'verified': False, 'motion_enabled': False}
