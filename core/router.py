"""Command registry and dispatch, with no terminal dependencies."""

from dataclasses import dataclass
import shlex
from core.performance import mark
from typing import Callable, Dict, Optional, Tuple

from core.permissions import PermissionLevel, PermissionManager


@dataclass(frozen=True)
class CommandResult:
    message: str = ""
    should_exit: bool = False
    should_clear: bool = False
    conversational: bool = False
    streamed: bool = False


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Callable[..., CommandResult]
    permission: PermissionLevel = PermissionLevel.SAFE
    argument_parser: Optional[Callable[[str], str]] = None
    confirmation: Optional[Callable[[str], str]] = None
    usage: str = ""
    category: str = "SYSTEM"


class CommandInputError(ValueError):
    """A safe, user-facing validation error."""


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
        if " ".join(command.name.split()) != command.name:
            raise ValueError("Use single spaces between command words.")
        if command.name in self._commands:
            raise ValueError("Command already registered: " + command.name)
        self._commands[command.name] = command

    def dispatch(
        self, text: str, *, confirmed: bool = False,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> CommandResult:
        if not text.strip():
            return CommandResult()
        if any(ord(char) < 32 and char != "\t" for char in text) or "\x7f" in text:
            raise CommandInputError("Control characters are not allowed.")
        try:
            tokens = shlex.split(text)
        except ValueError:
            raise CommandInputError("Invalid quoting. Close all quotes.") from None
        command = None
        argument = ""
        for candidate in sorted(self.commands, key=lambda c: len(c.name.split()), reverse=True):
            count = len(candidate.name.split())
            if [token.lower() for token in tokens[:count]] == candidate.name.split():
                if candidate.argument_parser is None and len(tokens) != count:
                    continue
                command = candidate
                argument = " ".join(tokens[count:])
                break
        if command is None:
            raise UnknownCommandError()
        if command.argument_parser is not None:
            argument = command.argument_parser(argument)
        if (command.permission is PermissionLevel.CONFIRM and not confirmed
                and confirm is not None):
            prompt = (command.confirmation(argument) if command.confirmation
                      else "Execute " + command.name + "?")
            confirmed = confirm(prompt + " [y/N] ")
            if not confirmed:
                return CommandResult("Cancelled.")
        if not self._permissions.is_allowed(command.permission, confirmed=confirmed):
            raise PermissionError("Permission denied for command: " + command.name)
        mark("action_execution")
        result = (command.handler(argument) if command.argument_parser is not None
                  else command.handler())
        if not isinstance(result, CommandResult):
            raise TypeError("Command handler must return CommandResult.")
        return result
