"""Configuration independent of the current working directory."""

from dataclasses import dataclass
from pathlib import Path
import math
import os


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
    ollama_url: str = "http://localhost:11434"

    @classmethod
    def from_env(cls, **overrides):
        # Keys stay in the provider's environment, never in config repr/logs.
        provider = os.environ.get("JARVIS_AI_PROVIDER", "disabled").strip().lower()
        default_model, default_timeout = (("qwen3:8b", 30.0) if provider == "ollama"
                                          else ("gpt-4.1-mini", 15.0))
        model = os.environ.get("JARVIS_AI_MODEL", default_model).strip()
        ollama_url = os.environ.get("JARVIS_OLLAMA_URL", "http://localhost:11434").strip()
        try:
            timeout = float(os.environ.get("JARVIS_AI_TIMEOUT", str(default_timeout)))
            if not math.isfinite(timeout) or not 1 <= timeout <= 60:
                raise ValueError()
        except ValueError:
            timeout = default_timeout
        try:
            end_silence = float(os.environ.get("JARVIS_VOICE_END_SILENCE", "0.7"))
            if not math.isfinite(end_silence) or not 0.6 <= end_silence <= 3:
                raise ValueError()
        except ValueError:
            end_silence = 0.7
        overrides.setdefault("voice_end_silence", end_silence)
        return cls(ai_provider=provider, ai_model=model, ai_timeout=timeout, ollama_url=ollama_url, **overrides)
