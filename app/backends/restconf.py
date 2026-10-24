import os
import re
from ipaddress import IPv4Interface

import requests

from app.planner import build_plan


def _error(result):
    return {"status": "error", "result": result}


def _request_error(error):
    if isinstance(error, PermissionError):
        return _error("authentication_failed")
    if isinstance(
        error,
        (
            ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ),
    ):
        return _error("connection_failed")
    return _error("backend_failed")


def _get_response(url, http_get):
    if http_get is not None:
        return http_get(url)

    return requests.get(
        url,
        auth=(os.environ["ROUTER_USERNAME"], os.environ["ROUTER_PASSWORD"]),
        headers={"Accept": "application/yang-data+json"},
        timeout=10,
    )


def get_interface_state(router, interface_name, http_get=None):
    """Return normalized interface status using RESTCONF."""
    url = (
        f"https://{router}/restconf/data/"
        f"ietf-interfaces:interfaces/interface={interface_name}"
    )

    try:
        response = _get_response(url, http_get)
    except PermissionError as error:
        return _request_error(error)
    except (
        ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    ) as error:
        return _request_error(error)
    except (requests.exceptions.RequestException, RuntimeError, KeyError) as error:
        return _request_error(error)

    if response.status_code in (401, 403):
        return _error("authentication_failed")
    if response.status_code == 404:
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }
    if response.status_code != 200:
        return _error("backend_failed")

    try:
        raw_interface = response.json()["ietf-interfaces:interface"]
        addresses = raw_interface["ietf-ip:ipv4"]["address"]
        if not isinstance(addresses, list) or not addresses:
            return _error("backend_failed")

        primary_address = addresses[0]
        ipv4 = str(
            IPv4Interface(
                f"{primary_address['ip']}/{primary_address['netmask']}"
            )
        )
        enabled = raw_interface["enabled"]
        if not isinstance(enabled, bool):
            return _error("backend_failed")

        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": raw_interface["name"],
                "ipv4": ipv4,
                "description": raw_interface.get("description"),
                "admin_state": "up" if enabled else "down",
            },
        }
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        requests.exceptions.JSONDecodeError,
    ):
        return _error("backend_failed")


def plan_interface(router, desired_interface, http_get=None):
    """Return a create/update/no_change plan using RESTCONF status."""
    status = get_interface_state(router, desired_interface["name"], http_get)
    if status["status"] == "error":
        return status

    current_interface = (
        status["interface"] if status["result"] == "found" else None
    )
    return build_plan(desired_interface, current_interface)


def apply_interface(router, desired_interface, http_put=None):
    """Apply complete desired interface state using RESTCONF."""
    try:
        match = re.fullmatch(r"Loopback(\d+)", desired_interface["name"])
        if match is None:
            return _error("backend_failed")

        ipv4 = IPv4Interface(desired_interface["ipv4"])
        loopback_number = int(match.group(1))
        loopback = {
            "name": loopback_number,
            "description": desired_interface["description"],
            "ip": {
                "address": {
                    "primary": {
                        "address": str(ipv4.ip),
                        "mask": str(ipv4.network.netmask),
                    }
                }
            },
        }
        if desired_interface["admin_state"] == "down":
            loopback["shutdown"] = [None]
        elif desired_interface["admin_state"] != "up":
            return _error("backend_failed")

        url = (
            f"https://{router}/restconf/data/"
            f"Cisco-IOS-XE-native:native/interface/Loopback={loopback_number}"
        )
        payload = {"Cisco-IOS-XE-native:Loopback": loopback}
    except (KeyError, TypeError, ValueError):
        return _error("backend_failed")

    try:
        if http_put is None:
            response = requests.put(
                url,
                json=payload,
                auth=(os.environ["ROUTER_USERNAME"], os.environ["ROUTER_PASSWORD"]),
                headers={"Accept": "application/yang-data+json"},
                timeout=10,
            )
        else:
            response = http_put(url, json=payload)
    except PermissionError as error:
        return _request_error(error)
    except (
        ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    ) as error:
        return _request_error(error)
    except (requests.exceptions.RequestException, RuntimeError, KeyError) as error:
        return _request_error(error)

    if response.status_code in (401, 403):
        return _error("authentication_failed")
    if response.status_code not in (200, 201, 204):
        return _error("backend_failed")

    return {"status": "ok", "result": "applied"}
