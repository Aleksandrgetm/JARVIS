import json
import contextlib
import io
import logging
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

from brain.brain import create_brain
from core.config import Config
from core.logger import setup_logger
from core.settings import ConfigurationError
from service.daemon import BackgroundService, shutdown_signals
from service.greeting import greeting_decision, record_greeting, reserve_greeting, startup_greeting
from service.health import OllamaMonitor, probe_ollama
from service.instance import AlreadyRunning, InstanceLock, is_locked
from service.launch_agent import LaunchAgent, ServiceError, generate_plist
from service.paths import LABEL, ServicePaths
from service.state import read_json, write_json
from voice.text_to_speech import MacOSTextToSpeech


class PathsFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = ServicePaths(Path(self.temp.name))


class PersistentConfigTests(PathsFixture):
    @patch.dict(os.environ, {}, clear=True)
    def test_file_and_environment_overrides(self):
        write_json(self.paths.config, dict(ai_provider='ollama', ai_model='qwen3:8b',
                   ollama_url='http://127.0.0.1:11434', ai_timeout=30, user_title='сэр',
                   startup_greeting_delay=3))
        config = Config.from_env(config_path=self.paths.config)
        self.assertEqual((config.ai_provider, config.ai_model, config.ai_timeout), ('ollama', 'qwen3:8b', 30))
        self.assertEqual(config.user_title, 'сэр')
        self.assertEqual(config.startup_greeting_delay, 3)
        with patch.dict(os.environ, {'JARVIS_AI_PROVIDER': 'disabled', 'JARVIS_AI_TIMEOUT': '8',
                                    'JARVIS_VOICE_END_SILENCE': '1.8',
                                    'JARVIS_STARTUP_GREETING_DELAY': '0.5'}):
            config = Config.from_env(config_path=self.paths.config)
        self.assertEqual((config.ai_provider, config.ai_timeout, config.voice_end_silence), ('disabled', 8, 1.8))
        self.assertEqual(config.startup_greeting_delay, 0.5)

    def test_bad_config_is_sanitized(self):
        for data in [[], {'OPENAI_API_KEY': 'PRIVATE'}, {'ai_timeout': -1}, {'user_title': 'x\nPRIVATE'}]:
            write_json(self.paths.config, data)
            with self.assertRaises(ConfigurationError) as caught:
                Config.from_env(config_path=self.paths.config)
            self.assertNotIn('PRIVATE', str(caught.exception))

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_config_preserves_disabled_default(self):
        self.assertEqual(Config.from_env(config_path=self.paths.config).ai_provider, 'disabled')

    def test_title_is_available_to_brain(self):
        brain = create_brain(Config(user_title='сэр'), Mock())
        self.assertIn('сэр', brain.system_prompt)
        self.assertIn('sparingly', brain.system_prompt)


