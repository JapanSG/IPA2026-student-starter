import os
import xml.etree.ElementTree as ET
from ipaddress import IPv4Interface

from app.planner import build_plan


NETCONF_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
INTERFACES_NS = "urn:ietf:params:xml:ns:yang:ietf-interfaces"
IP_NS = "urn:ietf:params:xml:ns:yang:ietf-ip"
NATIVE_NS = "http://cisco.com/ns/yang/Cisco-IOS-XE-native"


def _error(result):
    return {"status": "error", "result": result}


def _connection(router):
    from ncclient import manager

    return manager.connect(
        host=router,
        port=830,
        username=os.environ["ROUTER_USERNAME"],
        password=os.environ["ROUTER_PASSWORD"],
        hostkey_verify=False,
        device_params={"name": "iosxe"},
        allow_agent=False,
        look_for_keys=False,
        timeout=10,
    )


def _map_exception(error):
    from ncclient.operations.rpc import RPCError, TimeoutExpiredError
    from ncclient.transport.errors import (
        AuthenticationError,
        NCClientError,
        SessionCloseError,
        SSHError,
        TransportError,
    )
    from paramiko.ssh_exception import AuthenticationException, SSHException

    if isinstance(
        error,
        (PermissionError, AuthenticationError, AuthenticationException),
    ):
        return _error("authentication_failed")
    if isinstance(
        error,
        (
            ConnectionError,
            TimeoutError,
            OSError,
            SSHError,
            SSHException,
            SessionCloseError,
            TransportError,
            TimeoutExpiredError,
        ),
    ):
        return _error("connection_failed")
    if isinstance(
        error,
        (
            NCClientError,
            RPCError,
            RuntimeError,
            ET.ParseError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ),
    ):
        return _error("backend_failed")
    return _error("backend_failed")


def _handled_errors():
    from ncclient.operations.rpc import NCClientError
    from ncclient.transport.errors import NCClientError as TransportClientError
    from paramiko.ssh_exception import AuthenticationException, SSHException

    return (
        PermissionError,
        ConnectionError,
        TimeoutError,
        OSError,
        AuthenticationException,
        SSHException,
        NCClientError,
        TransportClientError,
        RuntimeError,
        ET.ParseError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    )


def _get_config(router, filter_xml, netconf_get_config):
    if netconf_get_config is not None:
        return netconf_get_config(router, filter_xml)
    with _connection(router) as session:
        response = session.get_config(source="running", filter=("subtree", filter_xml))
    return response.data_xml


def _interface_filter(interface_name):
    root = ET.Element(f"{{{INTERFACES_NS}}}interfaces")
    interface = ET.SubElement(root, f"{{{INTERFACES_NS}}}interface")
    ET.SubElement(interface, f"{{{INTERFACES_NS}}}name").text = interface_name
    return ET.tostring(root, encoding="unicode")


def get_interface_state(router, interface_name, netconf_get_config=None):
    """Return normalized interface status using NETCONF."""
    try:
        xml_data = _get_config(
            router,
            _interface_filter(interface_name),
            netconf_get_config,
        )
        root = ET.fromstring(xml_data)
        interface = root.find(f".//{{{INTERFACES_NS}}}interface")
        if interface is None:
            return {
                "status": "ok",
                "result": "not_found",
                "interface": None,
            }

        name = interface.findtext(f"{{{INTERFACES_NS}}}name")
        enabled_text = interface.findtext(f"{{{INTERFACES_NS}}}enabled")
        address = interface.find(
            f"{{{IP_NS}}}ipv4/{{{IP_NS}}}address"
        )
        if address is None:
            return _error("backend_failed")
        ip = address.findtext(f"{{{IP_NS}}}ip")
        netmask = address.findtext(f"{{{IP_NS}}}netmask")
        if not name or not ip or not netmask or enabled_text not in ("true", "false"):
            return _error("backend_failed")

        ipv4 = str(IPv4Interface(f"{ip}/{netmask}"))
        return {
            "status": "ok",
            "result": "found",
            "interface": {
                "name": name,
                "ipv4": ipv4,
                "description": interface.findtext(
                    f"{{{INTERFACES_NS}}}description"
                ),
                "admin_state": "up" if enabled_text == "true" else "down",
            },
        }
    except _handled_errors() as error:
        return _map_exception(error)


def plan_interface(router, desired_interface, netconf_get_config=None):
    """Return a plan using normalized NETCONF status."""
    status = get_interface_state(
        router,
        desired_interface["name"],
        netconf_get_config,
    )
    if status["status"] == "error":
        return status

    current_interface = (
        status["interface"] if status["result"] == "found" else None
    )
    return build_plan(desired_interface, current_interface)


def _native_config(desired_interface):
    name = desired_interface["name"]
    prefix = "Loopback"
    if not name.startswith(prefix) or not name[len(prefix):].isdigit():
        raise ValueError("Loopback name must end with its numeric key")
    if desired_interface["admin_state"] not in ("up", "down"):
        raise ValueError("admin_state must be up or down")

    ipv4 = IPv4Interface(desired_interface["ipv4"])
    root = ET.Element(f"{{{NETCONF_NS}}}config")
    native = ET.SubElement(root, f"{{{NATIVE_NS}}}native")
    interfaces = ET.SubElement(native, f"{{{NATIVE_NS}}}interface")
    loopback = ET.SubElement(
        interfaces,
        f"{{{NATIVE_NS}}}Loopback",
        {f"{{{NETCONF_NS}}}operation": "replace"},
    )
    ET.SubElement(loopback, f"{{{NATIVE_NS}}}name").text = name[len(prefix):]
    ET.SubElement(loopback, f"{{{NATIVE_NS}}}description").text = (
        desired_interface["description"]
    )
    ip = ET.SubElement(loopback, f"{{{NATIVE_NS}}}ip")
    address = ET.SubElement(ip, f"{{{NATIVE_NS}}}address")
    primary = ET.SubElement(address, f"{{{NATIVE_NS}}}primary")
    ET.SubElement(primary, f"{{{NATIVE_NS}}}address").text = str(ipv4.ip)
    ET.SubElement(primary, f"{{{NATIVE_NS}}}mask").text = str(
        ipv4.network.netmask
    )
    if desired_interface["admin_state"] == "down":
        ET.SubElement(loopback, f"{{{NATIVE_NS}}}shutdown")

    return ET.tostring(root, encoding="unicode")


def apply_interface(router, desired_interface, netconf_edit_config=None):
    """Apply complete desired interface state using NETCONF."""
    try:
        config_xml = _native_config(desired_interface)
        if netconf_edit_config is not None:
            netconf_edit_config(router, config_xml)
        else:
            with _connection(router) as session:
                reply = session.edit_config(target="running", config=config_xml)
            if getattr(reply, "ok", True) is False:
                return _error("backend_failed")
        return {"status": "ok", "result": "applied"}
    except _handled_errors() as error:
        return _map_exception(error)
