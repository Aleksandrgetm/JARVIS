"""Voice session orchestration over the same Router used by the CLI."""

import logging
import sys
import time
from typing import Callable

from actions.macos import ActionError
from core.assistant import clear_terminal
from core.config import Config
from core.input_processor import process_input
from core.performance import LatencyAudit, current_audit
from voice.speech_queue import SpeechQueue
from core.router import CommandInputError, Router, UnknownCommandError
from voice.errors import SpeechError
from voice.normalizer import VoiceCommandNormalizer
from voice.responses import confirmation_text, response_text
from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech


class VoiceAssistant:
    def __init__(self, config: Config, logger: logging.Logger, router: Router,
                 speech: SpeechToText, tts: TextToSpeech,
                 writer: Callable[[str], None] = print,
                 clearer: Callable[[], None] = clear_terminal,
                 pause: Callable[[float], None] = time.sleep, brain=None) -> None:
        self.config, self.logger, self.router = config, logger, router
        self.speech, self.tts = speech, tts
        self.normalizer = VoiceCommandNormalizer()
        self._write, self._clear, self._pause = writer, clearer, pause
        self.brain = brain
        self._audit = None

    def _say(self, text: str, spoken: str = "") -> None:
        self._write("JARVIS: " + text)
        sys.stdout.flush()
        if self._audit is not None:
            self._audit.mark('first_speech_chunk_ready')
            self._audit.mark('first_speakable_sentence')
            self._audit.mark('tts_start')
            self._audit.mark('total')
        try:
            self.tts.speak(spoken or text)
        except Exception:
            # Do not log text or exception payloads from speech providers.
            self.logger.warning("VOICE tts_failed")
            self._write("Speech output unavailable; text response shown above.")

    def _listen(self, timeout: float) -> str:
        self._write("Listening...")
        sys.stdout.flush()
        started = time.monotonic()
        try:
            text = self.speech.recognize(timeout)
        finally:
            if self.config.voice_debug:
                self._write(f"[PERF] stt={(time.monotonic() - started) * 1000:.1f}ms")
        if text.strip():
            self.logger.info("VOICE speech_recognized")
            self._write("You: " + text)
            sys.stdout.flush()
        return text

    def _confirm(self, prompt: str) -> bool:
        self._say(confirmation_text(prompt))
        try:
            answer = self._listen(self.config.voice_confirmation_timeout)
        except (KeyboardInterrupt, EOFError):
            raise
        except SpeechError as error:
            self.logger.warning("VOICE confirmation_failed")
            self._write(str(error))
            if not error.recoverable:
                raise
            self._pause(self.config.voice_retry_cooldown)
            return False
        except Exception:
            self.logger.warning("VOICE confirmation_failed")
            self._pause(self.config.voice_retry_cooldown)
            return False
        allowed = self.normalizer.is_confirmation(answer)
        self.logger.info("VOICE confirmation_%s", "accepted" if allowed else "cancelled")
        return allowed

    def run(self) -> None:
        self.logger.info("VOICE session_started")
        self._write(self.config.name + "\nVoice mode.\nSystem online.\n")
        try:
            mode = ("Apple Speech network mode enabled: audio may be sent to Apple."
                    if self.config.voice_allow_network else "Apple Speech local-only mode. Audio is not saved.")
            self._write(mode)
            self._write("Preparing speech recognition and checking macOS permissions...")
            self.speech.prepare()
            self._say("Система готова.")
            while True:
                self._audit = LatencyAudit(self.config.voice_debug, self._write)
                audit_token = current_audit.set(self._audit)
                speech_queue = SpeechQueue(self._say)
                try:
                    self._audit.mark('listen_start')
                    text = self._listen(self.config.voice_listen_timeout)
                    self._audit.mark("stt_final")
                    if not text.strip():
                        self.logger.info("VOICE command_unrecognized")
                        self._say("Команда не распознана.")
                        self._pause(self.config.voice_retry_cooldown)
                        continue
                    result = process_input(text, router=self.router, normalizer=self.normalizer,
                                           confirm=self._confirm, brain=self.brain, voice=True,
                                           on_sentence=speech_queue.submit)
                    speech_queue.finish(cancel=not result.streamed)
                    self.logger.info("VOICE response" if result.conversational else "VOICE action_processed")
                    if result.should_clear:
                        self._clear()
                    response = result.message if result.conversational else response_text(result.message)
                    # Long help/system output stays available in text, without a long monologue.
                    if not result.streamed:
                        self._say(response, "Команда выполнена. Подробности в терминале."
                                  if "\n" in response and not result.conversational else "")
                    if result.should_exit:
                        break
                except (KeyboardInterrupt, EOFError):
                    raise
                except SpeechError as error:
                    self.logger.warning("VOICE recognition_failed")
                    self._write(str(error))
                    if not error.recoverable:
                        break
                    self._pause(self.config.voice_retry_cooldown)
                except UnknownCommandError:
                    self._say("Команда не распознана.")
                except (CommandInputError, ActionError) as error:
                    self.logger.warning("VOICE action_failed")
                    self._say(str(error), "Не удалось выполнить команду. Подробности в терминале.")
                except PermissionError:
                    self._say("Действие не разрешено.")
                except Exception:
                    self.logger.warning("VOICE processing_failed")
                    self._say("Не удалось обработать команду.")
                    self._pause(self.config.voice_retry_cooldown)
                finally:
                    try:
                        speech_queue.finish(cancel=True)
                    finally:
                        self._audit.summary()
                        current_audit.reset(audit_token)
                        self._audit = None
        except SpeechError as error:
            self.logger.warning("VOICE startup_failed")
            self._write(str(error))
        except (KeyboardInterrupt, EOFError):
            pass
        except Exception:
            self.logger.warning("VOICE startup_failed")
            self._write("Voice mode unavailable. Check macOS speech and microphone settings.")
        finally:
            self.logger.info("VOICE session_stopped")
            self._write("JARVIS voice session stopped.")
