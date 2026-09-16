"""Public, privacy-safe errors from the voice adapters."""


class SpeechError(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class TextToSpeechError(RuntimeError):
    pass
