import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from core.logger import setup_logger


class LoggerTests(unittest.TestCase):
    def test_creates_utf8_log_without_duplicate_handlers(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "logs"
            logger = setup_logger(path)
            try:
                logger = setup_logger(path)
                logger.info("Проверка")
                try:
                    raise RuntimeError("test failure")
                except RuntimeError:
                    logger.exception("Handler failed")
                content = (path / "jarvis.log").read_text(encoding="utf-8")
                self.assertEqual(content.count("Проверка"), 1)
                self.assertIn("Traceback", content)
                self.assertIn("RuntimeError: test failure", content)
                self.assertEqual(len(logger.handlers), 1)
            finally:
                for handler in list(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()
