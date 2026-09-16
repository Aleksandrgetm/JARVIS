"""Per-turn timing only: no prompts, model text, audio or credentials."""
from contextvars import ContextVar
import math
import threading
import time

current_audit = ContextVar('jarvis_latency_audit', default=None)


class LatencyAudit:
    def __init__(self, enabled=False, writer=print):
        self.enabled, self.writer = enabled, writer
        self.started = time.monotonic()
        self.wall_start = time.time()
        self.events = {}
        self.lock = threading.Lock()

    def mark(self, name, timestamp=None, announce=True):
        with self.lock:
            if name in self.events:
                return
            value = self.wall_start + time.monotonic() - self.started if timestamp is None else timestamp
            self.events[name] = value
            if self.enabled and announce:
                self.writer(f'[PERF] {name} timestamp={value:.6f} (+{(value-self.wall_start)*1000:.1f}ms)')

    def note(self, text):
        if self.enabled:
            self.writer(text)

    def import_native(self, payload):
        if not isinstance(payload, dict):
            return
        for name in ('speech_start', 'speech_end_estimated', 'speech_end_detected', 'stt_native_final'):
            value = payload.get(name)
            if type(value) in (float, int) and math.isfinite(value) and self.wall_start - 1 <= value <= time.time() + 1:
                self.mark(name, value, announce=False)

    def delta(self, first, last):
        if first not in self.events or last not in self.events:
            return 'n/a'
        return f'{max(0, (self.events[last] - self.events[first]) * 1000):.1f} ms'

    def summary(self):
        if not self.enabled:
            return
        pairs = [
            ('STT final -> Ollama first token', 'stt_final', 'ollama_first_token'),
            ('Ollama first token -> first speech chunk', 'ollama_first_token', 'first_speech_chunk_ready'),
            ('first text token -> first speech chunk', 'first_text_token', 'first_speech_chunk_ready'),
            ('speech_end -> TTS process start', 'speech_end_estimated', 'first_tts_process_started'),
            ('speech_end -> action execution', 'speech_end_estimated', 'action_execution'),
            ('speech_end (estimated) -> end detected', 'speech_end_estimated', 'speech_end_detected'),
            ('speech_end detected -> stt_final', 'speech_end_detected', 'stt_final'),
            ('speech_end (estimated) -> stt_final', 'speech_end_estimated', 'stt_final'),
            ('stt_final -> routing', 'stt_final', 'command_normalized'),
            ('routing -> ollama_request', 'command_normalized', 'ollama_request_start'),
            ('ollama first token', 'ollama_request_start', 'ollama_first_token'),
            ('ollama generation', 'ollama_first_token', 'ollama_response_complete'),
            ('request -> first conversation text', 'ollama_request_start', 'ollama_first_response_text'),
            ('request -> first speakable sentence', 'ollama_request_start', 'first_speakable_sentence'),
            ('tts delay (sentence ready -> TTS call)', 'first_speakable_sentence', 'tts_start'),
            ('stt_final -> TTS call', 'stt_final', 'tts_start'),
            ('speech_end (estimated) -> TTS call', 'speech_end_estimated', 'tts_start'),
            ('total (listen start -> TTS call)', 'listen_start', 'tts_start'),
        ]
        self.writer('[PERF SUMMARY]\n' + '\n'.join(f'{label}: {self.delta(a,b)}' for label,a,b in pairs)
                    + '\nspeech_end -> first audible response: n/a (audio output onset is not measured)')


def mark(name):
    audit = current_audit.get()
    if audit is not None:
        audit.mark(name)


def note(text):
    audit = current_audit.get()
    if audit is not None:
        audit.note(text)
