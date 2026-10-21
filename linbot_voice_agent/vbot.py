"""Offline VBOT inventory. Never opens a robot session or imports native SDK code."""
import importlib.metadata
import importlib.util
import platform
import shutil
import sys

REFERENCE = 'https://github.com/VitaDynamics/vbot-lab/blob/main/docs/compatibility.md'
SDK_PAIRING = {
    'aorta-sdk': '2026.9.23',
    'aorta-msgs': '2026.9.23',
    'vbot-edu-msgs': '2026.9.24',
    'flatbuffers': '25.12.19',
}


def validate_profile(profile):
    """A device record is user-supplied evidence, never a compatibility grant."""
    fields = {'robot_type', 'reported_version', 'application_version'}
    if not isinstance(profile, dict) or set(profile) != fields:
        raise ValueError('设备档案必须且只能包含 robot_type、reported_version、application_version')
    for field in fields:
        value = profile[field]
        if value is None and field == 'application_version':
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError(f'设备档案字段 {field} 必须为非空短字符串；未知应用版本请填 null')
    return dict(profile)


def inventory():
    packages = {}
    for name in SDK_PAIRING:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        'system': platform.system(),
        'architecture': platform.machine(),
        'python': platform.python_version(),
        'python_supported': sys.version_info >= (3, 10),
        'rclpy_discoverable': importlib.util.find_spec('rclpy') is not None,
        'tools': {name: shutil.which(name) for name in ('ros2', 'aorta', 'zenohd')},
        'packages': packages,
    }


def readiness_report(profile, host=None):
    profile = validate_profile(profile)
    host = inventory() if host is None else host
    issues = []
    if profile['robot_type'] != 'foot_quadruped':
        issues.append('官方当前范围仅包含 foot_quadruped EDU。')
    if profile['application_version'] != 'V1.6.0':
        issues.append('尚未确认应用版本为 V1.6.0；reported_version 不能代替应用版本。')
    if not host['python_supported']:
        issues.append('需要 Python 3.10 或更新版本。')
    for name, version in SDK_PAIRING.items():
        installed = host['packages'].get(name)
        if installed != version:
            issues.append(f'{name}: 当前 {installed or "未安装"}，文档参考配对 {version}；安装前核对 Release manifest。')
    if not host['rclpy_discoverable'] or not host['tools'].get('ros2'):
        issues.append('当前 shell 未发现完整 ROS 2 Python 环境；仅在选择 ROS 2 桥接时需要。')
    issues.extend([
        '未验证设备会话配置、路由 ACL、消息类型、QoS 或实时样本。',
        'ROS 2 仅桥接 Aorta 的部分接口；安装 Humble 不等于可访问机器人。',
        '运动仍使用模拟机器人；可选 ROS 2 语音适配器需单独执行 --check 和现场验收。',
    ])
    return {
        'scope': 'offline_inventory',
        'device_contacted': False,
        'hardware_ready': False,
        'profile': profile,
        'host': host,
        'reference': REFERENCE,
        'reference_sdk_release': 'edu-sdk-2026.9.24',
        'reference_pairing': dict(SDK_PAIRING),
        'issues': issues,
    }
