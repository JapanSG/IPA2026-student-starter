import json
import xml.etree.ElementTree as ET
from pathlib import Path

import ncclient.manager
import requests
import yaml
from ncclient.operations.rpc import RPCError
from ncclient.transport.errors import AuthenticationError, SSHError
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoParsingException,
    NetmikoTimeoutException,
)
from paramiko.ssh_exception import AuthenticationException, SSHException
from textfsm.parser import TextFSMError

from app.backends import ansible, netconf, netmiko_textfsm, restconf
from app.backends.planning import plan_interface as plan_from_status


DESIRED = {
    "name": "Loopback777",
    "ipv4": "192.0.2.77/24",
    "description": "custom-backend",
    "admin_state": "up",
}


def response(status_code, payload=None):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return payload

    return FakeResponse()


def test_restconf_returns_backend_failed_for_malformed_success_response():
    assert restconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda url: response(200, {"unexpected": {}}),
    ) == {"status": "error", "result": "backend_failed"}


def test_restconf_default_transport_uses_https_credentials_and_json_headers(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response(404)

    monkeypatch.setattr(restconf.requests, "get", fake_get)
    assert restconf.get_interface_state(
        "10.0.29.107",
        "Loopback777",
    ) == {
        "status": "ok",
        "result": "not_found",
        "interface": None,
    }
    assert calls == [
        (
            "https://10.0.29.107/restconf/data/"
            "ietf-interfaces:interfaces/interface=Loopback777",
            {
                "auth": ("test-user", "test-password"),
                "headers": {"Accept": "application/yang-data+json"},
                "timeout": 10,
            },
        )
    ]


def test_restconf_default_put_uses_native_loopback_replacement(monkeypatch):
    calls = []
    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")

    def fake_put(url, **kwargs):
        calls.append((url, kwargs))
        return response(200)

    monkeypatch.setattr(restconf.requests, "put", fake_put)
    assert restconf.apply_interface("10.0.29.107", DESIRED) == {
        "status": "ok",
        "result": "applied",
    }
    assert calls[0][0].endswith(
        "/Cisco-IOS-XE-native:native/interface/Loopback=777"
    )
    assert calls[0][1]["auth"] == ("test-user", "test-password")
    assert calls[0][1]["json"]["Cisco-IOS-XE-native:Loopback"]["name"] == 777

    assert restconf.apply_interface(
        "10.0.29.101",
        {**DESIRED, "ipv4": "not-an-ipv4"},
        lambda url, json: response(204),
    ) == {"status": "error", "result": "backend_failed"}


def test_netconf_normalizes_prefix_and_missing_description():
    xml_data = """\
    <data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
      <interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">
        <interface>
          <name>Loopback777</name>
          <enabled>false</enabled>
          <ipv4 xmlns="urn:ietf:params:xml:ns:yang:ietf-ip">
            <address>
              <ip>192.0.2.77</ip>
              <netmask>255.255.255.0</netmask>
            </address>
          </ipv4>
        </interface>
      </interfaces>
    </data>
    """

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: xml_data,
    ) == {
        "status": "ok",
        "result": "found",
        "interface": {
            "name": "Loopback777",
            "ipv4": "192.0.2.77/24",
            "description": None,
            "admin_state": "down",
        },
    }


def test_netconf_plan_uses_none_for_absent_interface_and_propagates_errors():
    result = netconf.plan_interface(
        "10.0.29.101",
        DESIRED,
        lambda router, filter_xml: (
            '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>'
        ),
    )
    assert result["operation"] == "create"
    assert result["changes"]["ipv4"] == {"to": "192.0.2.77/24"}

    assert netconf.plan_interface(
        "10.0.29.101",
        DESIRED,
        lambda router, filter_xml: (_ for _ in ()).throw(
            ConnectionError("offline")
        ),
    ) == {"status": "error", "result": "connection_failed"}

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: (_ for _ in ()).throw(
            PermissionError("rejected")
        ),
    ) == {"status": "error", "result": "authentication_failed"}

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: "<not-valid-xml",
    ) == {"status": "error", "result": "backend_failed"}

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: (_ for _ in ()).throw(
            AuthenticationError("rejected")
        ),
    ) == {"status": "error", "result": "authentication_failed"}

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: (_ for _ in ()).throw(
            SSHError("ssh failure")
        ),
    ) == {"status": "error", "result": "connection_failed"}

    assert netconf.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, filter_xml: (_ for _ in ()).throw(
            RPCError(ET.Element("rpc-error"))
        ),
    ) == {"status": "error", "result": "backend_failed"}


