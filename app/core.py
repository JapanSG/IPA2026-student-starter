import yaml


def validate_request(data):
    """
    Validate one IPA2026 desired-state request.

    Return a response dictionary according to
    specs/response-spec-v1.md.
    """
    if not isinstance(data, str):
        return {"status": "error", "result": "invalid_yaml"}

    try:
        request = yaml.safe_load(data)
    except yaml.YAMLError:
        return {"status": "error", "result": "invalid_yaml"}

    if not isinstance(request, dict):
        return {"status": "error", "result": "invalid_yaml"}

    if type(request.get("version")) is not int or request["version"] != 1:
        return {"status": "error", "result": "invalid_version"}

    router = request.get("router")
    if router is None:
        return {"status": "error", "result": "missing_router"}
    if router not in (
        "10.0.29.101",
        "10.0.29.102",
        "10.0.29.103",
        "10.0.29.104",
        "10.0.29.105",
    ):
        return {"status": "error", "result": "invalid_router"}

    method = request.get("method")
    if method is None:
        return {"status": "error", "result": "missing_method"}

    supported_actions = {
        "restconf": ("status", "plan", "apply"),
        "netconf": ("status", "plan", "apply"),
        "ansible": ("apply",),
        "netmiko-textfsm": ("status", "delete"),
    }
    if not isinstance(method, str) or method not in supported_actions:
        return {"status": "error", "result": "invalid_method"}

    action = request.get("action")
    if action is None:
        return {"status": "error", "result": "missing_action"}
    if action not in supported_actions[method]:
        return {"status": "error", "result": "invalid_action"}

    desired = request.get("desired")
    if desired is None:
        return {"status": "error", "result": "missing_desired"}

    interface = desired.get("interface") if isinstance(desired, dict) else None
    if interface is None:
        return {"status": "error", "result": "missing_interface"}

    if not isinstance(interface, dict) or interface.get("name") is None:
        return {"status": "error", "result": "missing_interface_name"}

    if action in {"plan", "apply"}:
        for field, error in (
            ("ipv4", "missing_ipv4"),
            ("description", "missing_description"),
            ("admin_state", "missing_admin_state"),
        ):
            if interface.get(field) is None:
                return {"status": "error", "result": error}
        if interface["admin_state"] not in ("up", "down"):
            return {"status": "error", "result": "invalid_admin_state"}

    return {"status": "ok", "result": "accepted"}


def process_request_file(path):
    """
    Read a YAML request file, validate it,
    and return a response dictionary.
    """
    with open(path, encoding="utf-8") as request_file:
        return validate_request(request_file.read())
