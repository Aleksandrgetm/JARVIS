"""Permission policy; no OS permissions or actions are requested."""

from enum import Enum, auto


class PermissionLevel(Enum):
    SAFE = auto()
    CONFIRM = auto()
    DANGEROUS = auto()


class PermissionManager:
    def is_allowed(
        self, level: PermissionLevel, *, confirmed: bool = False
    ) -> bool:
        if level is PermissionLevel.SAFE:
            return True
        if level is PermissionLevel.CONFIRM:
            return confirmed
        # Dangerous and unrecognized levels fail closed at foundation stage.
        return False
