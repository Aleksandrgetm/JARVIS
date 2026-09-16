"""Replaceable synchronous TTS; microphone is closed while speaking."""

from abc import ABC, abstractmethod
import platform
import subprocess

from voice.errors import TextToSpeechError


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak the supplied text or raise TextToSpeechError."""


class MacOSTextToSpeech(TextToSpeech):
    def __init__(self, voice: str = "Milena") -> None:
        self.voice = voice

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        if platform.system() != "Darwin":
            raise TextToSpeechError("macOS TTS is unavailable.")
        try:
            # stdin prevents text beginning with '-' from becoming an option.
            result = subprocess.run(["/usr/bin/say", "-v", self.voice], input=text,
                                    text=True, capture_output=True, shell=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise TextToSpeechError("Speech output is unavailable; text mode output remains active.") from None
        if result.returncode != 0:
            raise TextToSpeechError("Speech output failed. Check the configured macOS voice.")
