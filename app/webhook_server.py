"""Webex webhook event handling and HTTP server."""

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.integration import process_request
from app.webex_client import WEBEX_API_BASE, WebexClient


LOGGER = logging.getLogger(__name__)
MAX_EVENT_BODY_LENGTH = 1_000_000


class WebexEventHandler:
    def __init__(self, client, bot_person_id, processor=None):
        self.client = client
        self.bot_person_id = bot_person_id
        self.processor = processor if processor is not None else process_request

    def handle_event(self, event):
        if not isinstance(event, dict):
            return None
        if event.get("resource") != "messages" or event.get("event") != "created":
            return None

        event_data = event.get("data")
        if not isinstance(event_data, dict):
            return None
        message_id = event_data.get("id")
        if not isinstance(message_id, str) or not message_id:
            return None
        if event_data.get("personId") == self.bot_person_id:
            return None

        message = self.client.get_message(message_id)
        if not isinstance(message, dict):
            return None
        if message.get("personId") == self.bot_person_id:
            return None

        room_id = message.get("roomId")
        files = message.get("files", [])
        if not isinstance(room_id, str) or not room_id or not isinstance(files, list):
            return None

        if len(files) > 1:
            result = {"status": "error", "result": "multiple_attachments"}
        elif not files:
            result = self.processor(
                message.get("mentionedPeople", []),
                None,
                self.bot_person_id,
            )
        else:
            file_url = files[0]
            if not isinstance(file_url, str) or not file_url:
                return None
            file_content = self.client.download_file(file_url)
            with tempfile.TemporaryDirectory(prefix="ipa2026-webhook-") as temp_dir:
                attachment_path = Path(temp_dir) / "request.yaml"
                attachment_path.write_bytes(file_content)
                result = self.processor(
                    message.get("mentionedPeople", []),
                    attachment_path,
                    self.bot_person_id,
                )

        self.client.send_message(room_id, json.dumps(result))
        return result


def make_http_handler(submit_event):
    class WebhookHTTPHandler(BaseHTTPRequestHandler):
        def _send_json(self, status_code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                self._send_json(404, {"status": "error", "result": "not_found"})
                return
            self._send_json(200, {"status": "ok", "result": "healthy"})

        def do_POST(self):
            if self.path != "/webhook":
                self._send_json(404, {"status": "error", "result": "not_found"})
                return

            raw_length = self.headers.get("Content-Length")
            try:
                body_length = int(raw_length)
            except (TypeError, ValueError):
                self._send_json(
                    400,
                    {"status": "error", "result": "invalid_event"},
                )
                return

            if not 1 <= body_length <= MAX_EVENT_BODY_LENGTH:
                remaining = body_length
                while remaining:
                    chunk = self.rfile.read(min(65_536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self._send_json(
                    400,
                    {"status": "error", "result": "invalid_event"},
                )
                return

            try:
                event = json.loads(self.rfile.read(body_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(
                    400,
                    {"status": "error", "result": "invalid_event"},
                )
                return

            if not isinstance(event, dict):
                self._send_json(
                    400,
                    {"status": "error", "result": "invalid_event"},
                )
                return

            submit_event(event)
            self._send_json(200, {"status": "ok", "result": "accepted"})

        def do_PUT(self):
            self._send_json(404, {"status": "error", "result": "not_found"})

        def do_DELETE(self):
            self._send_json(404, {"status": "error", "result": "not_found"})

        def log_message(self, format_string, *args):
            LOGGER.info("%s - %s", self.address_string(), format_string % args)

    return WebhookHTTPHandler


def _get_bot_person_id(client):
    response = client.session.get(
        f"{WEBEX_API_BASE}/people/me",
        headers=client.headers,
        timeout=10,
    )
    response.raise_for_status()
    identity = response.json()
    person_id = identity.get("id") if isinstance(identity, dict) else None
    if not isinstance(person_id, str) or not person_id:
        raise RuntimeError("Webex identity response did not contain a person ID")
    return person_id


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    client = WebexClient()
    bot_person_id = _get_bot_person_id(client)
    event_handler = WebexEventHandler(client, bot_person_id)
    executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="webex-event",
    )

    def submit_event(event):
        executor.submit(event_handler.handle_event, event)

    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), make_http_handler(submit_event))
    LOGGER.info("Starting webhook server on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping webhook server")
    finally:
        server.shutdown()
        server.server_close()
        executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
