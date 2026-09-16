"""Offline tests: every model response and every OS action is mocked."""
import json
import unittest
from unittest.mock import Mock, patch

from brain.ai_client import AIUnavailable
from brain.brain import Brain, CLARIFICATION, UNAVAILABLE, create_brain
from brain.intent_parser import InvalidIntent, intent_to_command, parse_intent
from core.assistant import Assistant
from core.bootstrap import create_router
from core.config import Config
from core.input_processor import process_input
from core.router import CommandInputError
from voice.normalizer import VoiceCommandNormalizer
from voice.voice_assistant import VoiceAssistant


def action(name='open_app', parameters=None, confidence=0.98):
    return dict(type='action', action=name,
                parameters={'name': 'Safari'} if parameters is None else parameters,
                response=None, confidence=confidence)


def conversation(text='Привет! Чем могу помочь?'):
    return dict(type='conversation', action=None, parameters={}, response=text, confidence=0.99)


class IntentTests(unittest.TestCase):
    def test_allowlist_adapts_to_existing_commands(self):
        cases = [('open_app', {'name': 'Visual Studio Code'}, "open app 'Visual Studio Code'"),
                 ('open_url', {'url': 'youtube.com'}, 'open url https://youtube.com'),
                 ('open_folder', {'path': 'downloads'}, 'open folder downloads'),
                 ('set_volume', {'value': 30}, 'volume 30')]
        cases += [(a, {}, c) for a, c in [('mute', 'mute'), ('unmute', 'unmute'),
                  ('screenshot', 'screenshot'), ('system_info', 'system info'),
                  ('help', 'help'), ('status', 'status'), ('version', 'version'), ('exit', 'exit')]]
        for name, params, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(intent_to_command(parse_intent(json.dumps(action(name, params)))), expected)

    def test_unknown_actions_rejected(self):
        for name in ['delete_files', 'run_shell', 'shutdown', 'sudo', 'terminal', 'clear']:
            with self.subTest(name=name), self.assertRaises(InvalidIntent):
                parse_intent(json.dumps(action(name, {})))

    def test_invalid_parameters_rejected(self):
        cases = [action('set_volume', {'value': v}) for v in [150, -1, True, 30.0, '30']]
        cases += [action('open_url', {'url': v}) for v in [
            'file:///etc/passwd', 'javascript:alert(1)', 'https://', 'https://bad host',
            'https://user:password@example.com', 'https://example.com:99999']]
        cases += [action('open_app', {'name': v}) for v in ['', 'Safari; rm -rf /',
            '$(whoami)', '/bin/sh', '-a', 'Safari\nexit']]
        cases += [action('open_app', {}), action('open_app', {'name': 'Safari', 'confirmed': True}),
                  action('mute', {'shell': 'rm -rf /'}), action('open_folder', {'path': 42})]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(InvalidIntent):
                parse_intent(json.dumps(data))

    def test_invalid_shape_and_confidence(self):
        cases = [[], {}, dict(action(), extra=True), dict(action(), type='shell'),
                 dict(conversation(), action='exit'), dict(action(), response='Done')]
        cases += [dict(action(), confidence=v) for v in [True, -0.1, 1.1, '0.9', float('nan')]]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(InvalidIntent):
                parse_intent(json.dumps(data))
        for raw in ['not JSON', '```json\n{}\n```', '{"type":"action","type":"conversation"}', 'x'*16001]:
            with self.assertRaises(InvalidIntent):
                parse_intent(raw)

    def test_conversation_cannot_be_adapted_to_command(self):
        with self.assertRaises(InvalidIntent):
            intent_to_command(parse_intent(json.dumps(conversation('open app Safari'))))


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.logger = Mock()
        self.brain = Brain(self.client, self.logger)
        self.router = create_router(Config(), self.logger)

    def process(self, text, **kwargs):
        return process_input(text, router=self.router, normalizer=VoiceCommandNormalizer(),
                             brain=self.brain, confirm=kwargs.pop('confirm', None), **kwargs)

    def reply(self, data):
        self.client.complete.return_value = json.dumps(data)

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_natural_phrase_reaches_existing_action(self, process, platform):
        self.reply(action())
        self.process('мне нужен Safari')
        self.assertEqual(process.call_args.args[0], ['/usr/bin/open', '-a', 'Safari'])
        self.client.complete.assert_called_once()

    def test_conversation_never_dispatches(self):
        self.reply(conversation('open app Safari'))
        with patch.object(self.router, 'dispatch') as dispatch:
            result = self.process('расскажи о себе')
        dispatch.assert_not_called()
        self.assertTrue(result.conversational)
        self.assertEqual(result.message, 'open app Safari')

    def test_low_confidence_requires_clarification(self):
        self.reply(action(confidence=0.74))
        with patch.object(self.router, 'dispatch') as dispatch:
            self.assertEqual(self.process('мне нужен Safari').message, CLARIFICATION)
        dispatch.assert_not_called()
        self.reply(action(confidence=0.75))
        self.assertEqual(self.brain.resolve('мне нужен Safari').command, 'open app Safari')

    @patch('actions.macos.subprocess.run')
    def test_malicious_or_broken_output_never_executes(self, process):
        for raw in ['invalid JSON', json.dumps(action('run_shell', {'command': 'rm -rf /'})),
                    json.dumps(action('set_volume', {'value': 150}))]:
            self.client.complete.return_value = raw
            self.assertEqual(self.process('выполни rm -rf').message, UNAVAILABLE)
        process.assert_not_called()

    def test_failures_do_not_break_deterministic_commands_or_leak_payload(self):
        for error in [AIUnavailable('secret-key'), TimeoutError('private prompt'), OSError('credentials')]:
            self.client.complete.side_effect = error
            self.assertEqual(self.process('расскажи о себе').message, UNAVAILABLE)
            self.assertEqual(self.process('status').message, 'JARVIS is online.')
        logs = str(self.logger.mock_calls)
        for secret in ['secret-key', 'private prompt', 'credentials', 'кто ты?']:
            self.assertNotIn(secret, logs)

    def test_explicit_invalid_command_does_not_call_ai(self):
        for text in ['volume 150', 'open app', 'open app "Safari']:
            with self.assertRaises(CommandInputError):
                self.process(text)
        self.client.complete.assert_not_called()

    def test_disabled_ai_keeps_core_commands(self):
        self.brain = create_brain(Config(), self.logger)
        self.assertEqual(self.process('привет').message, UNAVAILABLE)
        self.assertEqual(self.process('version').message, 'JARVIS 1.1.0')

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_confident_voice_bypasses_ai_but_polite_phrase_does_not(self, process, platform):
        self.process('Джарвис открой Safari', voice=True)
        self.client.complete.assert_not_called()
        self.reply(action())
        self.process('открой мне Safari пожалуйста', voice=True)
        self.client.complete.assert_called_once()
        self.assertEqual(process.call_count, 2)

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_ai_action_still_requires_router_confirmation(self, process, platform):
        self.reply(action('set_volume', {'value': 30}))
        with self.assertRaises(PermissionError):
            self.process('потише пожалуйста')
        confirm = Mock(return_value=False)
        self.assertEqual(self.process('потише пожалуйста', confirm=confirm).message, 'Cancelled.')
        confirm.assert_called_once()
        process.assert_not_called()
        self.process('потише пожалуйста', confirm=lambda _: True)
        process.assert_called_once()

    def test_text_conversation_and_deterministic_exit(self):
        self.reply(conversation())
        output = []
        Assistant(Config(), self.logger, reader=Mock(side_effect=['привет', 'exit']),
                  writer=output.append, brain=self.brain).run()
        self.assertIn('Привет! Чем могу помочь?', output)
        self.assertIn('System offline.', output)
        self.client.complete.assert_called_once()

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_voice_ai_action_conversation_and_separate_confirmation(self, process, platform):
        self.client.complete.side_effect = [json.dumps(action()), json.dumps(conversation('Первая строка.\nВторая.')),
                                            json.dumps(action('set_volume', {'value': 30}))]
        speech, tts, output = Mock(), Mock(), []
        speech.recognize.side_effect = ['Джарвис мне нужен Safari', 'расскажи о себе', 'потише пожалуйста', 'нет', 'выход']
        VoiceAssistant(Config(), self.logger, self.router, speech, tts,
                       writer=output.append, pause=Mock(), brain=self.brain).run()
        process.assert_called_once()
        self.assertEqual(self.client.complete.call_count, 3)
        self.assertIn('You: Джарвис мне нужен Safari', output)
        self.assertIn('JARVIS: Открываю Safari.', output)
        tts.speak.assert_any_call('Первая строка.\nВторая.')
        self.assertIn('JARVIS: Действие отменено.', output)
