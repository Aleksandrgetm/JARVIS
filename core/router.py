"""Command registry and dispatch, with no terminal dependencies."""

from dataclasses import dataclass
from typing import Callable, Dict, Tuple

from core.permissions import PermissionLevel, PermissionManager


@dataclass(frozen=True)
class CommandResult:
    message: str = ""
    should_exit: bool = False
    should_clear: bool = False


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Callable[[], CommandResult]
    permission: PermissionLevel = PermissionLevel.SAFE


class UnknownCommandError(LookupError):
    """No handler matches the entire input."""


class Router:
    def __init__(self, permissions: PermissionManager) -> None:
        self._permissions = permissions
        self._commands: Dict[str, Command] = {}

    @property
    def commands(self) -> Tuple[Command, ...]:
        return tuple(self._commands.values())

    def register(self, command: Command) -> None:
        if not command.name or command.name != command.name.strip().lower():
            raise ValueError("Command names must be nonempty and normalized.")
        if any(char.isspace() for char in command.name):
            raise ValueError("Command names cannot contain whitespace.")
        if command.name in self._commands:
            raise ValueError("Command already registered: " + command.name)
        self._commands[command.name] = command

    def dispatch(self, text: str, *, confirmed: bool = False) -> CommandResult:
        name = text.strip().lower()
        if not name:
            return CommandResult()
        command = self._commands.get(name)
        if command is None:
            raise UnknownCommandError(name)
        if not self._permissions.is_allowed(command.permission, confirmed=confirmed):
            raise PermissionError("Permission denied for command: " + name)
        result = command.handler()
        if not isinstance(result, CommandResult):
            raise TypeError("Command handler must return CommandResult.")
        return result
