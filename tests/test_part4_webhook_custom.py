import json
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.webhook_server import (
    MAX_EVENT_BODY_LENGTH,
    WebexEventHandler,
    make_http_handler,
)


class FakeClientCustom:
    def __init__(self, files=None, person_id="STUDENT"):
        self.message = {
            "roomId": "ROOM1",
            "personId": person_id,
            "mentionedPeople": ["BOT"],
            "files": files or [],
        }
        self.lookups = []
        self.downloads = []
        self.replies = []

    def get_message(self, message_id):
        self.lookups.append(message_id)
        return self.message

    def download_file(self, file_url):
        self.downloads.append(file_url)
        return b"request: custom\n"

    def send_message(self, room_id, text):
        self.replies.append((room_id, json.loads(text)))


def event_custom(**data):
    return {
        "resource": "messages",
        "event": "created",
        "data": {"id": "MSG1", **data},
    }


@contextmanager
def http_server_custom():
    submitted = []
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_http_handler(submitted.append),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", submitted
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_malformed_event_is_ignored_without_lookup_custom():
    client = FakeClientCustom()
    handler = WebexEventHandler(client, "BOT")

    assert handler.handle_event(
        {"resource": "messages", "event": "created", "data": []}
    ) is None
    assert handler.handle_event(
        {"resource": "messages", "event": "created", "data": {"id": ""}}
    ) is None
    assert client.lookups == []
    assert client.replies == []


def test_temporary_attachment_is_removed_when_processor_raises_custom():
    client = FakeClientCustom(["https://files.example/request.yaml"])
    paths = []

    def processor_custom(mentioned_people, path, bot_person_id):
        paths.append(path)
        raise RuntimeError("processor failed")

    handler = WebexEventHandler(client, "BOT", processor_custom)
    with pytest.raises(RuntimeError, match="processor failed"):
        handler.handle_event(event_custom())

    assert paths and not paths[0].exists()
    assert client.replies == []


def test_multiple_attachments_reply_without_processing_custom():
    client = FakeClientCustom(["url1", "url2"])
    called = []
    handler = WebexEventHandler(
        client,
        "BOT",
        lambda *args: called.append(args),
    )

    result = handler.handle_event(event_custom())

    assert result == {"status": "error", "result": "multiple_attachments"}
    assert called == []
    assert client.downloads == []
    assert client.replies == [
        ("ROOM1", {"status": "error", "result": "multiple_attachments"})
    ]


def test_webhook_rejects_non_object_and_unknown_path_custom():
    with http_server_custom() as (base, submitted):
        request = Request(
            base + "/webhook",
            data=b"[]",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 400
        assert json.load(error.value) == {
            "status": "error",
            "result": "invalid_event",
        }

        with pytest.raises(HTTPError) as error:
            urlopen(base + "/unknown", timeout=3)
        assert error.value.code == 404
        assert submitted == []


def test_webhook_rejects_empty_body_custom():
    with http_server_custom() as (base, submitted):
        request = Request(
            base + "/webhook",
            data=b"",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)

        assert error.value.code == 400
        assert json.load(error.value) == {
            "status": "error",
            "result": "invalid_event",
        }
        assert submitted == []


def test_webhook_accepts_maximum_body_length_custom():
    payload = b"{}" + b" " * (MAX_EVENT_BODY_LENGTH - 2)
    with http_server_custom() as (base, submitted):
        request = Request(
            base + "/webhook",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
            assert json.load(response) == {
                "status": "ok",
                "result": "accepted",
            }
        assert submitted == [{}]


def test_webhook_rejects_body_over_maximum_length_custom():
    payload = b"{}" + b" " * (MAX_EVENT_BODY_LENGTH - 1)
    with http_server_custom() as (base, submitted):
        request = Request(
            base + "/webhook",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 400
        assert json.load(error.value) == {
            "status": "error",
            "result": "invalid_event",
        }
        assert submitted == []