def test_restconf_and_netconf_plan_found_state_updates_only_differences():
    current = {
        "name": DESIRED["name"],
        "ipv4": DESIRED["ipv4"],
        "description": "old-description",
        "admin_state": DESIRED["admin_state"],
    }
    restconf_payload = {
        "ietf-interfaces:interface": {
            "name": current["name"],
            "description": current["description"],
            "enabled": True,
            "ietf-ip:ipv4": {
                "address": [
                    {
                        "ip": "192.0.2.77",
                        "netmask": "255.255.255.0",
                    }
                ]
            },
        }
    }
    netconf_xml = f"""\
    <data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
      <interfaces xmlns="{netconf.INTERFACES_NS}">
        <interface>
          <name>{current["name"]}</name>
          <description>{current["description"]}</description>
          <enabled>true</enabled>
          <ipv4 xmlns="{netconf.IP_NS}">
            <address>
              <ip>192.0.2.77</ip>
              <netmask>255.255.255.0</netmask>
            </address>
          </ipv4>
        </interface>
      </interfaces>
    </data>
    """

    assert restconf.plan_interface(
        "10.0.29.101",
        DESIRED,
        lambda url: response(
            200,
            restconf_payload,
        ),
    ) == {
        "status": "ok",
        "result": "planned",
        "operation": "update",
        "changes": {
            "description": {
                "from": "old-description",
                "to": "custom-backend",
            }
        },
    }
    assert netconf.plan_interface(
        "10.0.29.101",
        DESIRED,
        lambda router, filter_xml: netconf_xml,
    ) == {
        "status": "ok",
        "result": "planned",
        "operation": "update",
        "changes": {
            "description": {
                "from": "old-description",
                "to": "custom-backend",
            }
        },
    }

def test_netconf_apply_handles_non_32_prefix_and_up_state():
    calls = []

    def edit_config(router, config_xml):
        calls.append((router, config_xml))

    assert netconf.apply_interface(
        "10.0.29.101",
        DESIRED,
        edit_config,
    ) == {"status": "ok", "result": "applied"}

    assert len(calls) == 1
    root = ET.fromstring(calls[0][1])
    ns = {"native": netconf.NATIVE_NS}
    loopback = root.find(".//native:Loopback", ns)
    assert loopback is not None
    assert loopback.findtext("native:name", namespaces=ns) == "777"
    assert loopback.findtext(
        ".//native:mask",
        namespaces=ns,
    ) == "255.255.255.0"
    assert loopback.find("native:shutdown", ns) is None


def test_netconf_default_transport_uses_port_830_running_config_and_credentials(
    monkeypatch,
):
    calls = []
    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def get_config(self, **kwargs):
            calls.append(("get_config", kwargs))
            return type(
                "Response",
                (),
                {
                    "data_xml": (
                        '<data xmlns="urn:ietf:params:xml:ns:netconf:base:1.0"/>'
                    )
                },
            )()

        def edit_config(self, **kwargs):
            calls.append(("edit_config", kwargs))
            return type("Reply", (), {"ok": True})()

    def fake_connect(**kwargs):
        calls.append(("connect", kwargs))
        return FakeSession()

    monkeypatch.setattr(ncclient.manager, "connect", fake_connect)
    assert netconf.get_interface_state("10.0.29.107", "Loopback777")[
        "result"
    ] == "not_found"
    assert netconf.apply_interface("10.0.29.107", DESIRED) == {
        "status": "ok",
        "result": "applied",
    }

    connect_calls = [kwargs for name, kwargs in calls if name == "connect"]
    assert len(connect_calls) == 2
    assert all(
        options["port"] == 830
        and options["username"] == "test-user"
        and options["password"] == "test-password"
        for options in connect_calls
    )
    get_call = next(kwargs for name, kwargs in calls if name == "get_config")
    assert get_call["source"] == "running"
    assert get_call["filter"][0] == "subtree"
    filter_root = ET.fromstring(get_call["filter"][1])
    assert filter_root.tag == (
        f"{{{netconf.INTERFACES_NS}}}interfaces"
    )
    assert filter_root.findtext(
        f"{{{netconf.INTERFACES_NS}}}interface/"
        f"{{{netconf.INTERFACES_NS}}}name"
    ) == "Loopback777"
    edit_call = next(kwargs for name, kwargs in calls if name == "edit_config")
    assert edit_call["target"] == "running"


