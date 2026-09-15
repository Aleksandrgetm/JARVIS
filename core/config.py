"""Configuration independent of the current working directory."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    name: str = "JARVIS"
    version: str = "0.2.0"
    prompt: str = "jarvis > "
    log_dir: Path = Path(__file__).resolve().parent.parent / "data" / "logs"
    screenshot_dir: Path = Path(__file__).resolve().parent.parent / "data" / "screenshots"
