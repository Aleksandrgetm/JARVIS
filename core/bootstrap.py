"""One command registry shared by text and voice interfaces."""

import logging

from actions.commands import register_macos_commands
from core.commands import register_system_commands
from core.config import Config
from core.permissions import PermissionManager
from core.router import Router


def create_router(config: Config, logger: logging.Logger) -> Router:
    router = Router(PermissionManager())
    register_system_commands(router, config)
    register_macos_commands(router, config, logger)
    return router