def test_netconf_rejected_edit_reply_returns_backend_error(monkeypatch):
    class RejectedReply:
        ok = False

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def edit_config(self, **kwargs):
            return RejectedReply()

    assert netconf.apply_interface(
        "10.0.29.101",
        DESIRED,
        lambda router, config_xml: None,
    ) == {"status": "ok", "result": "applied"}

    # Exercise the real-session path's reply handling without opening a socket.
    monkeypatch.setattr(ncclient.manager, "connect", lambda **kwargs: FakeSession())
    assert netconf.apply_interface(
        "10.0.29.101",
        DESIRED,
    ) == {"status": "error", "result": "backend_failed"}


def test_ansible_apply_passes_normalized_non_32_prefix_variables():
    calls = []
    assert ansible.apply_interface(
        "10.0.29.101",
        DESIRED,
        calls.append,
    ) == {"status": "ok", "result": "applied"}

    assert calls == [
        {
            "router": "10.0.29.101",
            "interface_name": "Loopback777",
            "description": "custom-backend",
            "ipv4_address": "192.0.2.77",
            "netmask": "255.255.255.0",
            "shutdown": False,
        }
    ]


def test_ansible_classifies_unreachable_playbook_failures(monkeypatch):
    def fail_with_unreachable(router, variables):
        raise ansible._PlaybookFailure("UNREACHABLE! connection timed out")

    monkeypatch.setattr(ansible, "_run_real_playbook", fail_with_unreachable)
    assert ansible.apply_interface(
        "10.0.29.101",
        DESIRED,
    ) == {"status": "error", "result": "connection_failed"}


def test_ansible_classifies_authentication_and_generic_playbook_failures(
    monkeypatch,
):
    for message, expected in (
        ("Authentication failed: invalid password", "authentication_failed"),
        ("Task failed with an unexpected module error", "backend_failed"),
    ):
        monkeypatch.setattr(
            ansible,
            "_run_real_playbook",
            lambda router, variables, message=message: (
                _raise_playbook_failure(message)
            ),
        )
        assert ansible.apply_interface(
            "10.0.29.101",
            DESIRED,
        ) == {"status": "error", "result": expected}


def test_ansible_default_runner_invokes_playbook_with_router_and_variables(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(ansible.shutil, "which", lambda name: "ansible-playbook")
    monkeypatch.setattr(
        ansible.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or type("Completed", (), {"returncode": 0})(),
    )

    assert ansible.apply_interface("10.0.29.101", DESIRED) == {
        "status": "ok",
        "result": "applied",
    }
    command, options = calls[0]
    assert command[0] == "ansible-playbook"
    assert command[command.index("--inventory") + 1] == "10.0.29.101,"
    variables = json.loads(command[command.index("--extra-vars") + 1])
    assert variables == {
        "router": "10.0.29.101",
        "interface_name": "Loopback777",
        "description": "custom-backend",
        "ipv4_address": "192.0.2.77",
        "netmask": "255.255.255.0",
        "shutdown": False,
    }
    assert command[-1] == str(ansible.PLAYBOOK)
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["timeout"] == 180


def _raise_playbook_failure(message):
    raise ansible._PlaybookFailure(message)


def test_ansible_playbook_uses_network_cli_and_ios_config_defaults():
    playbook_path = (
        Path(__file__).resolve().parents[1]
        / "ansible"
        / "apply_interface.yml"
    )
    playbook = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))
    assert playbook[0]["connection"] == "ansible.netcommon.network_cli"
    task = playbook[0]["tasks"][0]["cisco.ios.ios_config"]
    assert playbook[0]["hosts"] == "all"
    assert playbook[0]["vars"]["ansible_network_os"] == "cisco.ios.ios"
    assert task["defaults"] is True
    assert task["parents"] == ["interface {{ interface_name }}"]
    assert task["lines"] == [
        "description {{ description }}",
        "ip address {{ ipv4_address }} {{ netmask }}",
        "{{ 'shutdown' if shutdown else 'no shutdown' }}",
    ]


