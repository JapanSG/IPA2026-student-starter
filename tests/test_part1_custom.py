from pathlib import Path

import pytest
import yaml

from app.core import process_request_file, validate_request
from app.request_handler import handle_webex_request, validate_webex_envelope

FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "sample-tests"
    / "part1"
    / "fixtures"
)


def test_status_ignores_extra_fields():
    result = process_request_file(
        FIXTURE_DIR / "18-status-ignores-extra-fields[custom].yaml"
    )
    assert result == {"status": "ok", "result": "accepted"}


def test_method_action_compatibility_happens_before_action_specific_validation():
    result = process_request_file(
        FIXTURE_DIR / "19-method-action-precedence[custom].yaml"
    )
    assert result == {"status": "error", "result": "invalid_action"}


def test_admin_state_only_checked_for_plan_and_apply():
    plan_result = process_request_file(
        FIXTURE_DIR / "20-plan-invalid-admin-state[custom].yaml"
    )
    assert plan_result == {"status": "error", "result": "invalid_admin_state"}

    status_result = process_request_file(
        FIXTURE_DIR / "21-status-invalid-admin-state[custom].yaml"
    )
    assert status_result == {"status": "ok", "result": "accepted"}


def test_webex_envelope_requires_bot_mention_before_attachment():
    assert validate_webex_envelope(["OTHER"], "request.yaml", "BOT") == {
        "status": "error",
        "result": "missing_bot_mention",
    }
    assert validate_webex_envelope(["BOT"], None, "BOT") == {
        "status": "error",
        "result": "no_yaml",
    }


def test_handler_validates_attached_yaml():
    result = handle_webex_request(
        ["TEST-BOT"],
        FIXTURE_DIR / "05-invalid-yaml.yaml",
        "TEST-BOT",
    )
    assert result == {"status": "error", "result": "invalid_yaml"}


def make_valid_request():
    return {
        "version": 1,
        "router": "10.0.29.101",
        "method": "restconf",
        "action": "plan",
        "desired": {
            "interface": {
                "name": "Loopback123",
                "ipv4": "172.23.123.1/32",
                "description": "IPA2026-66070123",
                "admin_state": "up",
            }
        },
    }


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda request: request.pop("version"), "invalid_version"),
        (lambda request: request.update(version=True), "invalid_version"),
        (lambda request: request.pop("router"), "missing_router"),
        (lambda request: request.update(router="10.0.29.106"), "invalid_router"),
        (lambda request: request.pop("method"), "missing_method"),
        (lambda request: request.update(method="ssh"), "invalid_method"),
        (lambda request: request.pop("action"), "missing_action"),
        (lambda request: request.update(action="delete"), "invalid_action"),
        (lambda request: request.pop("desired"), "missing_desired"),
        (lambda request: request.update(desired={}), "missing_interface"),
        (
            lambda request: request["desired"].update(interface={}),
            "missing_interface_name",
        ),
        (
            lambda request: request["desired"]["interface"].pop("ipv4"),
            "missing_ipv4",
        ),
        (
            lambda request: request["desired"]["interface"].pop("description"),
            "missing_description",
        ),
        (
            lambda request: request["desired"]["interface"].pop("admin_state"),
            "missing_admin_state",
        ),
        (
            lambda request: request["desired"]["interface"].update(
                admin_state="sideways"
            ),
            "invalid_admin_state",
        ),
    ],
)
def test_part1_validation_errors(mutate, expected):
    request = make_valid_request()
    mutate(request)
    result = validate_request(yaml.safe_dump(request))
    assert result == {"status": "error", "result": expected}


@pytest.mark.parametrize(
    ("input_data", "expected"),
    [
        (
            {"version": 2},
            "invalid_version",
        ),
        (
            {"version": 1, "method": "restconf"},
            "missing_router",
        ),
        (
            {"version": 1, "router": "10.0.29.101", "action": "status"},
            "missing_method",
        ),
        (
            {
                "version": 1,
                "router": "10.0.29.101",
                "method": "restconf",
                "desired": {},
            },
            "missing_action",
        ),
        (
            {
                "version": 1,
                "router": "10.0.29.101",
                "method": "restconf",
                "action": "delete",
            },
            "invalid_action",
        ),
    ],
)
def test_request_envelope_validation_precedence(input_data, expected):
    assert validate_request(yaml.safe_dump(input_data)) == {
        "status": "error",
        "result": expected,
    }


@pytest.mark.parametrize(
    "yaml_text",
    [
        "version: [",
        "- not a mapping",
    ],
)
def test_invalid_yaml_and_non_mapping_root(yaml_text):
    assert validate_request(yaml_text) == {
        "status": "error",
        "result": "invalid_yaml",
    }


@pytest.mark.parametrize(
    ("method", "action"),
    [
        ("restconf", "status"),
        ("restconf", "plan"),
        ("restconf", "apply"),
        ("netconf", "status"),
        ("netconf", "plan"),
        ("netconf", "apply"),
        ("ansible", "apply"),
        ("netmiko-textfsm", "status"),
        ("netmiko-textfsm", "delete"),
    ],
)
def test_all_part1_supported_method_action_combinations(method, action):
    request = make_valid_request()
    request["method"] = method
    request["action"] = action
    if action in {"status", "delete"}:
        request["desired"]["interface"] = {"name": "Loopback123"}
    result = validate_request(yaml.safe_dump(request))
    assert result == {"status": "ok", "result": "accepted"}


@pytest.mark.parametrize("router", [
    "10.0.29.101",
    "10.0.29.102",
    "10.0.29.103",
    "10.0.29.104",
    "10.0.29.105",
])
def test_all_part1_allowed_routers(router):
    request = make_valid_request()
    request["router"] = router
    assert validate_request(yaml.safe_dump(request)) == {
        "status": "ok",
        "result": "accepted",
    }
