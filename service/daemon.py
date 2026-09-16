"""launchd owns this foreground process. Stage 5 does not listen to microphone."""
import contextlib
import logging
import os
import platform
import signal
import sys
import threading
import time
from pathlib import Path

from brain.brain import create_brain
from core.bootstrap import create_router
from core.config import Config
from core.logger import setup_logger
from service.diagnostics import report_startup_failure
from service.greeting import current_login_session_id, greeting_decision, record_greeting, startup_greeting
from service.health import OllamaMonitor
from service.instance import AlreadyRunning, InstanceLock
from service.paths import ServicePaths
from service.state import write_json
from voice.text_to_speech import MacOSTextToSpeech


@contextlib.contextmanager
def shutdown_signals(stop):
    previous = {}
    def request_shutdown(signum, frame):
        stop.set()
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, request_shutdown)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class BackgroundService:
    def __init__(self, config, paths, logger, stop=None, tts=None, monitor_factory=OllamaMonitor):
        self.config, self.paths, self.logger = config, paths, logger
        self.stop = stop if stop is not None else threading.Event()
        self.tts = tts if tts is not None else MacOSTextToSpeech(config.tts_voice, stop_event=self.stop)
        self.monitor_factory = monitor_factory
        self.state_lock = threading.Lock()
        self.state = dict(status='STARTING', pid=os.getpid(), version=config.version,
                          core='STARTING', tts='UNAVAILABLE', stt='DISABLED',
                          ai='WAITING' if config.ai_provider == 'ollama' else 'UNAVAILABLE',
                          ollama='DISCONNECTED', model=config.ai_model, provider=config.ai_provider)

    def update(self, **fields):
        with self.state_lock:
            changed = any(self.state.get(k) != v for k, v in fields.items())
            self.state.update(fields)
            self.state['updated_at'] = time.time()
            try:
                write_json(self.paths.runtime / 'health.json', self.state)
            except OSError:
                self.logger.exception('Cannot persist service health state')
            if changed and ('ai' in fields or 'ollama' in fields):
                self.logger.info('AI readiness provider=%s ai=%s Ollama=%s', self.config.ai_provider,
                                 self.state['ai'], self.state['ollama'])

    def _start_greeting_worker(self):
        thread = threading.Thread(target=self._run_startup_greeting, name='jarvis-startup-greeting', daemon=True)
        thread.start()
        return thread

    def _run_startup_greeting(self):
        delay = max(0.0, float(self.config.startup_greeting_delay))
        if delay and self.stop.wait(delay):
            return
        if self.stop.is_set():
            return
        state_path = self.paths.runtime / 'greeting.json'
        session = current_login_session_id()
        decision = greeting_decision(state_path, session=session)
        self.logger.info('startup greeting decision: session=%s previous_session=%s cooldown_active=%s reason=%s',
                         decision.session, decision.previous_session, decision.cooldown_active, decision.reason)
        if not decision.should_play:
            self.logger.info('startup greeting skipped: cooldown active in same login session')
            self.update(tts='READY' if Path('/usr/bin/say').is_file() else 'UNAVAILABLE')
            return
        try:
            self.logger.info('startup greeting started')
            self.tts.speak(startup_greeting(self.config.user_title))
            record_greeting(state_path, session=decision.session)
            self.logger.info('startup greeting completed')
            self.update(tts='READY' if Path('/usr/bin/say').is_file() else 'UNAVAILABLE')
        except Exception as error:
            self.logger.warning('startup greeting failed: %s', error)
            self.update(tts='UNAVAILABLE')

    def run(self):
        monitor, brain, greeting_thread = None, None, None
        try:
            self.logger.info('startup PID=%s version=%s AI provider=%s launchd mode; active listening disabled',
                             os.getpid(), self.config.version, self.config.ai_provider)
            create_router(self.config, self.logger)
            brain = create_brain(self.config, self.logger)
            self.update(core='READY', status='RUNNING')
            self.logger.info('voice initialization: TTS only; STT DISABLED, permissions not requested. '
                             'Manual --voice requires Microphone and Speech Recognition permissions.')
            if self.config.ai_provider == 'ollama':
                monitor = self.monitor_factory(self.config, self.stop, self.update)
                monitor.start()  # Neither health nor model readiness blocks local TTS.
            greeting_thread = self._start_greeting_worker()
            while not self.stop.wait(30):
                self.update()  # Bounded heartbeat; no audio, generation or actions.
        finally:
            self.stop.set()
            if greeting_thread is not None and greeting_thread.is_alive():
                greeting_thread.join(timeout=5)
            if monitor is not None:
                monitor.close()
            if brain is not None:
                brain.client.close()
            self.update(status='STOPPED', core='STOPPED')
            self.logger.info('shutdown PID=%s; audio resources closed', os.getpid())


def run_daemon(paths=None):
    logger = None
    try:
        paths = paths or ServicePaths()
        logger = setup_logger(paths.logs, service=True)
        logger.info('daemon entry PID=%s executable=%s cwd=%s home=%s config=%s runtime=%s',
                    os.getpid(), sys.executable, Path.cwd(), paths.home, paths.config, paths.runtime)
        if platform.system() != 'Darwin':
            logger.error('Background service requires macOS')
            return 1
        config = Config.from_env(config_path=paths.config)
        with InstanceLock(paths.lock):
            service = BackgroundService(config, paths, logger)
            with shutdown_signals(service.stop):
                service.run()
        return 0
    except AlreadyRunning:
        logger.warning('JARVIS уже запущен.')
        return 0  # No failure restart loop while manual voice owns the lock.
    except Exception as error:
        report_startup_failure(error, logger)
        return 1