def test_netmiko_matches_ios_abbreviated_interface_and_allows_missing_description():
    ip_info = [
        {
            "interface": "Loopback777",
            "link_status": "administratively down",
            "ip_address": ["192.0.2.77"],
            "prefix_length": ["24"],
        }
    ]

    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (ip_info, [{"port": "Lo778"}]),
    ) == {
        "status": "ok",
        "result": "found",
        "interface": {
            "name": "Loopback777",
            "ipv4": "192.0.2.77/24",
            "description": None,
            "admin_state": "down",
        },
    }

    result = netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (
            ip_info,
            [{"port": "Lo777", "description": "matched-abbreviation"}],
        ),
    )
    assert result["interface"]["description"] == "matched-abbreviation"
    multiple_ip_info = [
        {
            **ip_info[0],
            "ip_address": ["192.0.2.77", "192.0.2.78"],
            "prefix_length": ["24", "25"],
        }
    ]
    first_address = netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (multiple_ip_info, []),
    )
    assert first_address["interface"]["ipv4"] == "192.0.2.77/24"


def test_netmiko_real_status_commands_use_textfsm_and_disconnect(monkeypatch):
    calls = []

    class FakeConnection:
        def send_command(self, command, **kwargs):
            calls.append((command, kwargs))
            if command.startswith("show ip interface"):
                return []
            return []

        def disconnect(self):
            calls.append(("disconnect", {}))

    def fake_connect_handler(**kwargs):
        calls.append(("connect", kwargs))
        return FakeConnection()

    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")
    monkeypatch.setattr(
        "netmiko.ConnectHandler",
        fake_connect_handler,
    )
    assert netmiko_textfsm.get_interface_state(
        "10.0.29.107",
        "Loopback777",
    )["result"] == "not_found"

    assert calls[0] == (
        "connect",
        {
            "device_type": "cisco_ios",
            "host": "10.0.29.107",
            "username": "test-user",
            "password": "test-password",
            "timeout": 10,
        },
    )
    assert calls[1] == (
        "show ip interface Loopback777",
        {"use_textfsm": True},
    )
    assert calls[2] == (
        "show interfaces description",
        {"use_textfsm": True},
    )
    assert calls[-1][0] == "disconnect"


def test_netmiko_real_delete_issues_ios_no_interface_command(monkeypatch):
    calls = []

    class FakeConnection:
        def send_config_set(self, commands):
            calls.append(("send_config_set", commands))

        def disconnect(self):
            calls.append(("disconnect", None))

    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")
    monkeypatch.setattr(
        "netmiko.ConnectHandler",
        lambda **kwargs: calls.append(("connect", kwargs))
        or FakeConnection(),
    )

    netmiko_textfsm._real_delete("10.0.29.107", "Loopback777")
    assert calls[1] == ("send_config_set", ["no interface Loopback777"])
    assert calls[-1] == ("disconnect", None)


