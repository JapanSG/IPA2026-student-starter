import yaml

from app import integration


def test_process_request_loads_yaml_once_custom(monkeypatch, tmp_path):
    request = {
        "version": 1,
        "router": "10.0.29.101",
        "method": "ansible",
        "action": "apply",
        "desired": {
            "interface": {
                "name": "Loopback123",
                "ipv4": "172.23.123.1/32",
                "description": "IPA2026-66070123",
                "admin_state": "up",
            }
        },
    }
    attachment = tmp_path / "request.yaml"
    attachment.write_text(yaml.safe_dump(request), encoding="utf-8")

    original_safe_load = yaml.safe_load
    loads = []

    def safe_load_custom(stream):
        loads.append(stream)
        return original_safe_load(stream)

    dispatched = []
    monkeypatch.setattr(yaml, "safe_load", safe_load_custom)
    monkeypatch.setattr(
        integration,
        "dispatch_request",
        lambda data: dispatched.append(data) or {
            "status": "ok",
            "result": "applied",
        },
    )

    result = integration.process_request(["BOT"], attachment, "BOT")

    assert result == {"status": "ok", "result": "applied"}
    assert len(loads) == 1
    assert dispatched == [request]


def test_process_request_rejects_invalid_utf8_custom(monkeypatch, tmp_path):
    attachment = tmp_path / "request.yaml"
    attachment.write_bytes(b"\xff")
    monkeypatch.setattr(
        integration,
        "dispatch_request",
        lambda data: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    assert integration.process_request(
        ["BOT"], attachment, "BOT"
    ) == {"status": "error", "result": "invalid_yaml"}
