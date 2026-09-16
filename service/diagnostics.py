"""Startup traceback reporting, including failures before file logging is ready."""
import os
import re
import sys
import traceback


def report_startup_failure(error, logger=None):
    # No locals/environment dump. Redact known credential values from exception text.
    detail = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    for name, value in os.environ.items():
        if value and re.search(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH', name, re.I):
            detail = detail.replace(value, '[REDACTED]')
    detail = re.sub(r'(?i)(Bearer\s+)\S+', r'\1[REDACTED]', detail)
    detail = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', detail)
    detail = 'JARVIS daemon startup/runtime failure:\n' + detail
    # launchd captures this even when the file logger could not be initialized.
    print(detail, file=sys.stderr, flush=True)
    if logger is not None:
        logger.error('%s', detail.rstrip())