class GreetingAndLockTests(PathsFixture):
    def test_exact_default_and_time_variants(self):
        self.assertEqual(startup_greeting(), 'Здравствуйте, сэр. JARVIS готов к работе.')
        for hour, prefix in [(0, 'Здравствуйте'), (4, 'Здравствуйте'), (5, 'Доброе утро'),
                             (11, 'Доброе утро'), (12, 'Здравствуйте'), (17, 'Здравствуйте'),
                             (18, 'Добрый вечер'), (23, 'Добрый вечер')]:
            self.assertEqual(startup_greeting(hour=hour, time_aware=True), prefix + ', сэр. JARVIS готов к работе.')
            self.assertEqual(startup_greeting(hour=hour), 'Здравствуйте, сэр. JARVIS готов к работе.')

    def test_same_session_cooldown_skips_until_boundary(self):
        path = self.paths.runtime / 'greeting.json'
        self.assertTrue(reserve_greeting(path, now=1000, session='login-a'))
        self.assertFalse(reserve_greeting(path, now=1599, session='login-a'))
        self.assertTrue(reserve_greeting(path, now=1600, session='login-a'))
        self.assertFalse(reserve_greeting(path, now=1601, session='login-a'))
        self.assertTrue(reserve_greeting(path, now=10, session='login-a'))  # Clock moved backwards.

    def test_new_login_session_ignores_old_cooldown(self):
        path = self.paths.runtime / 'greeting.json'
        record_greeting(path, session='login-a', now=1000)
        decision = greeting_decision(path, session='login-b', now=1001)
        self.assertTrue(decision.should_play)
        self.assertFalse(decision.cooldown_active)
        self.assertEqual(decision.reason, 'new login session')

    def test_greeting_occurs_once_per_session_after_record(self):
        path = self.paths.runtime / 'greeting.json'
        self.assertTrue(greeting_decision(path, session='login-a', now=1000).should_play)
        record_greeting(path, session='login-a', now=1000)
        self.assertFalse(greeting_decision(path, session='login-a', now=1001).should_play)
        self.assertTrue(greeting_decision(path, session='login-b', now=1002).should_play)

    def test_lock_blocks_second_instance_and_releases_without_unlink(self):
        with InstanceLock(self.paths.lock):
            self.assertTrue(is_locked(self.paths.lock))
            with self.assertRaisesRegex(AlreadyRunning, 'JARVIS уже запущен'):
                with InstanceLock(self.paths.lock):
                    self.fail('second instance acquired microphone lock')
        self.assertTrue(self.paths.lock.exists())
        self.assertFalse(is_locked(self.paths.lock))
        with InstanceLock(self.paths.lock):
            pass  # PID text is not authoritative.

    def test_crash_releases_kernel_lock(self):
        code = ('import os; from pathlib import Path; from service.instance import InstanceLock; '
                'lock=InstanceLock(Path(__import__("sys").argv[1])); lock.__enter__(); os._exit(1)')
        subprocess.run([sys.executable, '-B', '-c', code, str(self.paths.lock)], check=False, timeout=5)
        with InstanceLock(self.paths.lock):
            pass


class HealthTests(unittest.TestCase):
    def test_retry_backoff_and_recovery(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False]*6 + [True]
        probe = Mock(side_effect=[('DISCONNECTED','WAITING')]*6 + [('CONNECTED','READY')])
        update = Mock()
        OllamaMonitor(Config(), stop, update, probe).run()
        self.assertEqual([c.args[0] for c in stop.wait.call_args_list], [1,2,5,10,30,30,30])
        update.assert_called_with(ollama='CONNECTED', ai='READY')

    def test_unexpected_probe_failure_retries(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        update = Mock()
        OllamaMonitor(Config(), stop, update, Mock(side_effect=RuntimeError('private'))).run()
        update.assert_called_once_with(ollama='DISCONNECTED', ai='WAITING')

    @patch('service.health.HTTPConnection')
    def test_probe_checks_model_without_loading_or_generation(self, factory):
        conn = factory.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"models":[{"name":"qwen3:8b"}]}'
        self.assertEqual(probe_ollama(Config(ai_model='qwen3:8b')), ('CONNECTED','READY'))
        conn.request.assert_called_once_with('GET', '/api/tags')
        conn.close.assert_called_once()
        self.assertEqual(probe_ollama(Config(ai_model='not-installed')), ('CONNECTED','UNAVAILABLE'))

    @patch('service.health.HTTPConnection')
    def test_probe_unavailable_and_no_remote_fallback(self, factory):
        factory.return_value.request.side_effect = ConnectionRefusedError()
        self.assertEqual(probe_ollama(Config()), ('DISCONNECTED','WAITING'))
        factory.reset_mock()
        self.assertEqual(probe_ollama(Config(ollama_url='https://example.com')), ('DISCONNECTED','UNAVAILABLE'))
        factory.assert_not_called()


