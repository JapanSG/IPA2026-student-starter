import logging

from app.backends import ansible
from app.dispatcher import dispatch_request


def test_dispatch_logs_backend_failure_without_request_payload(caplog, monkeypatch):
    request = {
        "version": 1,
        "router": "10.0.29.101",
        "method": "ansible",
        "action": "apply",
        "desired": {
            "interface": {
                "name": "Loopback123",
                "ipv4": "172.23.123.1/32",
                "description": "PRIVATE-DESCRIPTION",
                "admin_state": "up",
            }
        },
    }
    monkeypatch.setattr(
        ansible,
        "apply_interface",
        lambda router, interface: {
            "status": "error",
            "result": "backend_failed",
        },
    )

    with caplog.at_level(logging.ERROR):
        result = dispatch_request(request)

    assert result == {"status": "error", "result": "backend_failed"}
    assert "method=ansible action=apply" in caplog.text
    assert "result=backend_failed" in caplog.text
    assert "PRIVATE-DESCRIPTION" not in caplog.text
