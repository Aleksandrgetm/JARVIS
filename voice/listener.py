"""Own the native capture process; each call closes the microphone before returning."""

import json
import subprocess
import sys
from typing import Any, Dict, Optional
from pathlib import Path

from core.config import Config
from voice.errors import SpeechError
from voice.native_bridge import build_native_bridge


class NativeMicrophoneListener:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._binary: Optional[Path] = None

    def prepare(self) -> Dict[str, Any]:
        self._binary = build_native_bridge(self.config.voice_build_dir)
        return self._invoke("--authorize", 30)

    def capture(self, timeout: float) -> Dict[str, Any]:
        return self._invoke("--listen", timeout)

    def _invoke(self, mode: str, timeout: float) -> Dict[str, Any]:
        if self._binary is None:
            raise SpeechError("Voice listener has not been prepared.")
        if not 1 <= timeout <= 30:
            raise SpeechError("Voice timeout must be between 1 and 30 seconds.")
        arguments = [str(self._binary), mode, self.config.voice_locale, str(timeout),
                     "network" if self.config.voice_allow_network else "local",
                     str(self.config.voice_start_speech_timeout), str(self.config.voice_end_silence),
                     str(self.config.voice_final_result_timeout),
                     "debug" if self.config.voice_debug else "quiet",
                     str(self.config.voice_min_speech_duration)]
        # The watchdog includes waiting, the full utterance and finalization.
        deadline = (self.config.voice_start_speech_timeout + timeout
                    + self.config.voice_final_result_timeout + 8) if mode == "--listen" else 38
        process = None
        try:
            # In debug mode native stderr is inherited: events appear LIVE, not after communicate().
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE,
                                       stderr=None if self.config.voice_debug else subprocess.PIPE,
                                       text=True, encoding="utf-8", shell=False, start_new_session=True)
            try:
                output, _ = process.communicate(timeout=deadline)
            except subprocess.TimeoutExpired:
                raise SpeechError("Speech recognition timed out.", recoverable=mode == "--listen") from None
            if process.returncode != 0:
                raise SpeechError("Apple Speech stopped unexpectedly. Check Microphone and Speech Recognition permissions for Terminal / the app running Python.")
            try:
                payload = json.loads(output)
                if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
                    raise ValueError()
            except (ValueError, TypeError):
                raise SpeechError("Apple Speech returned an invalid response.") from None
            return payload
        except OSError:
            raise SpeechError("Microphone/Apple Speech helper is unavailable.") from None
        finally:
            # Also runs on Ctrl+C; the helper must never outlive a listening call.
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    if self.config.voice_debug:
                        print("[VOICE] microphone stopped (helper terminated)", file=sys.stderr, flush=True)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
