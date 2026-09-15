"""Bounded native process execution shared by macOS actions."""

import logging
import platform
import subprocess
from typing import List


class ActionError(RuntimeError):
    """Safe diagnostic that does not expose process output or private inputs."""


class MacOSRunner:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def require_macos(self) -> None:
        if platform.system() != "Darwin":
            raise ActionError("This action requires macOS.")

    def run(self, arguments: List[str], action: str, failure: str) -> None:
        self.require_macos()
        try:
            result = subprocess.run(arguments, shell=False, capture_output=True,
                                    timeout=15, check=False)
        except subprocess.TimeoutExpired:
            self.logger.warning("ACTION %s timeout", action)
            raise ActionError("Action timed out. Please try again.") from None
        except OSError:
            self.logger.warning("ACTION %s unavailable", action)
            raise ActionError("macOS command is unavailable or could not be started.") from None
        if result.returncode != 0:
            self.logger.warning("ACTION %s failed code=%s", action, result.returncode)
            raise ActionError(failure)
        self.logger.info("ACTION %s completed", action)
