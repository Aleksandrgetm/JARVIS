import json
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

from brain.brain import Brain, UNAVAILABLE
from brain.conversation_stream import ConversationStream
from brain.ollama_provider import OllamaClient
from core.bootstrap import create_router
from core.config import Config
from core.performance import LatencyAudit, current_audit
from voice.speech_queue import SpeechQueue
from voice.voice_assistant import VoiceAssistant

HEADER = '{"type":"conversation","action":null,"parameters":{},"confidence":0.99,"response":"'


class ConversationStreamTests(unittest.TestCase):
    def test_first_sentence_before_final_json_and_no_duplicate(self):
        output = []
        parser = ConversationStream(output.append)
        parser.feed(HEADER + 'Я JARVIS, твой ассистент. ')
        self.assertEqual(output, ['Я JARVIS,', 'твой ассистент.'])
        parser.feed('Чем помочь?"}')
        parser.finish('Я JARVIS, твой ассистент. Чем помочь?')
        self.assertEqual(output, ['Я JARVIS,', 'твой ассистент.', 'Чем помочь?'])

    def test_character_boundaries_and_json_escapes(self):
        text = 'Я JARVIS, твой помощник. Привет, "друг"!'
        raw = HEADER + json.dumps(text, ensure_ascii=True)[1:] + '}'
        output = []
        parser = ConversationStream(output.append)
        for char in raw:
            parser.feed(char)
        parser.finish(text)
        self.assertEqual(' '.join(output), text)

    def test_actions_incomplete_headers_and_unknown_fields_never_speak(self):
        for raw in [HEADER.replace('conversation', 'action') + 'Открываю Safari.',
                    HEADER.replace('null', '"run_shell"') + 'Выполняю shell.',
                    '{"response":"Привет, я JARVIS.',
                    HEADER.replace('0.99', 'true') + 'Привет, я JARVIS.',
                    HEADER.replace('"confidence":', '"extra":0,"confidence":') + 'Привет, я JARVIS.']:
            output = []
            ConversationStream(output.append).feed(raw)
            self.assertEqual(output, [])

    def test_split_reasoning_tag_cannot_enter_tts(self):
        parser = ConversationStream(Mock())
        parser.feed(HEADER + '<thi')
        with self.assertRaises(ValueError):
            parser.feed('nk>internal analysis.')
        parser.on_sentence.assert_not_called()

    def test_long_sentence_chunks_at_word_boundaries(self):
        output = []
        text = 'слово ' * 70
        parser = ConversationStream(output.append)
        parser.feed(HEADER + text)
        self.assertGreater(len(output), 0)
        parser.finish(text.strip())
        self.assertEqual(' '.join(output), text.strip())


class StreamingProviderTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch('brain.ollama_provider.HTTPConnection')
        self.connection = self.patch.start().return_value
        self.addCleanup(self.patch.stop)
        self.response = self.connection.getresponse.return_value
        self.response.status = 200
        self.response.read.return_value = b''
        self.client = OllamaClient('qwen3:8b')
        self.brain = Brain(self.client, Mock())

    def line(self, content='', done=False, **kwargs):
        return json.dumps(dict(done=done, message=dict(content=content), **kwargs)).encode() + b'\n'

    def test_real_pipeline_speaks_while_http_is_still_streaming(self):
        spoken = threading.Event()
        order = []
        speech, tts, output = Mock(), Mock(), []
        speech.recognize.side_effect = ['расскажи о себе', 'выход']
        def speak(text):
            if 'твой помощник' in text:
                order.append('tts')
                spoken.set()
        tts.speak.side_effect = speak
        lines = iter([self.line(HEADER + 'Я JARVIS, твой помощник. '),
                      self.line('Чем помочь?"}'), self.line(done=True)])
        def read(_):
            if self.response.readline.call_count == 2:
                self.assertTrue(spoken.wait(1), 'TTS must start before next model chunk')
                order.append('next token')
            return next(lines)
        self.response.readline.side_effect = read
        VoiceAssistant(Config(voice_debug=True), Mock(), create_router(Config(), Mock()),
                       speech, tts, writer=output.append, brain=self.brain).run()
        self.assertEqual(order, ['tts', 'next token'])
        self.connection.request.assert_called_once()
        self.assertTrue(json.loads(self.connection.request.call_args.kwargs['body'])['stream'])
        self.assertEqual(output.count('JARVIS: твой помощник.'), 1)
        self.assertEqual(sum('[PERF SUMMARY]' in x for x in output), 2)
        self.assertTrue(any('ollama_first_token timestamp=' in x for x in output))
        self.assertIn('[AI] thinking=false', output)
        self.assertTrue(any('first audible response: n/a' in x for x in output))

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_full_deterministic_voice_path_has_no_http_and_reports_summary(self, process, platform):
        speech, tts, output = Mock(), Mock(), []
        speech.recognize.side_effect = ['Джарвис, открой Safari', 'Джарвис, открой YouTube',
                                       'Джарвис, сделай скриншот', 'нет',
                                       'Джарвис, громкость 30', 'нет', 'выход']
        VoiceAssistant(Config(voice_debug=True), Mock(), create_router(Config(), Mock()),
                       speech, tts, writer=output.append, brain=self.brain).run()
        self.connection.request.assert_not_called()
        self.assertEqual(process.call_count, 2)  # Open only; screenshot/volume declined.
        self.assertEqual(process.call_args_list[0].args[0], ['/usr/bin/open', '-a', 'Safari'])
        self.assertEqual(output.count('[ROUTER] deterministic match'), 5)
        self.assertEqual(output.count('[AI] skipped'), 5)
        self.assertEqual(sum('[PERF SUMMARY]' in line for line in output), 5)

    def test_action_waits_for_full_validation_and_no_tts_prefix(self):
        raw = '{"type":"action","action":"open_app","parameters":{"name":"Safari"},"confidence":0.99,"response":null}'
        self.response.readline.side_effect = [self.line(raw[:60]), self.line(raw[60:]), self.line(done=True)]
        speak = Mock()
        result = self.brain.resolve('мне нужен Safari', on_sentence=speak)
        self.assertEqual(result.command, 'open app Safari')
        speak.assert_not_called()

    def test_truncated_malformed_error_and_reasoning_are_rejected(self):
        for lines in [[self.line(HEADER + 'Привет'), b''], [b'bad json\n'],
                      [self.line(HEADER + 'Привет', done=True, done_reason='length')],
                      [self.line('<think>private</think>')],
                      [self.line(HEADER + 'Привет"}'), self.line(done=True, error='private')]]:
            with self.subTest(lines=lines):
                self.response.readline.side_effect = lines
                result = self.brain.resolve('привет', on_sentence=Mock())
                self.assertEqual(result.response, UNAVAILABLE)
                self.assertIsNone(self.client._connection)

    def test_reasoning_fields_ignored_and_connection_reused(self):
        body = json.dumps(dict(done=False, message=dict(content='', thinking='PRIVATE'))).encode() + b'\n'
        for _ in range(2):
            self.response.readline.side_effect = [body, self.line(HEADER + 'Привет, я JARVIS!"}'), self.line(done=True)]
            output = []
            self.assertTrue(self.brain.resolve('привет', on_sentence=output.append).streamed)
            self.assertNotIn('PRIVATE', str(output))
        self.assertEqual(self.connection.request.call_count, 2)
        self.connection.close.assert_not_called()


class QueueAndAuditTests(unittest.TestCase):
    def test_queue_is_serial_and_drains_before_return(self):
        output = []
        active = 0
        def speak(text):
            nonlocal active
            active += 1
            self.assertEqual(active, 1)
            output.append(text)
            active -= 1
        queue = SpeechQueue(speak)
        queue.submit('Первое.'); queue.submit('Второе.')
        queue.finish()
        self.assertEqual(output, ['Первое.', 'Второе.'])
        self.assertIsNone(queue.worker)

    def test_queue_cancellation_discards_pending_chunks(self):
        entered, release = threading.Event(), threading.Event()
        output = []
        def speak(text):
            output.append(text)
            entered.set()
            release.wait(1)
        queue = SpeechQueue(speak)
        queue.submit('Первое.')
        self.assertTrue(entered.wait(1))
        queue.submit('Отменённое.')
        queue.cancelled.set()
        release.set()
        queue.finish(cancel=True)
        self.assertEqual(output, ['Первое.'])

    def test_audit_calculates_intervals_and_does_not_invent_audio_onset(self):
        output = []
        audit = LatencyAudit(True, output.append)
        base = audit.wall_start
        audit.import_native(dict(speech_start=base, speech_end_estimated=base + 0.1,
                                 speech_end_detected=base + 0.9))
        audit.mark('stt_final', base+1)
        audit.mark('tts_start', base+1.2)
        audit.summary()
        self.assertIn('speech_end (estimated) -> stt_final: 900.0 ms', output[-1])
        self.assertIn('first audible response: n/a', output[-1])

    def test_end_silence_environment_validation(self):
        for value, expected in [('0.8', .8), ('1.8', 1.8), ('nan', .7), ('0', .7), ('10', .7)]:
            with patch.dict(os.environ, {'JARVIS_VOICE_END_SILENCE': value}):
                self.assertEqual(Config.from_env().voice_end_silence, expected)
