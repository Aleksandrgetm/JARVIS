"""Replaceable STT API and Apple Speech result translation."""

from abc import ABC, abstractmethod

from voice.errors import SpeechError
from voice.listener import NativeMicrophoneListener


class SpeechToText(ABC):
    @abstractmethod
    def prepare(self) -> None:
        """Initialize the backend and request required OS permissions."""

    @abstractmethod
    def recognize(self, timeout: float) -> str:
        """Listen for one utterance; return only a final transcription."""


class AppleSpeechToText(SpeechToText):
    ERRORS = {
        "microphone_denied": ("Microphone access denied. Allow Microphone for Terminal / the app running Python in macOS System Settings → Privacy & Security.", False),
        "speech_denied": ("Speech Recognition access denied. Allow Speech Recognition for Terminal / the app running Python in macOS System Settings → Privacy & Security.", False),
        "microphone_unavailable": ("Microphone unavailable. Connect/select a microphone and check macOS audio input settings.", False),
        "recognizer_unavailable": ("Speech recognizer unavailable. Check the language, macOS speech resources and network if enabled.", False),
        "on_device_unavailable": ("On-device speech recognition is unavailable for this language. Enable/download macOS speech resources, or explicitly use --voice --voice-allow-network to allow Apple server processing.", False),
        "network_unavailable": ("Speech network unavailable. Check the connection or use local recognition.", True),
        "no_speech": ("No speech detected.", True),
        "audio_unavailable": ("Microphone started but no audio buffers arrived. Check macOS input device and microphone access.", False),
        "recognizer_error": ("Apple Speech recognition failed. Run --voice --debug to see error domain/code; check speech resources and locale.", False),
        "recognition_failed": ("Speech not recognized. Try again; check speech resources or network if this persists.", True),
    }

    def __init__(self, listener: NativeMicrophoneListener) -> None:
        self.listener = listener

    def _check(self, payload) -> None:
        if payload["status"] != "ok":
            message, recoverable = self.ERRORS.get(payload["status"], ("Speech recognizer returned an unsupported response.", False))
            raise SpeechError(message, recoverable=recoverable)

    def prepare(self) -> None:
        self._check(self.listener.prepare())

    def recognize(self, timeout: float) -> str:
        payload = self.listener.capture(timeout)
        self._check(payload)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SpeechError("Speech not recognized.", recoverable=True)
        return text.strip()
