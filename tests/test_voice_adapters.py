import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from core.config import Config
from voice.errors import SpeechError, TextToSpeechError
from voice.listener import NativeMicrophoneListener
from voice.native_bridge import build_native_bridge
from voice.speech_to_text import AppleSpeechToText
from voice.text_to_speech import MacOSTextToSpeech


class SpeechAdapterTests(unittest.TestCase):
    def test_apple_1110_is_recoverable_no_speech(self):
        listener = Mock()
        listener.capture.return_value = {'status': 'no_speech',
            'error_domain': 'kAFAssistantErrorDomain', 'error_code': 1110}
        with self.assertRaises(SpeechError) as caught:
            AppleSpeechToText(listener).recognize(15)
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(str(caught.exception), 'No speech detected.')

    def test_success_and_language_independent_adapter(self):
        listener = Mock()
        listener.prepare.return_value = {'status': 'ok'}
        listener.capture.return_value = {'status': 'ok', 'text': '  статус  '}
        speech = AppleSpeechToText(listener)
        speech.prepare()
        self.assertEqual(speech.recognize(5), 'статус')
        listener.capture.assert_called_once_with(5)

    def test_speech_errors_have_safe_messages(self):
        for status in AppleSpeechToText.ERRORS:
            listener = Mock()
            listener.capture.return_value = {'status': status, 'text': 'secret'}
            with self.subTest(status=status), self.assertRaises(SpeechError) as caught:
                AppleSpeechToText(listener).recognize(5)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(caught.exception.recoverable, AppleSpeechToText.ERRORS[status][1])

    def test_empty_invalid_and_unknown_results(self):
        for payload in ({'status': 'ok'}, {'status': 'ok', 'text': ''},
                        {'status': 'ok', 'text': 42}, {'status': 'unknown'}):
            listener = Mock()
            listener.capture.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(SpeechError):
                AppleSpeechToText(listener).recognize(5)


class ListenerTests(unittest.TestCase):
    def make_process(self):
        process = Mock()
        process.returncode = 0
        process.poll.return_value = 0
        process.communicate.return_value = (json.dumps({'status': 'ok', 'text': 'статус'}), '')
        return process

    @patch('voice.listener.build_native_bridge', return_value=Path('/tmp/jarvis-speech-test'))
    @patch('voice.listener.subprocess.Popen')
    def test_no_microphone_on_construction_and_explicit_capture(self, popen, build):
        listener = NativeMicrophoneListener(Config())
        popen.assert_not_called()
        build.assert_not_called()
        popen.return_value = self.make_process()
        listener.prepare()
        listener.capture(5)
        self.assertEqual(popen.call_args.args[0], ['/tmp/jarvis-speech-test', '--listen', 'ru-RU', '5', 'local', '7.0', '1.8', '2.5', 'quiet', '0.9'])
        self.assertIs(popen.call_args.kwargs['shell'], False)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        popen.return_value.stdout.close.assert_called()

    @patch('voice.listener.subprocess.Popen')
    def test_network_requires_explicit_config(self, popen):
        listener = NativeMicrophoneListener(Config(voice_allow_network=True, voice_locale='en-US'))
        listener._binary = Path('/tmp/test')
        popen.return_value = self.make_process()
        listener.capture(5)
        self.assertEqual(popen.call_args.args[0][4], 'network')
        self.assertEqual(popen.call_args.args[0][2], 'en-US')

    @patch('voice.listener.subprocess.Popen')
    def test_timeout_and_ctrl_c_terminate_microphone_process(self, popen):
        for error in (subprocess.TimeoutExpired('test', 5), KeyboardInterrupt()):
            process = self.make_process()
            process.poll.return_value = None
            process.communicate.side_effect = [error, ('', '')]
            popen.return_value = process
            listener = NativeMicrophoneListener(Config())
            listener._binary = Path('/tmp/test')
            expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else SpeechError
            with self.subTest(error=type(error).__name__), self.assertRaises(expected):
                listener.capture(5)
            process.terminate.assert_called_once()
            process.stdout.close.assert_called_once()
            process.stderr.close.assert_called_once()

    @patch('voice.listener.subprocess.Popen')
    def test_stuck_process_is_killed(self, popen):
        process = self.make_process()
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired('test', 5),
                                          subprocess.TimeoutExpired('test', 2), ('', '')]
        popen.return_value = process
        listener = NativeMicrophoneListener(Config())
        listener._binary = Path('/tmp/test')
        with self.assertRaises(SpeechError):
            listener.capture(5)
        process.kill.assert_called_once()

    @patch('voice.listener.subprocess.Popen')
    def test_bad_protocol_crash_and_missing_executable(self, popen):
        listener = NativeMicrophoneListener(Config())
        listener._binary = Path('/tmp/test')
        for raw in ('not json', '[]', '{"status": 3}'):
            process = self.make_process()
            process.communicate.return_value = (raw, 'private stderr')
            popen.return_value = process
            with self.assertRaises(SpeechError):
                listener.capture(5)
        process.returncode = -6
        with self.assertRaisesRegex(SpeechError, 'permissions'):
            listener.capture(5)
        popen.side_effect = FileNotFoundError()
        with self.assertRaises(SpeechError):
            listener.capture(5)

    def test_unprepared_capture_cannot_open_microphone(self):
        with self.assertRaises(SpeechError):
            NativeMicrophoneListener(Config()).capture(5)

    @patch('voice.listener.subprocess.Popen')
    def test_watchdog_allows_start_plus_full_utterance_and_finalization(self, popen):
        process = self.make_process()
        popen.return_value = process
        listener = NativeMicrophoneListener(Config())
        listener._binary = Path('/tmp/test')
        listener.capture(15)
        process.communicate.assert_called_once_with(timeout=32.5)

    @patch('voice.listener.subprocess.Popen')
    def test_debug_is_live_stderr_and_does_not_enable_network(self, popen):
        popen.return_value = self.make_process()
        listener = NativeMicrophoneListener(Config(voice_debug=True))
        listener._binary = Path('/tmp/test')
        listener.capture(15)
        self.assertIsNone(popen.call_args.kwargs['stderr'])
        self.assertEqual(popen.call_args.args[0][-2], 'debug')
        self.assertEqual(popen.call_args.args[0][4], 'local')

    @patch('voice.listener.subprocess.Popen')
    def test_successful_attempt_after_watchdog_timeout(self, popen):
        failed = self.make_process()
        failed.poll.return_value = None
        failed.communicate.side_effect = [subprocess.TimeoutExpired('test', 33), ('', '')]
        success = self.make_process()
        popen.side_effect = [failed, success]
        listener = NativeMicrophoneListener(Config())
        listener._binary = Path('/tmp/test')
        with self.assertRaises(SpeechError):
            listener.capture(15)
        self.assertEqual(listener.capture(15)['text'], 'статус')
        failed.terminate.assert_called_once()
        self.assertEqual(popen.call_count, 2)


