"""Explicit hardware admission checks. SDK stop is never a hardware E-stop."""
from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareConditions:
    enabled: bool = False
    supervised: bool = False
    stop_available: bool = False
    sdk_matched: bool = False
    control_authorized: bool = False
    resource_free: bool = False
    device_ready: bool = False
    map_verified: bool = False


class MotionGuard:
    presets = frozenset({'welcome_wave'})

    def check(self, action, *, simulated, spatial_map, conditions=None):
        if action not in {'navigate', 'welcome_wave'}:
            raise ValueError('动作不在白名单')
        if spatial_map.data['floor_id'] != 1:
            raise ValueError('仅支持二维一楼地图')
        if simulated:
            return True
        if spatial_map.synthetic:
            raise ValueError('禁止将 synthetic 坐标发送至真实设备')
        conditions = conditions or HardwareConditions()
        if not all(getattr(conditions, name) is True for name in conditions.__dataclass_fields__):
            raise ValueError('真实动作默认禁用；现场、安全、版本、资源和地图条件未全部满足')
        return True
