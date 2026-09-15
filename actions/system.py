"""Volume, screenshots and limited system information."""

from datetime import datetime
from pathlib import Path
import platform
import re
import socket

from actions.macos import ActionError, MacOSRunner
from core.router import CommandInputError, CommandResult


def validate_volume(value: str) -> str:
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 0 <= int(value) <= 100:
        raise CommandInputError("Invalid volume. Use a value between 0 and 100.")
    return str(int(value))


def set_volume(runner: MacOSRunner, value: str) -> CommandResult:
    value = validate_volume(value)
    runner.run(["/usr/bin/osascript", "-e", "set volume output volume " + value],
               "set_volume", "Could not change volume.")
    return CommandResult("Volume set to " + value + "%.")


def set_muted(runner: MacOSRunner, muted: bool) -> CommandResult:
    runner.run(["/usr/bin/osascript", "-e",
                "set volume output muted " + ("true" if muted else "false")],
               "mute" if muted else "unmute", "Could not change mute state.")
    return CommandResult("Muted." if muted else "Unmuted.")


def screenshot_path(directory: Path) -> Path:
    return directory / ("screenshot_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".png")


def take_screenshot(runner: MacOSRunner, directory: Path) -> CommandResult:
    runner.require_macos()
    reserved = False
    completed = False
    path = None
    try:
        directory = directory.resolve()
        path = screenshot_path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        # Reserve the name exclusively; repeated captures never overwrite a file.
        with path.open("xb"):
            reserved = True
        runner.run(["/usr/sbin/screencapture", "-x", "-m", "-t", "png", str(path)],
                   "screenshot_capture", "Screenshot failed. Check macOS Screen Recording permission.")
        if not path.is_file() or path.stat().st_size == 0:
            raise ActionError("Screenshot was not created. Check macOS Screen Recording permission.")
        completed = True
    except FileExistsError:
        raise ActionError("Screenshot name already exists. Try again in a second.") from None
    except OSError:
        raise ActionError("Could not save screenshot. Check directory access.") from None
    finally:
        if reserved and not completed:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
    runner.logger.info("ACTION screenshot saved file=%s", path.name)
    return CommandResult("Screenshot saved: " + str(path))


def system_info(runner: MacOSRunner) -> CommandResult:
    runner.require_macos()
    runner.logger.info("ACTION system_info completed")
    return CommandResult("\n".join((
        "macOS version: " + platform.mac_ver()[0],
        "Architecture: " + platform.machine(),
        "Hostname: " + socket.gethostname(),
        "Python version: " + platform.python_version(),
    )))
