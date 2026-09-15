"""HTTP(S)-only URL validation and opening."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

from actions.macos import MacOSRunner
from core.router import CommandInputError, CommandResult


def normalize_url(value: str) -> str:
    if not value:
        raise CommandInputError("Usage: open url <url>")
    error = "Invalid URL. Use an http or https URL with a valid hostname."
    if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
        raise CommandInputError(error)
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if (parts.scheme.lower() not in ("http", "https") or not host
                or parts.username is not None or parts.password is not None
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = host.encode("idna").decode("ascii")
            labels = host.rstrip(".").split(".")
            if len(host) > 253 or any(not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                    for label in labels):
                raise ValueError()
    except (ValueError, UnicodeError):
        raise CommandInputError(error) from None
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, parts.fragment))


def open_url(runner: MacOSRunner, value: str) -> CommandResult:
    url = normalize_url(value)
    runner.run(["/usr/bin/open", url], "open_url", "Could not open the website.")
    return CommandResult("Opening website.")
