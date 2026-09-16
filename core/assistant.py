"""CLI lifecycle and error boundary around command handlers."""

import logging
import sys
from typing import Callable, Optional

from actions.macos import ActionError
from core.bootstrap import create_router
from core.config import Config
from core.router import CommandInputError, Router, UnknownCommandError


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
        self.router = router if router is not None else create_router(config, logger)
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
                    result = self.router.dispatch(text, confirm=self._confirm)
                    self.logger.info("Command processed")
                    if result.should_clear:
                        self._clear()
                    if result.message:
                        self._write(result.message)
                    if result.should_exit:
                        break
                except UnknownCommandError:
                    self.logger.warning("Unknown command")
                    self._write("Unknown command. Type 'help' for available commands.")
                except (CommandInputError, ActionError) as error:
                    self.logger.warning("Command rejected or action failed: %s", type(error).__name__)
                    self._write(str(error))
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

    def _confirm(self, prompt: str) -> bool:
        accepted = self._read(prompt).strip().lower() in ("y", "yes")
        self.logger.info("Action %s", "confirmed" if accepted else "cancelled")
        return accepted
