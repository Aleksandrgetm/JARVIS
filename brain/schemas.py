"""Intent data and the single allowlist shared by parsing and serialization."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional


# action -> (existing CLI name, required parameter, Python type)
ACTION_SPECS = {
    "open_app": ("open app", "name", str),
    "open_url": ("open url", "url", str),
    "open_folder": ("open folder", "path", str),
    "set_volume": ("volume", "value", int),
    "mute": ("mute", None, None), "unmute": ("unmute", None, None),
    "screenshot": ("screenshot", None, None), "system_info": ("system info", None, None),
    "help": ("help", None, None), "status": ("status", None, None),
    "version": ("version", None, None), "exit": ("exit", None, None),
}


@dataclass(frozen=True)
class Intent:
    type: str
    action: Optional[str]
    parameters: Mapping[str, Any]
    response: Optional[str]
    confidence: float


def object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


INTENT_SCHEMA = object_schema({
    "type": {"type": "string", "enum": ["action", "conversation"]},
    "action": {"type": ["string", "null"], "enum": list(ACTION_SPECS) + [None]},
    "parameters": {"anyOf": [object_schema({})] + [
        object_schema({parameter: {"type": "integer" if kind is int else "string"}})
        for _, parameter, kind in ACTION_SPECS.values() if parameter is not None
    ]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "response": {"type": ["string", "null"]},
})
