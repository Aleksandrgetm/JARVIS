"""Local Ollama HTTP adapter, without SDK, tools, retries or process execution."""

import json
import time
from http.client import HTTPConnection
from urllib.parse import urlsplit

from brain.ai_client import AIClient, AIUnavailable
from core.performance import mark, note


class OllamaClient(AIClient):
    supports_streaming = True
    MAX_RESPONSE_BYTES = 131072

    def __init__(self, model: str, timeout: float = 30.0,
                 base_url: str = 'http://localhost:11434', debug: bool = False) -> None:
        self.model, self.timeout, self.base_url = model, timeout, base_url
        self.debug = debug
        self._connection = None

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def complete(self, *, system_prompt, user_text, schema) -> str:
        return self._request(system_prompt=system_prompt, user_text=user_text, schema=schema)

    def complete_stream(self, *, system_prompt, user_text, schema, on_text) -> str:
        return self._request(system_prompt=system_prompt, user_text=user_text, schema=schema, on_text=on_text)

    def _request(self, *, system_prompt, user_text, schema, on_text=None) -> str:
        started = time.monotonic()
        try:
            url = urlsplit(self.base_url)
            # This provider is local-only; proxies and redirects cannot forward prompts.
            if (url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1')
                    or url.username is not None or url.password is not None
                    or url.path not in ('', '/') or url.query or url.fragment
                    or (url.port is not None and not 1 <= url.port <= 65535)
                    or not self.model):
                raise ValueError('Invalid local Ollama configuration')
            payload = {
                'model': self.model,
                'messages': [{'role': 'system', 'content': system_prompt},
                             {'role': 'user', 'content': user_text}],
                'format': schema, 'stream': on_text is not None, 'think': False, 'keep_alive': '10m',
                'options': {'temperature': 0, 'num_predict': 192},
            }
            if self._connection is None:
                self._connection = HTTPConnection(url.hostname, url.port or 80, timeout=self.timeout)
            mark('ollama_request_start')
            note('[AI] thinking=false')
            self._connection.request('POST', '/api/chat',
                                     body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
            response = self._connection.getresponse()
            try:
                if response.status != 200:
                    raise ValueError('Ollama HTTP error')
                if on_text is not None:
                    data, content = self._read_stream(response, on_text, started)
                else:
                    raw = response.read(self.MAX_RESPONSE_BYTES + 1)
                    if len(raw) > self.MAX_RESPONSE_BYTES:
                        raise ValueError('Response too large')
                    data = json.loads(raw)
                    content = self._content(data, final=True)
            finally:
                response.close()
            mark('ollama_response_complete')
            if self.debug:
                for name in ('load_duration', 'prompt_eval_duration', 'eval_duration', 'eval_count'):
                    value = data.get(name)
                    if type(value) in (int, float) and value >= 0:
                        print(f"[PERF] {name}={value / 1_000_000:.1f}ms" if name.endswith('duration')
                              else f"[PERF] {name}={value}", flush=True)
            # Only content is returned. Separate thinking/reasoning fields are ignored.
            # Reject legacy inline reasoning rather than recovering executable JSON from it.
            if any(marker in content.casefold() for marker in ('<think', '</think', 'thinking...')):
                raise ValueError('Inline reasoning is not a final response')
            return content
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        except Exception:
            self.close()
            raise AIUnavailable('Ollama unavailable.') from None
        finally:
            if self.debug:
                if on_text is None:
                    print('[PERF] ollama_first_token=n/a (non-streaming validated JSON)', flush=True)
                print(f'[PERF] ollama_total={(time.monotonic() - started) * 1000:.1f}ms', flush=True)

    @staticmethod
    def _content(data, final=False):
        if (not isinstance(data, dict) or data.get('error')
                or type(data.get('done')) is not bool
                or (final and data.get('done') is not True)
                or data.get('done_reason') == 'length'):
            raise ValueError('Incomplete response')
        message = data.get('message')
        if not isinstance(message, dict) or message.get('tool_calls'):
            raise ValueError('Invalid message')
        content = message.get('content', '')
        if not isinstance(content, str) or (final and not content.strip()):
            raise ValueError('Missing content')
        # message.thinking / reasoning are never returned, logged or spoken.
        return content

    def _read_stream(self, response, on_text, started):
        size, content = 0, ''
        while True:
            remaining = self.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError()
            if self._connection.sock is not None:
                self._connection.sock.settimeout(remaining)
            raw = response.readline(self.MAX_RESPONSE_BYTES + 1)
            size += len(raw)
            if not raw or size > self.MAX_RESPONSE_BYTES:
                raise ValueError('Truncated or oversized stream')
            data = json.loads(raw)
            fragment = self._content(data)
            if fragment:
                mark('ollama_first_token')
                content += fragment
                if len(content) > 16000 or any(marker in content.casefold()
                        for marker in ('<think', '</think', 'thinking...')):
                    raise ValueError('Invalid streamed content')
                on_text(fragment)
            if data['done']:
                if not content.strip():
                    raise ValueError('Empty stream')
                # Consume the HTTP terminator before reusing the connection.
                if response.read(1):
                    raise ValueError('Unexpected trailing stream data')
                if self._connection.sock is not None:
                    self._connection.sock.settimeout(self.timeout)
                return data, content