class DaemonTests(PathsFixture):
    def test_startup_greeting_does_not_wait_for_ollama_or_initialize_microphone(self):
        stop, tts, factory, logger = threading.Event(), Mock(), Mock(), Mock()
        tts.speak.side_effect = lambda text: stop.set()
        with patch('voice.listener.NativeMicrophoneListener') as microphone:
            service = BackgroundService(Config(ai_provider='ollama', ai_model='qwen3:8b', startup_greeting_delay=0),
                                        self.paths, logger, stop=stop, tts=tts, monitor_factory=factory)
            service.run()
        microphone.assert_not_called()
        tts.speak.assert_called_once_with('Здравствуйте, сэр. JARVIS готов к работе.')
        factory.return_value.start.assert_called_once()
        factory.return_value.close.assert_called_once()
        self.assertEqual(service.state['stt'], 'DISABLED')
        self.assertEqual(read_json(self.paths.runtime / 'health.json')['status'], 'STOPPED')

    def test_ollama_unavailable_does_not_stop_service(self):
        stop, tts = threading.Event(), Mock()
        monitor = Mock()
        def create(config, event, update):
            monitor.start.side_effect = lambda: update(ollama='DISCONNECTED', ai='WAITING')
            return monitor
        service = BackgroundService(Config(ai_provider='ollama', startup_greeting_delay=0), self.paths, Mock(), stop=stop,
                                    tts=tts, monitor_factory=create)
        def greeted(text):
            self.assertEqual(service.state['ai'], 'WAITING')
            self.assertEqual(service.state['core'], 'READY')
            stop.set()
        tts.speak.side_effect = greeted
        service.run()
        tts.speak.assert_called_once()

    def test_tts_failure_does_not_crash_startup(self):
        stop, tts, logger = threading.Event(), Mock(), Mock()
        def fail(text):
            stop.set()
            raise RuntimeError('private')
        tts.speak.side_effect = fail
        service = BackgroundService(Config(startup_greeting_delay=0), self.paths, logger, stop=stop, tts=tts)
        with patch('service.daemon.current_login_session_id', return_value='login-a'):
            service.run()
        self.assertEqual(service.state['tts'], 'UNAVAILABLE')
        self.assertTrue(any('startup greeting failed' in str(c.args) for c in logger.warning.call_args_list))

    def test_manual_restart_same_session_does_not_spam_greeting(self):
        record_greeting(self.paths.runtime / 'greeting.json', session='login-a')
        stop, tts = threading.Event(), Mock()
        service = BackgroundService(Config(startup_greeting_delay=0), self.paths, Mock(), stop=stop, tts=tts)
        def finish(**fields):
            BackgroundService.update(service, **fields)
            if fields.get('tts'):
                stop.set()
        service.update = finish
        with patch('service.daemon.current_login_session_id', return_value='login-a'):
            service.run()
        tts.speak.assert_not_called()

    def test_crash_restart_same_session_does_not_spam_greeting(self):
        record_greeting(self.paths.runtime / 'greeting.json', session='login-a', now=1000)
        self.assertFalse(greeting_decision(self.paths.runtime / 'greeting.json', session='login-a', now=1001).should_play)

    def test_reboot_new_session_replays_greeting_despite_recent_cooldown(self):
        record_greeting(self.paths.runtime / 'greeting.json', session='login-a', now=1000)
        stop, tts = threading.Event(), Mock()
        tts.speak.side_effect = lambda text: stop.set()
        service = BackgroundService(Config(startup_greeting_delay=0), self.paths, Mock(), stop=stop, tts=tts)
        with patch('service.daemon.current_login_session_id', return_value='login-b'):
            service.run()
        tts.speak.assert_called_once_with('Здравствуйте, сэр. JARVIS готов к работе.')
        self.assertEqual(read_json(self.paths.runtime / 'greeting.json')['last_greeting_session'], 'login-b')

    def test_greeting_delay_does_not_block_core_initialization(self):
        stop, tts = threading.Event(), Mock()
        service = BackgroundService(Config(startup_greeting_delay=0.2), self.paths, Mock(), stop=stop, tts=tts)
        with patch.object(service, '_run_startup_greeting', side_effect=lambda: stop.wait(0.2)):
            thread = threading.Thread(target=service.run)
            thread.start()
            try:
                deadline = time.monotonic() + 2
                while service.state['core'] != 'READY' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(service.state['core'], 'READY')
                tts.speak.assert_not_called()
            finally:
                stop.set()
                thread.join(timeout=2)

    @patch('service.daemon.signal.signal')
    def test_sigterm_and_sigint_set_shutdown_and_restore_handlers(self, install):
        previous = object()
        install.return_value = previous
        stop = threading.Event()
        with shutdown_signals(stop):
            handlers = {c.args[0]: c.args[1] for c in install.call_args_list}
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.assertTrue(stop.is_set())
            stop.clear()
            handlers[signal.SIGINT](signal.SIGINT, None)
            self.assertTrue(stop.is_set())
        self.assertEqual(install.call_args_list[-1].args, (signal.SIGINT, previous))

    @patch('voice.text_to_speech.platform.system', return_value='Darwin')
    @patch('voice.text_to_speech.subprocess.Popen')
    def test_shutdown_terminates_active_greeting_process(self, popen, platform):
        stop = threading.Event()
        process = popen.return_value.__enter__.return_value
        def interrupt(**kwargs):
            stop.set()
            process.communicate.side_effect = None
            raise subprocess.TimeoutExpired('say', .25)
        process.communicate.side_effect = interrupt
        MacOSTextToSpeech(stop_event=stop).speak('Здравствуйте, сэр.')
        process.terminate.assert_called_once()

    def test_real_sigterm_shuts_down_idle_service_without_launchd(self):
        script = """
import logging, sys, threading
from pathlib import Path
from core.config import Config
from service.daemon import BackgroundService, shutdown_signals
from service.paths import ServicePaths
paths = ServicePaths(Path(sys.argv[1]))
class SilentTTS:
    def speak(self, text):
        (paths.runtime / 'test-ready').write_text('ready')
stop = threading.Event()
with shutdown_signals(stop):
    BackgroundService(Config(startup_greeting_delay=0), paths, logging.getLogger('test-daemon'), stop=stop, tts=SilentTTS()).run()
"""
        process = subprocess.Popen([sys.executable, '-B', '-c', script, str(self.paths.home)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not (self.paths.runtime / 'test-ready').exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    self.fail('Test service did not start')
                time.sleep(.02)
            process.send_signal(signal.SIGTERM)
            _, errors = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, errors.decode())
            self.assertEqual(read_json(self.paths.runtime / 'health.json')['status'], 'STOPPED')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    @patch('service.daemon.setup_logger', side_effect=PermissionError('Cannot create log directory'))
    def test_logger_setup_failure_has_stderr_traceback(self, setup):
        from service.daemon import run_daemon
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(run_daemon(self.paths), 1)
        self.assertIn('Traceback (most recent call last)', output.getvalue())
        self.assertIn('PermissionError', output.getvalue())
        self.assertIn('Cannot create log directory', output.getvalue())

    @patch('service.daemon.platform.system', return_value='Darwin')
    @patch('service.daemon.Config.from_env', side_effect=RuntimeError('private-test-token'))
    def test_startup_failure_reports_traceback_to_stderr_and_error_log(self, config, platform):
        from service.daemon import run_daemon
        output = io.StringIO()
        with patch.dict(os.environ, {'EXAMPLE_API_KEY':'private-test-token'}), contextlib.redirect_stderr(output):
            self.assertEqual(run_daemon(self.paths), 1)
        for text in (output.getvalue(), (self.paths.logs / 'jarvis-error.log').read_text()):
            self.assertIn('Traceback (most recent call last)', text)
            self.assertIn('RuntimeError', text)
            self.assertNotIn('private-test-token', text)
            self.assertIn('[REDACTED]', text)
        for handler in list(logging.getLogger('jarvis').handlers):
            handler.close()
            logging.getLogger('jarvis').removeHandler(handler)

    def test_daemon_argparse_accepts_absolute_main_with_minimal_environment(self):
        entry = Path(__file__).resolve().parent.parent / 'main.py'
        result = subprocess.run([sys.executable, '-B', str(entry), '--daemon', '--help'],
                                cwd=self.paths.home, env={'PATH':'/usr/bin:/bin'},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--daemon', result.stdout)

    def test_user_log_paths_and_rotation(self):
        self.assertEqual(self.paths.logs, self.paths.home / 'Library/Logs/JARVIS')
        logger = setup_logger(self.paths.logs, service=True)
        try:
            logger.info('startup')
            logger.warning('test warning')
            self.assertIn('startup', (self.paths.logs / 'jarvis.log').read_text())
            self.assertNotIn('startup', (self.paths.logs / 'jarvis-error.log').read_text())
            self.assertEqual(len(logger.handlers), 2)
            self.assertTrue(all(h.maxBytes == 1000000 and h.backupCount == 3 for h in logger.handlers))
        finally:
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)


class LaunchAgentTests(PathsFixture):
    def setUp(self):
        super().setUp()
        self.project = self.paths.home / 'project with spaces'
        self.project.mkdir()
        (self.project / 'main.py').write_text('')
        self.loaded = False
        self.runner = Mock(side_effect=self.launchctl)
        self.agent = LaunchAgent(self.paths, uid=501, runner=self.runner)

    def launchctl(self, args, **kwargs):
        self.assertEqual(args[0], '/bin/launchctl')
        self.assertFalse(kwargs['shell'])
        if args[1] == 'bootstrap':
            self.loaded = True
        if args[1] == 'bootout':
            self.loaded = False
        if args[1] == 'print':
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, stdout='state = running\n pid = 123\n')
        return subprocess.CompletedProcess(args, 0, stdout='')

    def test_plist_absolute_paths_and_venv_symlink_preserved(self):
        python = self.project / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        data = generate_plist(self.project, python)
        self.assertEqual(data['ProgramArguments'], [str(python), '-u', str(self.project.resolve() / 'main.py'), '--daemon'])
        self.assertEqual(data['WorkingDirectory'], str(self.project.resolve()))
        self.assertTrue(data['RunAtLoad'])
        self.assertEqual(data['ThrottleInterval'], 60)
        self.assertFalse(data['KeepAlive']['SuccessfulExit'])
        self.assertNotIn('OPENAI_API_KEY', str(data))
        self.assertNotIn('UserName', data)

    def test_launchd_stdout_stderr_are_absolute_user_log_paths(self):
        data = generate_plist(self.project, sys.executable, self.paths)
        for key, name in [('StandardOutPath', 'launchd-stdout.log'),
                          ('StandardErrorPath', 'launchd-stderr.log')]:
            self.assertEqual(data[key], str(self.paths.logs / name))
            self.assertTrue(Path(data[key]).is_absolute())
            self.assertNotIn('~', data[key])
        self.assertEqual(data['StandardInPath'], '/dev/null')

    def test_reinstall_updates_old_registered_null_logs_plist(self):
        self.agent.install(self.project, sys.executable)
        old = plistlib.loads(self.paths.plist.read_bytes())
        old.update(StandardOutPath='/dev/null', StandardErrorPath='/dev/null')
        self.paths.plist.write_bytes(plistlib.dumps(old))
        config_before = self.paths.config.read_bytes()
        self.runner.reset_mock()
        original = self.launchctl
        def check_bootstrap(args, **kwargs):
            if args[1] == 'bootstrap':
                self.assertTrue(self.paths.logs.is_dir())
                current = plistlib.loads(self.paths.plist.read_bytes())
                self.assertTrue(current['StandardErrorPath'].endswith('/launchd-stderr.log'))
            return original(args, **kwargs)
        self.runner.side_effect = check_bootstrap
        self.agent.install(self.project, sys.executable)
        verbs = [c.args[0][1] for c in self.runner.call_args_list]
        self.assertLess(verbs.index('bootout'), verbs.index('bootstrap'))
        self.assertEqual(self.paths.config.read_bytes(), config_before)
        self.runner.reset_mock()
        self.agent.install(self.project, sys.executable)
        self.assertFalse(any(c.args[0][1] in ('bootout','bootstrap') for c in self.runner.call_args_list))

    def test_logs_displays_all_four_with_empty_and_missing_distinguished(self):
        self.paths.logs.mkdir(parents=True)
        (self.paths.logs / 'jarvis.log').write_text('startup')
        (self.paths.logs / 'launchd-stdout.log').write_text('')
        (self.paths.logs / 'launchd-stderr.log').write_text('python: cannot open main.py')
        text = self.agent.logs()
        for name in ('jarvis.log','jarvis-error.log','launchd-stdout.log','launchd-stderr.log'):
            self.assertIn(name, text)
        self.assertIn('(empty file)', text)
        self.assertIn('(no log yet)', text)
        self.assertIn('cannot open main.py', text)

    def test_install_is_idempotent_and_preserves_config(self):
        self.agent.install(self.project, sys.executable)
        data = plistlib.loads(self.paths.plist.read_bytes())
        self.assertEqual(data['Label'], LABEL)
        self.assertEqual(self.paths.plist.stat().st_mode & 0o777, 0o600)
        write_json(self.paths.config, dict(ai_provider='disabled'))
        self.runner.reset_mock()
        before = self.paths.plist.read_bytes()
        self.agent.install(self.project, sys.executable)
        self.assertEqual(self.paths.plist.read_bytes(), before)
        self.assertEqual(read_json(self.paths.config), dict(ai_provider='disabled'))
        self.assertFalse(any(c.args[0][1] in ('bootstrap', 'bootout') for c in self.runner.call_args_list))

    def test_uninstall_removes_only_managed_plist(self):
        self.agent.install(self.project, sys.executable)
        unrelated = self.paths.plist.parent / 'other.plist'
        unrelated.write_text('keep')
        log = self.paths.logs / 'jarvis.log'
        log.write_text('keep')
        self.agent.uninstall()
        self.agent.uninstall()
        self.assertFalse(self.paths.plist.exists())
        self.assertTrue(self.paths.config.exists())
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(log.read_text(), 'keep')
        self.assertTrue((self.project / 'main.py').exists())

    def test_unmanaged_plist_is_never_overwritten_or_deleted(self):
        self.paths.plist.parent.mkdir(parents=True)
        self.paths.plist.write_bytes(plistlib.dumps({'Label': LABEL}))
        for operation in (lambda: self.agent.install(self.project, sys.executable), self.agent.uninstall):
            with self.assertRaises(ServiceError):
                operation()
        self.runner.assert_not_called()
        self.assertTrue(self.paths.plist.exists())

    def test_restart_bootout_then_bootstrap_no_force_kill(self):
        self.agent.install(self.project, sys.executable)
        self.runner.reset_mock()
        self.agent.restart()
        verbs = [c.args[0][1] for c in self.runner.call_args_list]
        self.assertLess(verbs.index('bootout'), verbs.index('bootstrap'))
        self.assertNotIn('-k', str(self.runner.call_args_list))

    def test_status_does_not_claim_old_health_is_current(self):
        self.loaded = True
        write_json(self.paths.runtime / 'health.json', dict(pid=999, status='RUNNING', core='READY'))
        self.assertIn('stale', self.agent.status())
        write_json(self.paths.runtime / 'health.json', dict(pid=123, status='RUNNING', core='READY', stt='DISABLED'))
        self.assertIn('STT: DISABLED', self.agent.status())

    def test_launchctl_error_is_not_misinterpreted_as_missing_service(self):
        self.runner.side_effect = None
        self.runner.return_value = subprocess.CompletedProcess([], 1, stdout='')
        with self.assertRaises(ServiceError):
            self.agent.stop()

    def test_root_is_rejected(self):
        with self.assertRaises(ServiceError):
            LaunchAgent(self.paths, uid=0, runner=self.runner)
