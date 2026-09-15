"""Foundation commands; no external integrations or system actions."""

from core.config import Config
from core.router import Command, CommandResult, Router


def register_system_commands(router: Router, config: Config) -> None:
    def help_command() -> CommandResult:
        lines = ["Available commands:"]
        lines.extend("  {:<8} {}".format(c.name, c.description) for c in router.commands)
        return CommandResult("\n".join(lines))

    for command in (
        Command("help", "Show available commands", help_command),
        Command("status", "Show assistant status",
                lambda: CommandResult(config.name + " is online.")),
        Command("version", "Show application version",
                lambda: CommandResult(config.name + " " + config.version)),
        Command("clear", "Clear the terminal",
                lambda: CommandResult(should_clear=True)),
        Command("exit", "Exit JARVIS",
                lambda: CommandResult("System offline.", should_exit=True)),
    ):
        router.register(command)
