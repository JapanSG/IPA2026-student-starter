"""Part 4 student implementation."""

import yaml

from app.core import validate_request_data
from app.dispatcher import dispatch_request
from app.request_handler import validate_webex_envelope


def process_request(mentioned_people, attachment_path, bot_person_id):
    envelope_error = validate_webex_envelope(
        mentioned_people,
        attachment_path,
        bot_person_id,
    )
    if envelope_error is not None:
        return envelope_error

    with open(attachment_path, encoding="utf-8") as attachment:
        try:
            request = yaml.safe_load(attachment)
        except (UnicodeDecodeError, yaml.YAMLError):
            return {"status": "error", "result": "invalid_yaml"}

    validation_result = validate_request_data(request)
    if validation_result["status"] == "error":
        return validation_result

    return dispatch_request(request)