class TTSAdapterTests(unittest.TestCase):
    @patch('voice.text_to_speech.platform.system', return_value='Darwin')
    @patch('voice.text_to_speech.subprocess.run', return_value=Mock(returncode=0))
    def test_text_is_passed_via_stdin_not_shell_or_options(self, run, platform_mock):
        text = '-o /tmp/should-not-be-created; $(whoami)'
        MacOSTextToSpeech().speak(text)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/say', '-v', 'Milena'])
        self.assertEqual(run.call_args.kwargs['input'], text)
        self.assertIs(run.call_args.kwargs['shell'], False)

    @patch('voice.text_to_speech.platform.system', return_value='Darwin')
    @patch('voice.text_to_speech.subprocess.run')
    def test_empty_text_and_tts_errors(self, run, platform_mock):
        MacOSTextToSpeech().speak('')
        run.assert_not_called()
        run.return_value.returncode = 1
        with self.assertRaises(TextToSpeechError):
            MacOSTextToSpeech().speak('test')
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('say', 30)):
            run.side_effect = error
            with self.assertRaises(TextToSpeechError):
                MacOSTextToSpeech().speak('test')


class BuildTests(unittest.TestCase):
    @patch('voice.native_bridge.platform.system', return_value='Darwin')
    @patch('voice.native_bridge.subprocess.run')
    def test_build_is_cached_and_does_not_start_capture(self, run, platform_mock):
        def build(arguments, **kwargs):
            if arguments[0] == '/usr/bin/xcrun':
                Path(arguments[-1]).write_bytes(b'fake binary')
            return Mock(returncode=0)
        run.side_effect = build
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = build_native_bridge(directory)
            self.assertTrue(first.is_file())
            self.assertEqual(build_native_bridge(directory), first)
            self.assertEqual(run.call_count, 2)  # swiftc + codesign, once only
            for call in run.call_args_list:
                self.assertIs(call.kwargs['shell'], False)

    @patch('voice.native_bridge.platform.system', return_value='Darwin')
    @patch('voice.native_bridge.subprocess.run', return_value=Mock(returncode=1))
    def test_missing_compiler_is_a_friendly_error(self, run, platform_mock):
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(SpeechError, 'Command Line Tools'):
                build_native_bridge(Path(temporary))

    @patch('voice.native_bridge.platform.system', return_value='Linux')
    def test_non_macos_build_is_rejected(self, platform_mock):
        with self.assertRaisesRegex(SpeechError, 'requires macOS'):
            build_native_bridge(Path('/tmp/not-used'))
