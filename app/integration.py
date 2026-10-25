"""Part 4 student implementation."""

import logging

import yaml

from app.core import validate_request_data
from app.dispatcher import dispatch_request
from app.request_handler import validate_webex_envelope


LOGGER = logging.getLogger(__name__)


def process_request(mentioned_people, attachment_path, bot_person_id):
    envelope_error = validate_webex_envelope(
        mentioned_people,
        attachment_path,
        bot_person_id,
    )
    if envelope_error is not None:
        LOGGER.warning("Request rejected at Webex envelope validation: %s", envelope_error["result"])
        return envelope_error

    with open(attachment_path, encoding="utf-8") as attachment:
        try:
            request = yaml.safe_load(attachment)
        except (UnicodeDecodeError, yaml.YAMLError):
            LOGGER.warning("Request attachment could not be parsed as YAML")
            return {"status": "error", "result": "invalid_yaml"}

    validation_result = validate_request_data(request)
    if validation_result["status"] == "error":
        LOGGER.warning(
            "Request rejected by schema validation: %s",
            validation_result["result"],
        )
        return validation_result

    LOGGER.info(
        "Validated request method=%s action=%s router=%s",
        request["method"],
        request["action"],
        request["router"],
    )
    return dispatch_request(request)
