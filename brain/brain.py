"""Reasoning only: return a Router command or display text, never execute actions."""

from dataclasses import dataclass
import logging
import time
from typing import Optional

from brain.ai_client import AIClient
from brain.conversation_stream import ConversationStream
from brain.provider_factory import create_ai_client
from brain.intent_parser import intent_to_command, parse_intent, safe_text
from brain.prompts import SYSTEM_PROMPT
from brain.schemas import INTENT_SCHEMA

UNAVAILABLE = "AI-модуль сейчас недоступен."
CLARIFICATION = "Не уверен, что правильно понял команду. Что именно нужно сделать?"


@dataclass(frozen=True)
class BrainResult:
    command: Optional[str] = None
    response: Optional[str] = None
    streamed: bool = False


class Brain:
    def __init__(self, client: AIClient, logger: logging.Logger, debug: bool = False) -> None:
        self.client, self.logger = client, logger
        self.debug = debug

    def resolve(self, text: str, on_sentence=None) -> BrainResult:
        started = time.monotonic()
        self.logger.info("AI request started")
        try:
            text = safe_text(text, limit=8000, multiline=True)
            stream = (ConversationStream(on_sentence) if on_sentence is not None
                      and getattr(type(self.client), "supports_streaming", False) else None)
            arguments = dict(system_prompt=SYSTEM_PROMPT, user_text=text, schema=INTENT_SCHEMA)
            raw = (self.client.complete_stream(on_text=stream.feed, **arguments) if stream is not None
                   else self.client.complete(**arguments))
            intent = parse_intent(raw)
            self.logger.info("AI request completed type=%s action=%s confidence=%.2f latency_ms=%.0f",
                             intent.type, intent.action, intent.confidence,
                             (time.monotonic() - started) * 1000)
            if intent.type == "conversation":
                if stream is not None:
                    stream.finish(intent.response)
                return BrainResult(response=intent.response, streamed=bool(stream and stream.sent))
            if intent.confidence < 0.75:
                return BrainResult(response=CLARIFICATION)
            return BrainResult(command=intent_to_command(intent))
        except Exception:
            self.logger.warning("AI request failed latency_ms=%.0f", (time.monotonic() - started) * 1000)
            return BrainResult(response=UNAVAILABLE)

        finally:
            if self.debug:
                print(f"[PERF] brain={(time.monotonic() - started) * 1000:.1f}ms", flush=True)


def create_brain(config, logger: logging.Logger) -> Brain:
    return Brain(create_ai_client(config), logger, debug=config.voice_debug)
