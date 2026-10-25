"""Part 4 student implementation."""

import logging

from app.backends import ansible, netconf, netmiko_textfsm, restconf


LOGGER = logging.getLogger(__name__)


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

    LOGGER.info(
        "Dispatching request method=%s action=%s router=%s",
        method,
        action,
        router,
    )
    if action == "status":
        response = backend.get_interface_state(router, interface["name"])
    elif action == "plan":
        response = backend.plan_interface(router, interface)
    elif action == "apply":
        response = backend.apply_interface(router, interface)
    elif action == "delete":
        response = backend.delete_interface(router, interface["name"])
    else:
        raise ValueError(f"Unsupported backend action: {action}")

    if response.get("status") == "error":
        LOGGER.error(
            "Backend request failed method=%s action=%s router=%s result=%s",
            method,
            action,
            router,
            response.get("result"),
        )
    return response
