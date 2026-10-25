"""Part 4 student implementation."""

from app.backends import ansible, netconf, netmiko_textfsm, restconf


def dispatch_request(data):
    method = data["method"]
    action = data["action"]
    router = data["router"]
    interface = data["desired"]["interface"]

    if method == "restconf":
        backend = restconf
    elif method == "netconf":
        backend = netconf
    elif method == "ansible":
        backend = ansible
    elif method == "netmiko-textfsm":
        backend = netmiko_textfsm
    else:
        raise ValueError(f"Unsupported backend method: {method}")

    if action == "status":
        return backend.get_interface_state(router, interface["name"])
    if action == "plan":
        return backend.plan_interface(router, interface)
    if action == "apply":
        return backend.apply_interface(router, interface)
    if action == "delete":
        return backend.delete_interface(router, interface["name"])

    raise ValueError(f"Unsupported backend action: {action}")
