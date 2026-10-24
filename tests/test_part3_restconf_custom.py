import requests

from app.backends.restconf import (
    apply_interface,
    get_interface_state,
    plan_interface,
)


def response(status_code, payload=None):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return payload

    return FakeResponse()


def test_status_maps_authentication_and_unexpected_http_errors():
    for status_code, result in (
        (403, "authentication_failed"),
        (500, "backend_failed"),
    ):
        assert get_interface_state(
            "10.0.29.101",
            "Loopback777",
            lambda url: response(status_code),
        ) == {"status": "error", "result": result}


def test_status_normalizes_prefix_and_missing_description():
    raw_interface = {
        "name": "Loopback777",
        "enabled": False,
        "ietf-ip:ipv4": {
            "address": [
                {
                    "ip": "192.0.2.77",
                    "netmask": "255.255.255.0",
                }
            ]
        },
    }

    assert get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda url: response(
            200,
            {"ietf-interfaces:interface": raw_interface},
        ),
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


def test_status_maps_transport_and_malformed_response_errors():
    for failure, expected in (
        (requests.exceptions.Timeout(), "connection_failed"),
        (requests.exceptions.ConnectionError(), "connection_failed"),
        (PermissionError("permission denied"), "authentication_failed"),
        (RuntimeError("backend error"), "backend_failed"),
    ):
        def failing_get(url, failure=failure):
            raise failure

        assert get_interface_state(
            "10.0.29.101",
            "Loopback777",
            failing_get,
        ) == {"status": "error", "result": expected}

    assert get_interface_state(
        "10.0.29.101",
        "Loopback777",
        lambda url: response(200, {}),
    ) == {"status": "error", "result": "backend_failed"}


def test_plan_converts_not_found_to_none_and_propagates_errors():
    desired = {
        "name": "Loopback777",
        "ipv4": "192.0.2.77/32",
        "description": "custom",
        "admin_state": "up",
    }
    result = plan_interface(
        "10.0.29.101",
        desired,
        lambda url: response(404),
    )

    assert result == {
        "status": "ok",
        "result": "planned",
        "operation": "create",
        "changes": {
            "ipv4": {"to": "192.0.2.77/32"},
            "description": {"to": "custom"},
            "admin_state": {"to": "up"},
        },
    }

    assert plan_interface(
        "10.0.29.101",
        desired,
        lambda url: response(403),
    ) == {"status": "error", "result": "authentication_failed"}


def test_apply_maps_http_and_transport_errors():
    desired = {
        "name": "Loopback777",
        "ipv4": "192.0.2.77/24",
        "description": "custom",
        "admin_state": "down",
    }
    for status_code, expected in (
        (403, "authentication_failed"),
        (500, "backend_failed"),
    ):
        assert apply_interface(
            "10.0.29.101",
            desired,
            lambda url, json: response(status_code),
        ) == {"status": "error", "result": expected}

    def failing_put(url, json):
        raise requests.exceptions.Timeout()

    assert apply_interface(
        "10.0.29.101",
        desired,
        failing_put,
    ) == {"status": "error", "result": "connection_failed"}
