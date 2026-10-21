"""Validated destination navigation with an explicit confirmed task identity."""
def navigate(adapter, plan, task_id, guard, spatial_map):
    guard.check('navigate', simulated=adapter.simulated, spatial_map=spatial_map)
    return adapter.navigate(plan, task_id) is True
