"""Launch an application by name, without passing application arguments."""

from actions.macos import MacOSRunner
from core.router import CommandInputError, CommandResult


def validate_app(name: str) -> str:
    if not name:
        raise CommandInputError("Usage: open app <name>")
    if name.startswith("-") or any(c in name for c in ("/", ":", "\\")):
        raise CommandInputError("Use an application name, not a path or options.")
    return name


def open_app(runner: MacOSRunner, name: str) -> CommandResult:
    name = validate_app(name)
    runner.run(["/usr/bin/open", "-a", name], "open_app",
               "Application not found or could not be opened.")
    return CommandResult("Opening " + name + ".")
