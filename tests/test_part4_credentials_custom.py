from app.backends.credentials import router_credentials


def test_router_credentials_use_spec_names_custom(monkeypatch):
    monkeypatch.setenv("ROUTER_USER", "spec-user")
    monkeypatch.setenv("ROUTER_PASS", "spec-pass")
    monkeypatch.setenv("ROUTER_USERNAME", "legacy-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "legacy-pass")

    assert router_credentials() == ("spec-user", "spec-pass")


def test_router_credentials_keep_legacy_names_as_fallback_custom(monkeypatch):
    monkeypatch.delenv("ROUTER_USER", raising=False)
    monkeypatch.delenv("ROUTER_PASS", raising=False)
    monkeypatch.setenv("ROUTER_USERNAME", "legacy-user")
    monkeypatch.setenv("ROUTER_PASSWORD", "legacy-pass")

    assert router_credentials() == ("legacy-user", "legacy-pass")
