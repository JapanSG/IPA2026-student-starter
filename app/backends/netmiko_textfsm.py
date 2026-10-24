import os
import re
from ipaddress import ip_interface


def _error(result):
    return {"status": "error", "result": result}


def _map_exception(error):
    from textfsm.parser import TextFSMError
    from netmiko.exceptions import (
        NetmikoBaseException,
        NetmikoAuthenticationException,
        NetmikoTimeoutException,
        NetmikoParsingException,
        ConnectionException,
    )
    from paramiko.ssh_exception import AuthenticationException, SSHException

    if isinstance(
        error,
        (
            PermissionError,
            NetmikoAuthenticationException,
            AuthenticationException,
        ),
    ):
        return _error("authentication_failed")
    if isinstance(
        error,
        (
            ConnectionError,
            TimeoutError,
            OSError,
            NetmikoTimeoutException,
            ConnectionException,
            SSHException,
        ),
    ):
        return _error("connection_failed")
    if isinstance(
        error,
        (
            NetmikoBaseException,
            NetmikoParsingException,
            TextFSMError,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
        ),
    ):
        return _error("backend_failed")
    return _error("backend_failed")


def _handled_errors():
    from netmiko.exceptions import NetmikoBaseException
    from textfsm.parser import TextFSMError
    from paramiko.ssh_exception import AuthenticationException, SSHException

    return (
        PermissionError,
        ConnectionError,
        TimeoutError,
        OSError,
        AuthenticationException,
        SSHException,
        NetmikoBaseException,
        TextFSMError,
        RuntimeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    )


def _connect(router):
    from netmiko import ConnectHandler

    return ConnectHandler(
        device_type="cisco_ios",
        host=router,
        username=os.environ["ROUTER_USERNAME"],
        password=os.environ["ROUTER_PASSWORD"],
        timeout=10,
    )


def _real_get(router, interface_name):
    connection = _connect(router)
    try:
        ip_info = connection.send_command(
            f"show ip interface {interface_name}",
            use_textfsm=True,
        )
        descriptions = connection.send_command(
            "show interfaces description",
            use_textfsm=True,
        )
        return ip_info, descriptions
    finally:
        connection.disconnect()


def _record_name(value):
    name = str(value or "").strip()
    if name.startswith("Lo") and not name.startswith("Loopback"):
        return f"Loopback{name[2:]}"
    return name


def _extract_interface(ip_info, descriptions, interface_name):
    if not isinstance(ip_info, list) or not isinstance(descriptions, list):
        raise ValueError("TextFSM output must be a list of records")

    desired_name = _record_name(interface_name)
    record = next(
        (
            item
            for item in ip_info
            if isinstance(item, dict)
            and _record_name(item.get("interface")) == desired_name
        ),
        None,
    )
    if record is None:
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    ips = record.get("ip_address", [])
    prefixes = record.get("prefix_length", [])
    if isinstance(ips, str):
        ips = [ips]
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    if not ips or not prefixes:
        raise ValueError("TextFSM record has no IPv4 address or prefix")

    description_record = next(
        (
            item
            for item in descriptions
            if isinstance(item, dict)
            and _record_name(item.get("port")) == desired_name
        ),
        None,
    )
    link_status = str(record.get("link_status", "")).lower()
    return {
        "status": "ok",
        "result": "found",
        "interface": {
            "name": str(record.get("interface") or interface_name),
            "ipv4": str(ip_interface(f"{ips[0]}/{prefixes[0]}")),
            "description": (
                description_record.get("description")
                if description_record is not None
                else None
            ),
            "admin_state": (
                "down" if "administratively down" in link_status else "up"
            ),
        },
    }


def get_interface_state(router, interface_name, netmiko_get=None):
    """Return normalized interface status using Netmiko + TextFSM."""
    try:
        if netmiko_get is None:
            ip_info, descriptions = _real_get(router, interface_name)
        else:
            ip_info, descriptions = netmiko_get(router, interface_name)
        return _extract_interface(ip_info, descriptions, interface_name)
    except _handled_errors() as error:
        return _map_exception(error)


def _validate_interface_name(interface_name):
    return isinstance(interface_name, str) and re.fullmatch(
        r"Loopback\d+", interface_name
    ) is not None


def _real_delete(router, interface_name):
    connection = _connect(router)
    try:
        connection.send_config_set([f"no interface {interface_name}"])
    finally:
        connection.disconnect()


def delete_interface(router, interface_name, netmiko_delete=None):
    """Delete a Loopback interface using Netmiko."""
    if not _validate_interface_name(interface_name):
        return _error("backend_failed")

    if netmiko_delete is not None:
        try:
            result = netmiko_delete(router, interface_name)
        except _handled_errors() as error:
            return _map_exception(error)
        if result == "deleted":
            return {"status": "ok", "result": "deleted"}
        if result == "not_found":
            return {
                "status": "ok",
                "result": "not_found",
                "interface": None,
            }
        return _error("backend_failed")

    before = get_interface_state(router, interface_name)
    if before["status"] == "error":
        return before
    if before["result"] == "not_found":
        return {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        }

    try:
        _real_delete(router, interface_name)
    except _handled_errors() as error:
        return _map_exception(error)

    after = get_interface_state(router, interface_name)
    if after["status"] == "error":
        return after
    if after["result"] == "not_found":
        return {"status": "ok", "result": "deleted"}
    return _error("backend_failed")
