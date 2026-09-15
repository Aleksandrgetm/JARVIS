"""Register macOS actions in the existing command registry."""

import logging

from actions.apps import open_app, validate_app
from actions.browser import normalize_url, open_url
from actions.filesystem import open_folder, resolve_folder
from actions.macos import MacOSRunner
from actions.system import set_muted, set_volume, system_info, take_screenshot, validate_volume
from core.config import Config
from core.permissions import PermissionLevel
from core.router import Command, Router


def register_macos_commands(router: Router, config: Config, logger: logging.Logger) -> None:
    runner = MacOSRunner(logger)
    confirm = PermissionLevel.CONFIRM
    commands = (
        Command("open app", "Open an application", lambda value: open_app(runner, value),
                argument_parser=validate_app, usage="open app <name>", category="MACOS"),
        Command("open url", "Open an HTTP(S) website", lambda value: open_url(runner, value),
                argument_parser=normalize_url, usage="open url <url>", category="MACOS"),
        Command("open folder", "Open a directory", lambda value: open_folder(runner, value),
                argument_parser=resolve_folder, usage="open folder <path>", category="MACOS"),
        Command("volume", "Set output volume", lambda value: set_volume(runner, value),
                confirm, validate_volume, lambda value: "Set volume to " + value + "%?",
                "volume <0-100>", "MACOS"),
        Command("mute", "Mute output", lambda: set_muted(runner, True), confirm,
                confirmation=lambda _: "Mute audio?", category="MACOS"),
        Command("unmute", "Unmute output", lambda: set_muted(runner, False), confirm,
                confirmation=lambda _: "Unmute audio?", category="MACOS"),
        Command("screenshot", "Capture the main display", lambda: take_screenshot(runner, config.screenshot_dir),
                confirm, confirmation=lambda _: "Take screenshot?", category="MACOS"),
        Command("system info", "Show basic system information", lambda: system_info(runner), category="MACOS"),
    )
    for command in commands:
        router.register(command)
