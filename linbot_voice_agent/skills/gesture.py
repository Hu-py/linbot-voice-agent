"""Only the application welcome alias is allowed, never a model-supplied DAG."""
def gesture(adapter, preset, guard, spatial_map):
    guard.check(preset, simulated=adapter.simulated, spatial_map=spatial_map)
    return adapter.gesture(preset) is True
