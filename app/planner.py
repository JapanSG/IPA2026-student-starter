def planned_result_dict(operation, changes):
    """
    Generate the planned result dictionary (Helper function for build_plan() function)
    """
    return {
    "status": "ok",
    "result": "planned",
    "operation": operation,
    "changes": changes
    }

def build_plan(desired_interface, current_interface):
    """
    Compare desired interface state with normalized current state
    and return a plan according to specs/plan-spec-v1.md.
    """
    mappings = ["ipv4", "description", "admin_state"]
    operation = "no_change"
    changes = {}

    # Create
    if current_interface is None:
        operation = "create"
        for mapping in mappings:
            changes[mapping] = {"to" : desired_interface[mapping]}
        return planned_result_dict(operation, changes)

    # Update and No Change
    for mapping in mappings:
        if current_interface[mapping] != desired_interface[mapping]:
            changes[mapping] = {
                "from": current_interface[mapping],
                "to": desired_interface[mapping]
            }
    if changes:
        operation = "update"
    return planned_result_dict(operation, changes)

if __name__ == "__main__":
    desired_interface = {
    "name": "Loopback123",
    "ipv4": "172.23.123.1/32",
    "description": "New_description",
    "admin_state": "down"
    }
    current_interface = {
    "name": "Loopback123",
    "ipv4": "172.23.123.1/32",
    "description": "Old",
    "admin_state": "up"
    }
    print(build_plan(desired_interface, current_interface))
