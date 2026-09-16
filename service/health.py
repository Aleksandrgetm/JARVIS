"""Local-only model availability check; never loads a model or sends prompts."""
from http.client import HTTPConnection
import json
import threading
from urllib.parse import urlsplit


def probe_ollama(config):
    connection = None
    try:
        url = urlsplit(config.ollama_url)
        if (url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1')
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
            return 'DISCONNECTED', 'UNAVAILABLE'
        connection = HTTPConnection(url.hostname, url.port or 80, timeout=2)
        connection.request('GET', '/api/tags')
        response = connection.getresponse()
        with response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                raise ValueError()
        data = json.loads(raw)
        names = {item['name'] for item in data['models']}
        model = config.ai_model if ':' in config.ai_model else config.ai_model + ':latest'
        return 'CONNECTED', 'READY' if model in names else 'UNAVAILABLE'
    except (OSError, ValueError, KeyError, TypeError):
        return 'DISCONNECTED', 'WAITING'
    finally:
        if connection is not None:
            connection.close()


class OllamaMonitor:
    BACKOFF = (1, 2, 5, 10, 30)

    def __init__(self, config, stop, update, probe=probe_ollama):
        self.config, self.stop, self.update, self.probe = config, stop, update, probe
        self.thread = None

    def run(self):
        attempt = 0
        while not self.stop.is_set():
            try:
                ollama, ai = self.probe(self.config)
            except Exception:
                ollama, ai = 'DISCONNECTED', 'WAITING'
            self.update(ollama=ollama, ai=ai)
            delay = 30 if ai == 'READY' else self.BACKOFF[min(attempt, len(self.BACKOFF)-1)]
            attempt = 0 if ai == 'READY' else attempt + 1
            if self.stop.wait(delay):
                break

    def start(self):
        self.thread = threading.Thread(target=self.run, name='jarvis-ollama-health', daemon=True)
        self.thread.start()

    def close(self):
        if self.thread is not None:
            self.thread.join(timeout=5)
