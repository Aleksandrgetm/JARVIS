"""Configuration independent of the current working directory."""

from dataclasses import dataclass
from pathlib import Path
import math
import os
from core.settings import load_settings, ConfigurationError


@dataclass(frozen=True)
class Config:
    name: str = "JARVIS"
    version: str = "1.1.0"
    prompt: str = "jarvis > "
    log_dir: Path = Path(__file__).resolve().parent.parent / "data" / "logs"
    screenshot_dir: Path = Path(__file__).resolve().parent.parent / "data" / "screenshots"
    voice_build_dir: Path = Path(__file__).resolve().parent.parent / "data" / "voice"
    voice_locale: str = "ru-RU"
    voice_allow_network: bool = False
    voice_listen_timeout: float = 15.0  # Maximum utterance AFTER speech begins.
    voice_confirmation_timeout: float = 5.0
    voice_start_speech_timeout: float = 7.0
    voice_end_silence: float = 0.7
    voice_min_speech_duration: float = 0.9
    voice_final_result_timeout: float = 2.5
    voice_retry_cooldown: float = 0.4
    voice_debug: bool = False
    tts_voice: str = "Milena"
    ai_provider: str = "disabled"
    ai_model: str = "gpt-4.1-mini"
    ai_timeout: float = 15.0
    user_title: str = "сэр"
    startup_greeting_delay: float = 3.0
    ollama_url: str = "http://localhost:11434"

    @classmethod
    def from_env(cls, config_path=None, **overrides):
        settings = load_settings(config_path)
        # Keys stay in the provider's environment, never in config repr/logs.
        provider = os.environ.get("JARVIS_AI_PROVIDER", settings.get("ai_provider", "disabled")).strip().lower()
        default_model, default_timeout = (("qwen3:8b", 30.0) if provider == "ollama"
                                          else ("gpt-4.1-mini", 15.0))
        model = os.environ.get("JARVIS_AI_MODEL", settings.get("ai_model", default_model)).strip()
        ollama_url = os.environ.get("JARVIS_OLLAMA_URL", settings.get("ollama_url", "http://localhost:11434")).strip()
        try:
            timeout = float(os.environ.get("JARVIS_AI_TIMEOUT", str(settings.get("ai_timeout", default_timeout))))
            if not math.isfinite(timeout) or not 1 <= timeout <= 60:
                raise ValueError()
        except ValueError:
            timeout = default_timeout
        try:
            end_silence = float(os.environ.get("JARVIS_VOICE_END_SILENCE", str(settings.get("voice_end_silence", 0.7))))
            if not math.isfinite(end_silence) or not 0.6 <= end_silence <= 3:
                raise ValueError()
        except ValueError:
            end_silence = 0.7
        title = os.environ.get("JARVIS_USER_TITLE", settings.get("user_title", "сэр")).strip()
        if not title or len(title) > 32 or not all(c.isalpha() or c in ' -' for c in title):
            raise ConfigurationError('Invalid JARVIS user_title.')
        try:
            greeting_delay = float(os.environ.get("JARVIS_STARTUP_GREETING_DELAY",
                                                 str(settings.get("startup_greeting_delay", 3))))
            if not math.isfinite(greeting_delay) or not 0 <= greeting_delay <= 30:
                raise ValueError()
        except ValueError:
            greeting_delay = 3.0
        voice = os.environ.get("JARVIS_TTS_VOICE", settings.get("tts_voice", "Milena")).strip()
        if not voice or len(voice) > 128 or any(ord(c) < 32 for c in voice):
            raise ConfigurationError('Invalid JARVIS TTS voice.')
        overrides.setdefault("user_title", title)
        overrides.setdefault("tts_voice", voice)
        overrides.setdefault("startup_greeting_delay", greeting_delay)
        overrides.setdefault("voice_end_silence", end_silence)
        return cls(ai_provider=provider, ai_model=model, ai_timeout=timeout, ollama_url=ollama_url, **overrides)