def test_netmiko_real_delete_maps_command_failures(monkeypatch):
    class FakeConnection:
        def send_config_set(self, commands):
            raise AuthenticationException("rejected")

        def disconnect(self):
            pass

    monkeypatch.setenv("ROUTER_USERNAME", "test-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "test-password")
    monkeypatch.setattr(
        "netmiko.ConnectHandler",
        lambda **kwargs: FakeConnection(),
    )
    monkeypatch.setattr(
        netmiko_textfsm,
        "get_interface_state",
        lambda router, name: {
            "status": "ok",
            "result": "found",
            "interface": {},
        },
    )

    assert netmiko_textfsm.delete_interface(
        "10.0.29.101",
        "Loopback777",
    ) == {"status": "error", "result": "authentication_failed"}



def test_netmiko_delete_checks_existing_state_and_verifies_removal(monkeypatch):
    statuses = iter(
        [
            {"status": "ok", "result": "found", "interface": {}},
            {"status": "ok", "result": "not_found", "interface": None},
        ]
    )
    deleted = []
    monkeypatch.setattr(
        netmiko_textfsm,
        "get_interface_state",
        lambda router, name: next(statuses),
    )
    monkeypatch.setattr(
        netmiko_textfsm,
        "_real_delete",
        lambda router, name: deleted.append((router, name)),
    )

    assert netmiko_textfsm.delete_interface(
        "10.0.29.101",
        "Loopback777",
    ) == {"status": "ok", "result": "deleted"}
    assert deleted == [("10.0.29.101", "Loopback777")]


def test_netmiko_delete_reports_failure_if_interface_remains(monkeypatch):
    monkeypatch.setattr(
        netmiko_textfsm,
        "get_interface_state",
        lambda router, name: {
            "status": "ok",
            "result": "found",
            "interface": {},
        },
    )
    monkeypatch.setattr(netmiko_textfsm, "_real_delete", lambda router, name: None)

    assert netmiko_textfsm.delete_interface(
        "10.0.29.101",
        "Loopback777",
    ) == {"status": "error", "result": "backend_failed"}

def test_netmiko_delete_of_absent_interface_does_not_run_command(monkeypatch):
    monkeypatch.setattr(
        netmiko_textfsm,
        "get_interface_state",
        lambda router, name: {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        },
    )
    monkeypatch.setattr(
        netmiko_textfsm,
        "_real_delete",
        lambda router, name: (_ for _ in ()).throw(AssertionError()),
    )

    assert netmiko_textfsm.delete_interface(
        "10.0.29.101",
        "Loopback777",
    ) == {
        "status": "ok",
        "result": "not_found",
        "interface": None,
    }


def test_netmiko_parsing_and_transport_exceptions_have_common_error_responses():
    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (_ for _ in ()).throw(RuntimeError("parser")),
    ) == {"status": "error", "result": "backend_failed"}

    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (_ for _ in ()).throw(
            requests.exceptions.Timeout()
        ),
    ) == {"status": "error", "result": "connection_failed"}

    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (_ for _ in ()).throw(PermissionError()),
    ) == {"status": "error", "result": "authentication_failed"}

    for failure, expected in (
        (NetmikoAuthenticationException("rejected"), "authentication_failed"),
        (NetmikoTimeoutException("timeout"), "connection_failed"),
        (NetmikoParsingException("parse"), "backend_failed"),
        (TextFSMError("parse"), "backend_failed"),
    ):
        assert netmiko_textfsm.get_interface_state(
            "10.0.29.101",
            "Loopback777",
            lambda router, name, failure=failure: (_raise(failure)),
        ) == {"status": "error", "result": expected}

    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (_ for _ in ()).throw(SSHException()),
    ) == {"status": "error", "result": "connection_failed"}

    assert netmiko_textfsm.get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda router, name: (_ for _ in ()).throw(
            AuthenticationException()
        ),
    ) == {"status": "error", "result": "authentication_failed"}


def test_generic_planning_wrapper_converts_not_found_and_propagates_errors():
    result = plan_from_status(
        "10.0.29.101",
        DESIRED,
        lambda router, name: {
            "status": "ok",
            "result": "not_found",
            "interface": None,
        },
    )
    assert result["operation"] == "create"

    assert plan_from_status(
        "10.0.29.101",
        DESIRED,
        lambda router, name: {
            "status": "error",
            "result": "connection_failed",
        },
    ) == {"status": "error", "result": "connection_failed"}


def _raise(error):
    raise error
