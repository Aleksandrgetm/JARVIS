"""Replaceable synchronous TTS; microphone is closed while speaking."""

from abc import ABC, abstractmethod
import platform
import subprocess

from voice.errors import TextToSpeechError
from core.performance import mark


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
            with subprocess.Popen(["/usr/bin/say", "-v", self.voice], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, shell=False) as process:
                mark('first_tts_process_started')
                try:
                    process.communicate(input=text, timeout=30)
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
                returncode = process.returncode
        except (OSError, subprocess.TimeoutExpired):
            raise TextToSpeechError("Speech output is unavailable; text mode output remains active.") from None
        if returncode != 0:
            raise TextToSpeechError("Speech output failed. Check the configured macOS voice.")
