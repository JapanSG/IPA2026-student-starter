from app.planner import build_plan


def test_plan_is_deterministic_and_uses_only_supported_operations():
    desired = {
        "name": "Loopback777",
        "ipv4": "192.0.2.77/32",
        "description": "custom-plan",
        "admin_state": "down",
    }
    current = {
        "name": "Loopback777",
        "ipv4": "192.0.2.7/32",
        "description": "old-description",
        "admin_state": "up",
    }

    first_result = build_plan(desired, current)
    second_result = build_plan(desired, current)

    assert first_result == second_result
    assert first_result["operation"] in {"create", "update", "no_change"}
    assert set(first_result["changes"]) == {
        "ipv4",
        "description",
        "admin_state",
    }
