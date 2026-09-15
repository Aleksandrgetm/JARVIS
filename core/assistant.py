"""CLI lifecycle and error boundary around command handlers."""

import logging
import sys
from typing import Callable, Optional

from core.commands import register_system_commands
from core.config import Config
from core.permissions import PermissionManager
from core.router import Router, UnknownCommandError


def clear_terminal() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


class Assistant:
    def __init__(
        self, config: Config, logger: logging.Logger,
        router: Optional[Router] = None,
        reader: Callable[[str], str] = input,
        writer: Callable[[str], None] = print,
        clearer: Callable[[], None] = clear_terminal,
    ) -> None:
        self.config = config
        self.logger = logger
        self.router = router if router is not None else Router(PermissionManager())
        if router is None:
            register_system_commands(self.router, config)
        self._read = reader
        self._write = writer
        self._clear = clearer

    def run(self) -> None:
        self.logger.info("System online")
        self._write(self.config.name + "\nSystem online.\n")
        try:
            while True:
                try:
                    text = self._read(self.config.prompt)
                    if not text.strip():
                        continue
                    result = self.router.dispatch(text)
                    self.logger.info("Command completed: %r", text.strip().lower())
                    if result.should_clear:
                        self._clear()
                    if result.message:
                        self._write(result.message)
                    if result.should_exit:
                        break
                except UnknownCommandError:
                    self.logger.warning("Unknown command")
                    self._write("Unknown command. Type 'help' for available commands.")
                except PermissionError:
                    self.logger.warning("Command permission denied")
                    self._write("Permission denied.")
                except (KeyboardInterrupt, EOFError):
                    self._write("\nSystem offline.")
                    break
                except Exception:
                    self.logger.exception("Command processing failed")
                    self._write("Command failed. See data/logs/jarvis.log for details.")
        finally:
            self.logger.info("System offline")
