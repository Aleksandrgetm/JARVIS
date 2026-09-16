"""Extract only conversation text from a JSON prefix; never produce an action.

Early speech is provisional. The final object is still validated by Brain before
any routing. Missing/reordered headers simply defer speech until final validation.
"""
import json
import re

from brain.intent_parser import InvalidIntent, safe_text, validate_intent
from core.performance import mark


class ConversationStream:
    def __init__(self, on_sentence):
        self.on_sentence = on_sentence
        self.raw = ''
        self.emitted = 0
        self.prefix = ''
        self.sent = False

    def feed(self, fragment):
        self.raw += fragment
        if len(self.raw) > 16000:
            raise InvalidIntent('Intent too large')
        text = self._conversation_prefix()
        if text:
            mark("ollama_first_response_text")
            mark("first_text_token")
            self._emit(text, final=False)

    def _conversation_prefix(self):
        decoder = json.JSONDecoder()
        raw = self.raw.lstrip()
        if not raw.startswith('{'):
            return None
        index, fields = 1, {}
        try:
            while True:
                while index < len(raw) and raw[index].isspace():
                    index += 1
                key, index = decoder.raw_decode(raw, index)
                if not isinstance(key, str) or key in fields:
                    return None
                while index < len(raw) and raw[index].isspace():
                    index += 1
                if raw[index] != ':':
                    return None
                index += 1
                while index < len(raw) and raw[index].isspace():
                    index += 1
                if key == 'response':
                    if set(fields) != {'type', 'action', 'parameters', 'confidence'}:
                        return None
                    # Full header must already describe a valid conversation.
                    validate_intent(dict(fields, response='placeholder'))
                    if fields['type'] != 'conversation' or raw[index] != '"':
                        return None
                    start = index + 1
                    end = start
                    while end < len(raw):
                        char = raw[end]
                        if char == '"':
                            break
                        if char == '\\':
                            size = 6 if end + 1 < len(raw) and raw[end+1] == 'u' else 2
                            if end + size > len(raw):
                                break
                            end += size
                        else:
                            end += 1
                    # json.loads decodes escapes; incomplete escapes never enter TTS.
                    text = json.loads('"' + raw[start:end] + '"')
                    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
                        return None  # Wait for a split surrogate pair.
                    return text
                fields[key], index = decoder.raw_decode(raw, index)
                while index < len(raw) and raw[index].isspace():
                    index += 1
                if raw[index] != ',':
                    return None
                index += 1
        except (ValueError, IndexError):
            return None

    def _emit(self, text, final):
        text = text.lstrip()
        if not text:
            return
        safe_text(text, limit=4000, multiline=True)
        if any(x in text.casefold() for x in ('<think', '</think', 'thinking...')):
            raise InvalidIntent('Inline reasoning')
        if not text.startswith(self.prefix.rstrip() if final else self.prefix):
            raise InvalidIntent('Non-monotonic conversation')
        self.prefix = text
        while self.emitted < len(text):
            remaining = text[self.emitted:]
            if not self.sent:
                # Punctuation or three whitespace-terminated words; a trailing partial
                # word is never used as a boundary. Subsequent chunks are longer.
                early = re.search(r'[,;:.!?](?:\s|$)', remaining)
                words = list(re.finditer(r'\S+\s+', remaining))
                if early and re.search(r'\w', remaining[:early.end()]):
                    count = early.end()
                elif len(words) >= 3:
                    count = words[2].end()
                elif final:
                    count = len(remaining)
                else:
                    return
            else:
                count = self._later_boundary(remaining, final)
                if count is None:
                    return
            chunk = remaining[:count].strip()
            self.emitted += count
            if chunk:
                mark('first_speech_chunk_ready')
                mark('first_speakable_sentence')  # Compatibility alias: now a chunk.
                self.on_sentence(chunk)
                self.sent = True

    @staticmethod
    def _later_boundary(remaining, final):
        match = re.search(r'[.!?](?:\s|$)|\n', remaining)
        if match and match.end() >= 12:
            count = match.end()
        elif len(remaining) >= 180:
            count = remaining.rfind(' ', 60, 180)
            if count < 0:
                return  # Never split a token/word for speech.
        elif final:
            count = len(remaining)
        else:
            return
        return count

    def finish(self, text):
        # Called only after validation of the complete conversation Intent.
        self._emit(text, final=True)
