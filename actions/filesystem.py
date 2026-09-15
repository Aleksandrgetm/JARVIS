"""Validated folder opening; no shell expansion."""

from pathlib import Path

from actions.macos import MacOSRunner
from core.router import CommandInputError, CommandResult


def resolve_folder(value: str) -> str:
    if not value:
        raise CommandInputError("Usage: open folder <path>")
    home = Path.home()
    aliases = {name.lower(): home / name for name in ("Desktop", "Downloads", "Documents")}
    aliases["projects"] = home / "Desktop" / "Projects"
    try:
        path = aliases.get(value.lower(), Path(value).expanduser()).resolve()
        if not path.is_dir():
            raise CommandInputError("Folder not found or is not a directory.")
    except (OSError, RuntimeError, ValueError):
        raise CommandInputError("Folder not found or is not a directory.") from None
    return str(path)


def open_folder(runner: MacOSRunner, value: str) -> CommandResult:
    path = resolve_folder(value)
    runner.run(["/usr/bin/open", path], "open_folder", "Could not open the folder.")
    return CommandResult("Opening folder.")
