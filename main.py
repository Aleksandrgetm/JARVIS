"""JARVIS entry point."""

from core.assistant import Assistant
from core.config import Config
from core.logger import setup_logger


def main() -> None:
    config = Config()
    logger = setup_logger(config.log_dir)
    Assistant(config=config, logger=logger).run()


if __name__ == "__main__":
    main()
