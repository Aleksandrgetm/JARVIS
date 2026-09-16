"""Replaceable synchronous TTS; microphone is closed while speaking."""

from abc import ABC, abstractmethod
import platform
import subprocess
import time

from voice.errors import TextToSpeechError
from core.performance import mark


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak the supplied text or raise TextToSpeechError."""


class MacOSTextToSpeech(TextToSpeech):
    def __init__(self, voice: str = "Milena", stop_event=None) -> None:
        self.voice = voice
        self.stop_event = stop_event

    def speak(self, text: str) -> None:
        if not text.strip() or (self.stop_event is not None and self.stop_event.is_set()):
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
                    if self.stop_event is None:
                        process.communicate(input=text, timeout=30)
                    else:
                        deadline, first = time.monotonic() + 30, True
                        while True:
                            if self.stop_event.is_set():
                                process.terminate()
                                try:
                                    process.communicate(timeout=1)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.communicate()
                                return
                            if time.monotonic() >= deadline:
                                raise subprocess.TimeoutExpired('say', 30)
                            try:
                                process.communicate(input=text if first else None, timeout=0.25)
                                break
                            except subprocess.TimeoutExpired:
                                first = False
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
                returncode = process.returncode
        except (OSError, subprocess.TimeoutExpired):
            raise TextToSpeechError("Speech output is unavailable; text mode output remains active.") from None
        if returncode != 0:
            raise TextToSpeechError("Speech output failed. Check the configured macOS voice.")
