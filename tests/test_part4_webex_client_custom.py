import pytest

from app.webex_client import WebexClient


class ResponseCustom:
    def raise_for_status(self):
        raise RuntimeError("HTTP failure")


class SessionCustom:
    def __init__(self):
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append(("GET", url, headers, timeout))
        return ResponseCustom()


def test_headers_use_bearer_token_and_missing_token_fails_custom(monkeypatch):
    monkeypatch.delenv("WEBEX_BOT_TOKEN", raising=False)
    with pytest.raises(PermissionError):
        WebexClient()

    client = WebexClient(token="test-token", session=SessionCustom())
    assert client.headers["Authorization"] == "Bearer test-token"


def test_api_calls_use_ten_second_timeout_and_raise_http_errors_custom():
    session = SessionCustom()
    client = WebexClient(token="test-token", session=session)

    with pytest.raises(RuntimeError, match="HTTP failure"):
        client.get_message("MSG1")

    method, _, headers, timeout = session.calls[0]
    assert method == "GET"
    assert headers["Authorization"] == "Bearer test-token"
    assert timeout == 10
