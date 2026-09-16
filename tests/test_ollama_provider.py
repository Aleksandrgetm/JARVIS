import json
import os
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch

from brain.ai_client import AIUnavailable, DisabledAIClient
from brain.brain import Brain, UNAVAILABLE, create_brain
from brain.ollama_provider import OllamaClient
from brain.openai_provider import OpenAIClient
from brain.provider_factory import create_ai_client
from brain.schemas import INTENT_SCHEMA
from core.config import Config
from core.bootstrap import create_router
from core.input_processor import process_input
from voice.normalizer import VoiceCommandNormalizer
from voice.voice_assistant import VoiceAssistant
from tests.test_brain import action, conversation


class OllamaTests(unittest.TestCase):
    def setUp(self):
        settings = patch('core.config.load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)
        self.patch = patch('brain.ollama_provider.HTTPConnection')
        self.opener = self.patch.start().return_value
        self.addCleanup(self.patch.stop)
        self.response = self.opener.getresponse.return_value
        self.response.status = 200
        self.client = OllamaClient('qwen3:8b')
        self.logger = Mock()
        self.brain = Brain(self.client, self.logger)

    def reply(self, content, **extra):
        body = json.dumps(dict(done=True, message=dict(content=content, **extra))).encode()
        self.response.read.return_value = body
        self.response.readline.return_value = body + b'\n'
        self.response.read.side_effect = lambda size: b'' if size == 1 else body

    def complete(self):
        return self.client.complete(system_prompt='JARVIS', user_text='Кто ты?', schema=INTENT_SCHEMA)

    def test_factory_is_lazy_and_keeps_all_providers(self):
        for name, cls in [('ollama', OllamaClient), ('openai', OpenAIClient),
                          ('disabled', DisabledAIClient), ('unknown', DisabledAIClient)]:
            self.assertIsInstance(create_ai_client(Config(ai_provider=name)), cls)
        create_brain(Config(ai_provider='ollama'), self.logger)
        self.opener.request.assert_not_called()

    @patch.dict(os.environ, {'JARVIS_AI_PROVIDER': 'ollama'}, clear=True)
    def test_provider_defaults_without_key(self):
        config = Config.from_env()
        self.assertEqual((config.ai_model, config.ai_timeout, config.ollama_url),
                         ('qwen3:8b', 30, 'http://localhost:11434'))
        self.assertIsInstance(create_ai_client(config), OllamaClient)

    def test_request_schema_thinking_timeout_and_one_request(self):
        self.reply(json.dumps(conversation()))
        self.complete()
        self.opener.request.assert_called_once()
        args = self.opener.request.call_args
        self.assertEqual(args.args, ('POST', '/api/chat'))
        payload = json.loads(args.kwargs['body'])
        self.assertEqual(payload['format'], INTENT_SCHEMA)
        self.assertFalse(payload['think'])
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['options']['num_predict'], 192)
        self.assertEqual(payload['keep_alive'], '10m')
        self.assertNotIn('tools', payload)
        self.assertNotIn('Authorization', args.kwargs['headers'])
        self.response.close.assert_called_once()

    def test_identity_conversation_and_natural_action(self):
        self.reply(json.dumps(conversation('Я JARVIS, твой персональный ассистент на Mac.')))
        self.assertIn('Я JARVIS', self.brain.resolve('Кто ты?').response)
        self.reply(json.dumps(action()))
        self.assertEqual(self.brain.resolve('Мне нужен Safari').command, 'open app Safari')

    @patch('actions.macos.subprocess.run')
    def test_malformed_and_unknown_actions_never_execute(self, process):
        for content in ['not json', '```json\n{}\n```', json.dumps(action('run_shell', {'command': 'rm -rf /'}))]:
            self.reply(content)
            self.assertEqual(self.brain.resolve('выполни shell').response, UNAVAILABLE)
        process.assert_not_called()

    def test_server_errors_and_timeout_are_sanitized(self):
        for error in [ConnectionRefusedError('private'), TimeoutError('private'),
                      URLError('private'), HTTPError('url', 404, 'model missing', {}, None)]:
            self.opener.request.side_effect = error
            self.assertEqual(self.brain.resolve('Кто ты?').response, UNAVAILABLE)
        self.assertNotIn('private', str(self.logger.mock_calls))

    def test_bad_envelopes_and_truncation(self):
        for body in [b'bad json', b'[]', b'{}', b'x' * 131073,
                     b'{"done":true,"error":"model missing"}',
                     b'{"done":false,"message":{"content":"{}"}}',
                     b'{"done":true,"done_reason":"length"}',
                     b'{"done":true,"message":{"content":""}}']:
            self.response.read.return_value = body
            with self.assertRaises(AIUnavailable):
                self.complete()

    def test_deterministic_command_bypasses_unavailable_ollama(self):
        self.opener.request.side_effect = ConnectionRefusedError()
        router = create_router(Config(), self.logger)
        result = process_input('status', router=router, normalizer=VoiceCommandNormalizer(),
                               confirm=None, brain=self.brain)
        self.assertEqual(result.message, 'JARVIS is online.')
        self.opener.request.assert_not_called()

    def test_separate_reasoning_never_reaches_voice_or_logs(self):
        self.reply(json.dumps(conversation('Я JARVIS.')), thinking='SECRET_REASONING', reasoning='SECRET_REASONING')
        speech, tts, output = Mock(), Mock(), []
        speech.recognize.side_effect = ['Джарвис расскажи о себе', 'выход']
        VoiceAssistant(Config(), self.logger, create_router(Config(), self.logger), speech, tts,
                       writer=output.append, pause=Mock(), brain=self.brain).run()
        tts.speak.assert_any_call('Я JARVIS.')
        self.assertNotIn('SECRET_REASONING', str(output) + str(tts.mock_calls) + str(self.logger.mock_calls))

    def test_inline_thinking_is_rejected_even_inside_json(self):
        for content in ['<think>private</think>{}', json.dumps(conversation('<think>private</think>Hi'))]:
            self.reply(content)
            self.assertEqual(self.brain.resolve('Привет').response, UNAVAILABLE)

    def test_remote_or_invalid_url_is_rejected_before_http(self):
        for url in ['https://example.com', 'http://example.com', 'http://localhost@evil.test',
                    'http://localhost:11434/path', 'http://localhost:99999']:
            self.client.base_url = url
            with self.assertRaises(AIUnavailable):
                self.complete()
        self.opener.request.assert_not_called()

    def test_connection_reused_and_closed_explicitly(self):
        self.reply(json.dumps(conversation()))
        self.complete()
        connection = self.client._connection
        self.complete()
        self.assertIs(self.client._connection, connection)
        self.assertEqual(self.opener.request.call_count, 2)
        self.opener.close.assert_not_called()
        self.client.close()
        self.opener.close.assert_called_once()
        self.assertIsNone(self.client._connection)

    def test_failed_connection_is_discarded_without_retry(self):
        self.opener.request.side_effect = TimeoutError()
        with self.assertRaises(AIUnavailable):
            self.complete()
        self.opener.request.assert_called_once()
        self.assertIsNone(self.client._connection)
        self.opener.request.side_effect = None
        self.reply(json.dumps(conversation()))
        self.complete()
        self.assertEqual(self.opener.request.call_count, 2)

    def test_debug_metadata_contains_only_metrics(self):
        self.client.debug = True
        self.response.read.return_value = json.dumps(dict(done=True,
            message=dict(content=json.dumps(conversation()), thinking='SECRET'),
            load_duration=1000000, prompt_eval_duration=2000000,
            eval_duration=3000000, eval_count=40)).encode()
        with patch('builtins.print') as output:
            self.complete()
        text = str(output.call_args_list)
        self.assertIn('load_duration=1.0ms', text)
        self.assertIn('eval_count=40', text)
        self.assertIn('ollama_total=', text)
        self.assertIn('ollama_first_token=n/a', text)
        self.assertNotIn('SECRET', text)

    def test_all_requested_fast_paths_skip_ollama_in_voice(self):
        router = Mock()
        for text in ['открой Safari', 'открой YouTube', 'громкость 30', 'выключи звук',
                     'сделай скриншот', 'status', 'version', 'help']:
            process_input(text, router=router, normalizer=VoiceCommandNormalizer(),
                          confirm=Mock(), brain=self.brain, voice=True)
        self.assertEqual(router.dispatch.call_count, 8)
        self.opener.request.assert_not_called()

    def test_voice_debug_times_stt_to_tts_without_exposing_reasoning(self):
        self.reply(json.dumps(conversation('Я JARVIS.')), thinking='SECRET')
        self.brain.debug = True
        speech, tts, output = Mock(), Mock(), []
        speech.recognize.side_effect = ['расскажи о себе', 'выход']
        with patch('builtins.print') as perf:
            VoiceAssistant(Config(voice_debug=True), self.logger, create_router(Config(), self.logger),
                           speech, tts, writer=output.append, brain=self.brain).run()
        self.assertIn('brain=', str(perf.call_args_list))
        for metric in ['stt=', 'tts_start timestamp=', 'total timestamp=']:
            self.assertTrue(any('[PERF] ' + metric in line for line in output))
        self.assertNotIn('SECRET', str(output) + str(tts.mock_calls))
        self.opener.request.assert_called_once()
