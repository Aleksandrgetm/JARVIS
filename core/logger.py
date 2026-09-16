"""Rotating UTF-8 file logging."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(log_dir: Path, service: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Repeated initialization must not duplicate log entries or leak files.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        log_dir / "jarvis.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    logger.addHandler(handler)
    if service:
        errors = RotatingFileHandler(log_dir / "jarvis-error.log", maxBytes=1_000_000,
                                     backupCount=3, encoding="utf-8")
        errors.setLevel(logging.WARNING)
        errors.setFormatter(handler.formatter)
        logger.addHandler(errors)
    return logger
