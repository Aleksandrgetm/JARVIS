"""Validate untrusted JSON before producing a literal Router command."""

import json
import math
import re
import shlex
from types import MappingProxyType
import unicodedata

from actions.browser import normalize_url
from brain.schemas import ACTION_SPECS, Intent


class InvalidIntent(ValueError):
    pass


def safe_text(value, limit=1024, multiline=False):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidIntent("Invalid text parameter.")
    if any(unicodedata.category(c) in ("Cc", "Cs") and
           not (multiline and c in "\n\t") for c in value):
        raise InvalidIntent("Control characters are not allowed.")
    return value.strip()


def validate_intent(data) -> Intent:
    if not isinstance(data, dict) or set(data) != {"type", "action", "parameters", "response", "confidence"}:
        raise InvalidIntent("Invalid intent fields.")
    kind, action, parameters = data["type"], data["action"], data["parameters"]
    confidence = data["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise InvalidIntent("Invalid confidence.")
    if not isinstance(parameters, dict):
        raise InvalidIntent("Parameters must be an object.")
    parameters = dict(parameters)
    if kind == "conversation":
        if action is not None or parameters:
            raise InvalidIntent("Conversation cannot contain actions.")
        response = safe_text(data["response"], limit=4000, multiline=True)
    elif kind == "action":
        if not isinstance(action, str) or action not in ACTION_SPECS or data["response"] is not None:
            raise InvalidIntent("Action is not allowed.")
        _, parameter, expected_type = ACTION_SPECS[action]
        if set(parameters) != ({parameter} if parameter else set()):
            raise InvalidIntent("Unexpected or missing parameters.")
        if parameter:
            value = parameters[parameter]
            if type(value) is not expected_type:
                raise InvalidIntent("Incorrect parameter type.")
            if action == "set_volume":
                if not 0 <= value <= 100:
                    raise InvalidIntent("Volume must be 0–100.")
            else:
                value = safe_text(value)
                if action == "open_app" and (value.startswith("-") or
                        not re.fullmatch(r"[\w .+()&'’-]{1,128}", value)):
                    raise InvalidIntent("Invalid application name.")
                if action == "open_url":
                    try:
                        value = normalize_url(value)
                    except ValueError:
                        raise InvalidIntent("Invalid URL.") from None
                parameters[parameter] = value
        response = None
    else:
        raise InvalidIntent("Unknown intent type.")
    return Intent(kind, action, MappingProxyType(parameters), response, float(confidence))


def parse_intent(raw: str) -> Intent:
    if not isinstance(raw, str) or len(raw) > 16000:
        raise InvalidIntent("Invalid JSON response.")

    def reject_constant(_):
        raise InvalidIntent("Non-finite JSON number.")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidIntent("Duplicate JSON key.")
            result[key] = value
        return result

    try:
        return validate_intent(json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object))
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise InvalidIntent("Invalid structured intent.") from None


def intent_to_command(intent: Intent) -> str:
    # Revalidate even when a caller constructs Intent directly.
    intent = validate_intent({"type": intent.type, "action": intent.action,
                              "parameters": dict(intent.parameters), "response": intent.response,
                              "confidence": intent.confidence})
    if intent.type != "action":
        raise InvalidIntent("Conversation has no Router command.")
    command, parameter, _ = ACTION_SPECS[intent.action]
    return command + (" " + shlex.quote(str(intent.parameters[parameter])) if parameter else "")
