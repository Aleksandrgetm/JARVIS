import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import main


class EntryPointTests(unittest.TestCase):
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
        for args in (['--text', '--voice'], ['--voice-allow-network'], ['--debug']):
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
