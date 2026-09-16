"""One bounded, serial TTS worker per conversational answer."""
from contextvars import copy_context
import queue
import threading


class SpeechQueue:
    def __init__(self, speak):
        self.speak = speak
        self.items = queue.Queue(maxsize=64)
        self.worker = None
        self.cancelled = threading.Event()
        self.failed = False

    def submit(self, text):
        if self.cancelled.is_set():
            return
        if self.worker is None:
            self.worker = threading.Thread(target=copy_context().run, args=(self._run,), name='jarvis-tts', daemon=True)
            self.worker.start()
        self.items.put_nowait(text)

    def _run(self):
        while True:
            text = self.items.get()
            try:
                if text is None:
                    return
                if not self.cancelled.is_set():
                    try:
                        self.speak(text)
                    except Exception:
                        self.failed = True
                        self.cancelled.set()
            finally:
                self.items.task_done()

    def finish(self, cancel=False):
        if self.worker is None:
            return
        if cancel:
            self.cancelled.set()
        # Joining ensures the microphone never reopens while a say call is active.
        self.items.put(None)
        try:
            self.worker.join()
        except KeyboardInterrupt:
            self.cancelled.set()
            self.worker.join()
            raise
        finally:
            self.worker = None
