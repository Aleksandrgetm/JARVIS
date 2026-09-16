import unittest
from unittest.mock import Mock, patch

from core.bootstrap import create_router
from core.config import Config
from core.permissions import PermissionLevel
from core.router import Command, CommandResult
from voice.errors import SpeechError
from voice.voice_assistant import VoiceAssistant


class VoiceSessionTests(unittest.TestCase):
    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_native_fallback_text_reaches_existing_router_and_action(self, process, platform_mock):
        from voice.speech_to_text import AppleSpeechToText
        listener = Mock()
        listener.prepare.return_value = {'status': 'ok'}
        listener.capture.side_effect = [
            {'status': 'ok', 'text': 'Джарвис открой Safari', 'source': 'fallback'},
            {'status': 'ok', 'text': 'выход', 'source': 'final'}]
        session = self.make_session([])
        session.speech = AppleSpeechToText(listener)
        session.run()
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0], ['/usr/bin/open', '-a', 'Safari'])
        self.assertIn('You: Джарвис открой Safari', self.output)
        self.assertIn('JARVIS: Открываю Safari.', self.output)
        self.assertLess(self.output.index('You: Джарвис открой Safari'),
                        self.output.index('JARVIS: Открываю Safari.'))

    def test_apple_1110_cleans_attempt_and_retries_in_same_session(self):
        from voice.speech_to_text import AppleSpeechToText
        listener = Mock()
        listener.prepare.return_value = {'status': 'ok'}
        listener.capture.side_effect = [
            {'status': 'no_speech', 'error_domain': 'kAFAssistantErrorDomain', 'error_code': 1110},
            {'status': 'ok', 'text': 'версия'}, {'status': 'ok', 'text': 'выход'}]
        session = self.make_session([])
        session.speech = AppleSpeechToText(listener)
        session.run()
        self.assertEqual(listener.capture.call_count, 3)
        session._pause.assert_called_once_with(0.4)
        self.assertIn('No speech detected.', self.output)
        self.assertIn('JARVIS: JARVIS 1.0.0', self.output)
        self.assertEqual(self.output.count('JARVIS voice session stopped.'), 1)

    def make_session(self, phrases, **kwargs):
        self.config = Config()
        self.logger = Mock()
        self.speech = Mock()
        self.speech.recognize.side_effect = phrases
        self.tts = Mock()
        self.output = []
        self.router = create_router(self.config, self.logger)
        return VoiceAssistant(self.config, self.logger, self.router, self.speech, self.tts,
                              writer=self.output.append, pause=Mock(), **kwargs)

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_voice_uses_existing_app_action(self, process, platform_mock):
        session = self.make_session(['Джарвис открой Safari', 'выход'])
        session.run()
        self.assertIs(session.router, self.router)
        process.assert_called_once()
        self.assertEqual(process.call_args.args[0], ['/usr/bin/open', '-a', 'Safari'])
        self.assertIn('JARVIS: Открываю Safari.', self.output)
        self.assertIn('You: Джарвис открой Safari', self.output)
        self.assertNotIn('Safari', str(self.logger.mock_calls))

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_voice_confirmation_executes_only_after_separate_yes(self, process, platform_mock):
        session = self.make_session(['поставь громкость 30', 'да', 'выход'])
        session.run()
        self.assertIn('JARVIS: Установить громкость 30 процентов?', self.output)
        self.assertEqual(process.call_args.args[0], ['/usr/bin/osascript', '-e', 'set volume output volume 30'])
        self.assertEqual(self.speech.recognize.call_args_list[1].args, (5.0,))
        process.assert_called_once()

    @patch('actions.macos.subprocess.run')
    def test_confirmation_cancel_unknown_and_recognition_failure(self, process):
        for reply in ('нет', 'отмена', 'no', 'не знаю', '', 'да нет',
                      SpeechError('No speech', recoverable=True), RuntimeError('private speech')):
            with self.subTest(reply=type(reply).__name__):
                self.make_session(['сделай скриншот', reply, 'выход']).run()
                self.assertIn('JARVIS: Действие отменено.', self.output)
                self.assertNotIn('private speech', str(self.logger.mock_calls))
        process.assert_not_called()

    @patch('actions.macos.subprocess.run')
    def test_confirmation_is_not_reused_and_never_dispatches_reply(self, process):
        session = self.make_session(['выключи звук', 'открой Safari', 'выход'])
        session.run()
        self.assertIn('JARVIS: Действие отменено.', self.output)
        process.assert_not_called()

    def test_dangerous_is_still_denied(self):
        session = self.make_session(['выключи звук', 'выход'])
        handler = Mock(return_value=CommandResult())
        # New isolated router using the same PermissionManager policy.
        from core.router import Router
        from core.permissions import PermissionManager
        session.router = Router(PermissionManager())
        session.router.register(Command('mute', 'Denied', handler, PermissionLevel.DANGEROUS))
        session.router.register(Command('exit', 'Exit', lambda: CommandResult(should_exit=True)))
        session.run()
        handler.assert_not_called()
        self.assertIn('JARVIS: Действие не разрешено.', self.output)

    def test_unknown_and_empty_speech_never_dispatch(self):
        session = self.make_session(['', 'как дела', 'run whoami', 'выход'])
        session.router = Mock(wraps=self.router)
        session.run()
        self.assertEqual(session.router.dispatch.call_count, 1)
        self.assertEqual(self.output.count('JARVIS: Команда не распознана.'), 3)

    def test_speech_errors_recover_or_stop(self):
        session = self.make_session([SpeechError('Speech not recognized.', recoverable=True),
                                     RuntimeError('private transcript'), 'статус', 'выход'])
        session.run()
        self.assertIn('JARVIS: Система готова.', self.output)
        self.assertNotIn('private transcript', str(self.logger.mock_calls))
        session = self.make_session([SpeechError('Microphone access denied.'), 'выход'])
        session.run()
        self.assertEqual(self.speech.recognize.call_count, 1)
        self.assertIn('Microphone access denied.', self.output)

    def test_tts_failure_keeps_text_output_and_session(self):
        session = self.make_session(['версия', 'выход'])
        self.tts.speak.side_effect = RuntimeError('private output')
        session.run()
        self.assertIn('JARVIS: JARVIS 1.0.0', self.output)
        self.assertIn('JARVIS voice session stopped.', self.output)
        self.assertNotIn('private output', str(self.logger.mock_calls))

    def test_startup_errors_are_friendly(self):
        for error in (SpeechError('Microphone access denied.'), RuntimeError('backend failed')):
            session = self.make_session([])
            self.speech.prepare.side_effect = error
            session.run()
            self.speech.recognize.assert_not_called()
            self.assertEqual(self.output[-1], 'JARVIS voice session stopped.')

    @patch('actions.macos.subprocess.run')
    def test_interrupt_during_listening_or_confirmation(self, process):
        for phrases in ([KeyboardInterrupt()], ['сделай скриншот', KeyboardInterrupt()],
                        [EOFError()], ['выключи звук', EOFError()]):
            session = self.make_session(phrases)
            session.run()
            self.assertEqual(self.output[-1], 'JARVIS voice session stopped.')
        process.assert_not_called()

    def test_speaking_finishes_before_next_listen(self):
        events = []
        phrases = iter(['статус', 'выход'])
        session = self.make_session([])
        self.speech.recognize.side_effect = lambda timeout: (events.append('listen'), next(phrases))[1]
        self.tts.speak.side_effect = lambda text: events.append('speak')
        session.run()
        self.assertEqual(events, ['speak', 'listen', 'speak', 'listen', 'speak'])

    @patch('actions.macos.subprocess.run')
    def test_invalid_volume_does_not_ask_for_confirmation(self, process):
        self.make_session(['громкость 150', 'выход']).run()
        self.assertTrue(any('Invalid volume' in item for item in self.output))
        self.assertEqual(self.speech.recognize.call_count, 2)
        process.assert_not_called()

    def test_no_speech_timeout_cools_down_then_retries_successfully(self):
        session = self.make_session([SpeechError('No speech detected.', recoverable=True),
                                     'версия', 'выход'])
        session.run()
        session._pause.assert_called_once_with(0.4)
        self.assertIn('No speech detected.', self.output)
        self.assertIn('JARVIS: JARVIS 1.0.0', self.output)
        self.assertEqual(self.speech.recognize.call_count, 3)
        self.assertLess(self.output.index('You: версия'), self.output.index('JARVIS: JARVIS 1.0.0'))

    def test_native_recognizer_error_stops_instead_of_restarting_broken_engine(self):
        from voice.speech_to_text import AppleSpeechToText
        message, recoverable = AppleSpeechToText.ERRORS['recognizer_error']
        session = self.make_session([SpeechError(message, recoverable=recoverable), 'выход'])
        session.run()
        self.assertEqual(self.speech.recognize.call_count, 1)
        self.assertIn('--debug', '\n'.join(self.output))
        self.assertEqual(self.output[-1], 'JARVIS voice session stopped.')
