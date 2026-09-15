"""Foundation commands; no external integrations or system actions."""

from core.config import Config
from core.router import Command, CommandResult, Router


def register_system_commands(router: Router, config: Config) -> None:
    def help_command() -> CommandResult:
        lines = ["Available commands:"]
        categories = list(dict.fromkeys(c.category for c in router.commands))
        for category in categories:
            if len(categories) > 1:
                lines.append(category)
            lines.extend("  {:<22} {}".format(c.usage or c.name, c.description)
                         for c in router.commands if c.category == category)
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
