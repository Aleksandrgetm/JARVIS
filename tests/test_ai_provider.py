import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from brain.ai_client import AIUnavailable
from brain.openai_provider import OpenAIClient
from brain.schemas import INTENT_SCHEMA
from core.config import Config


class OpenAIProviderTests(unittest.TestCase):
    def request(self):
        return OpenAIClient('configured-model', 9).complete(
            system_prompt='fixed instructions', user_text='привет', schema=INTENT_SCHEMA)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only-placeholder'})
    def test_official_sdk_request_contract_and_cleanup(self):
        api, factory = Mock(), Mock()
        factory.return_value.__enter__ = Mock(return_value=api)
        factory.return_value.__exit__ = Mock(return_value=False)
        api.responses.create.return_value = SimpleNamespace(status='completed', output_text='result')
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=factory)}):
            self.assertEqual(self.request(), 'result')
        factory.assert_called_once_with(api_key='test-only-placeholder',
            base_url='https://api.openai.com/v1', timeout=9, max_retries=0)
        kwargs = api.responses.create.call_args.kwargs
        self.assertEqual(kwargs['model'], 'configured-model')
        self.assertEqual(kwargs['tools'], [])
        self.assertFalse(kwargs['store'])
        self.assertTrue(kwargs['text']['format']['strict'])
        self.assertIs(kwargs['text']['format']['schema'], INTENT_SCHEMA)
        self.assertEqual(len(kwargs['input']), 2)
        factory.return_value.__exit__.assert_called_once()

    @patch.dict(os.environ, {'OPENAI_API_KEY': ''})
    def test_missing_key_fails_without_sdk_request(self):
        factory = Mock()
        with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=factory)}):
            with self.assertRaises(AIUnavailable):
                self.request()
        factory.assert_not_called()

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only-placeholder'})
    def test_timeout_provider_error_incomplete_and_empty_response(self):
        for response in [TimeoutError('secret'), OSError('secret'),
                         SimpleNamespace(status='incomplete', output_text='partial'),
                         SimpleNamespace(status='completed', output_text='')]:
            api, factory = Mock(), Mock()
            factory.return_value.__enter__ = Mock(return_value=api)
            factory.return_value.__exit__ = Mock(return_value=False)
            if isinstance(response, Exception):
                api.responses.create.side_effect = response
            else:
                api.responses.create.return_value = response
            with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=factory)}):
                with self.assertRaises(AIUnavailable) as error:
                    self.request()
            self.assertNotIn('secret', str(error.exception))
            factory.return_value.__exit__.assert_called_once()

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only-placeholder'})
    def test_missing_optional_sdk_is_graceful(self):
        with patch.dict(sys.modules, {'openai': None}):
            with self.assertRaises(AIUnavailable):
                self.request()


class AIConfigTests(unittest.TestCase):
    def setUp(self):
        settings = patch('core.config.load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)

    @patch.dict(os.environ, {}, clear=True)
    def test_default_is_disabled(self):
        self.assertEqual(Config.from_env().ai_provider, 'disabled')

    @patch.dict(os.environ, {'JARVIS_AI_PROVIDER': ' OpenAI ', 'JARVIS_AI_MODEL': 'my-model',
                            'JARVIS_AI_TIMEOUT': '3', 'OPENAI_API_KEY': 'private-key'}, clear=True)
    def test_environment_config_keeps_key_out(self):
        config = Config.from_env()
        self.assertEqual((config.ai_provider, config.ai_model, config.ai_timeout), ('openai', 'my-model', 3))
        self.assertNotIn('private-key', repr(config))

    def test_invalid_timeout_uses_bounded_default(self):
        for value in ['nan', 'inf', '-1', '0', '61', 'not a number']:
            with patch.dict(os.environ, {'JARVIS_AI_TIMEOUT': value}):
                self.assertEqual(Config.from_env().ai_timeout, 15)
