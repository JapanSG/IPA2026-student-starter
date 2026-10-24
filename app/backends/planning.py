from app.planner import build_plan


def plan_interface(router, desired_interface, get_interface_state):
    """Build a Part 2 plan from a backend status response."""
    status = get_interface_state(router, desired_interface["name"])
    if status["status"] == "error":
        return status

    current_interface = (
        status["interface"] if status["result"] == "found" else None
    )
    return build_plan(desired_interface, current_interface)
