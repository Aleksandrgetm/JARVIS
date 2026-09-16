import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import main


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        lock = patch('main.InstanceLock')
        lock.start()
        self.addCleanup(lock.stop)
        settings = patch('core.config.load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)

    @patch('main.setup_logger', return_value=Mock())
    @patch('main.Assistant')
    @patch('voice.listener.build_native_bridge')
    def test_default_and_explicit_text_do_not_initialize_voice(self, build, assistant, logger):
        for args in ([], ['--text']):
            main.main(args)
        self.assertEqual(assistant.return_value.run.call_count, 2)
        build.assert_not_called()

    @patch('main.setup_logger', return_value=Mock())
    @patch('voice.voice_assistant.VoiceAssistant')
    def test_voice_and_network_are_explicit(self, voice, logger):
        for args, allowed in ((['--voice'], False), (['--voice', '--voice-allow-network'], True)):
            main.main(args)
            self.assertEqual(voice.call_args.args[0].voice_allow_network, allowed)
            voice.return_value.run.assert_called()

    def test_conflicting_modes_or_network_without_voice_are_rejected(self):
        for args in (['--text', '--voice'], ['--voice-allow-network'], ['--debug'],
                     ['--daemon', '--voice'], ['--daemon', '--debug']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main.main(args)
            self.assertEqual(caught.exception.code, 2)

    @patch('main.setup_logger', return_value=Mock())
    @patch('voice.voice_assistant.VoiceAssistant')
    def test_voice_debug_preserves_local_only_mode(self, voice, logger):
        main.main(['--voice', '--debug'])
        config = voice.call_args.args[0]
        self.assertTrue(config.voice_debug)
        self.assertFalse(config.voice_allow_network)

    @patch('service.daemon.run_daemon', return_value=0)
    @patch('voice.voice_assistant.VoiceAssistant')
    def test_daemon_entrypoint_never_starts_voice_loop(self, voice, daemon):
        self.assertEqual(main.main(['--daemon']), 0)
        daemon.assert_called_once_with()
        voice.assert_not_called()

    @patch('main.InstanceLock', side_effect=main.AlreadyRunning())
    @patch('main.setup_logger', return_value=Mock())
    @patch('voice.voice_assistant.VoiceAssistant')
    def test_existing_instance_prevents_voice_initialization(self, voice, logger, lock):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.main(['--voice'])
        self.assertIn('JARVIS уже запущен.', output.getvalue())
        voice.assert_not_called()
