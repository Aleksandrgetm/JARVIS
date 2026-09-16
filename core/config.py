"""Configuration independent of the current working directory."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    name: str = "JARVIS"
    version: str = "1.0.0"
    prompt: str = "jarvis > "
    log_dir: Path = Path(__file__).resolve().parent.parent / "data" / "logs"
    screenshot_dir: Path = Path(__file__).resolve().parent.parent / "data" / "screenshots"
    voice_build_dir: Path = Path(__file__).resolve().parent.parent / "data" / "voice"
    voice_locale: str = "ru-RU"
    voice_allow_network: bool = False
    voice_listen_timeout: float = 15.0  # Maximum utterance AFTER speech begins.
    voice_confirmation_timeout: float = 5.0
    voice_start_speech_timeout: float = 7.0
    voice_end_silence: float = 1.8
    voice_min_speech_duration: float = 0.9
    voice_final_result_timeout: float = 2.5
    voice_retry_cooldown: float = 0.4
    voice_debug: bool = False
    tts_voice: str = "Milena"
