import threading
import unittest
from unittest.mock import Mock, patch

from brain.conversation_stream import ConversationStream
from core.bootstrap import create_router
from core.config import Config
from core.input_processor import process_input
from core.performance import LatencyAudit, current_audit
from voice.normalizer import VoiceCommandNormalizer
from voice.speech_queue import SpeechQueue
from voice.text_to_speech import MacOSTextToSpeech
from tests.test_streaming_latency import HEADER
from tests import test_streaming_latency as streaming_fixtures


class EarlyChunkTests(unittest.TestCase):
    def test_first_comma_flushes_before_sentence_is_complete(self):
        output = []
        stream = ConversationStream(output.append)
        stream.feed(HEADER + 'Я JAR')
        self.assertEqual(output, [])
        stream.feed('VIS,')
        self.assertEqual(output, ['Я JARVIS,'])
        stream.feed(' твой персональный ассистент')
        self.assertEqual(output, ['Я JARVIS,'])
        stream.finish('Я JARVIS, твой персональный ассистент')
        self.assertEqual(output, ['Я JARVIS,', 'твой персональный ассистент'])

    def test_three_complete_words_never_split_following_partial_word(self):
        output = []
        stream = ConversationStream(output.append)
        stream.feed(HEADER + 'Docker помогает запуск')
        self.assertEqual(output, [])
        stream.feed('ать приложе')
        self.assertEqual(output, ['Docker помогает запускать'])
        stream.feed('ния в контейнерах')
        stream.finish('Docker помогает запускать приложения в контейнерах')
        self.assertEqual(output, ['Docker помогает запускать', 'приложения в контейнерах'])

    def test_each_natural_punctuation_flushes_first_chunk(self):
        for punctuation in ',;:.!?':
            output = []
            stream = ConversationStream(output.append)
            stream.feed(HEADER + 'Привет' + punctuation)
            self.assertEqual(output, ['Привет' + punctuation])

    def test_identity_is_local_but_extended_questions_are_not(self):
        brain, router = Mock(), create_router(Config(), Mock())
        for text in ['Джарвис, кто ты?', 'как тебя зовут', 'Кто ты?', 'Jarvis, как тебя зовут?']:
            result = process_input(text, router=router, normalizer=VoiceCommandNormalizer(),
                                   confirm=None, brain=brain, voice=True)
            self.assertEqual(result.message, 'Я JARVIS, твой персональный ассистент.')
        brain.resolve.assert_not_called()
        brain.resolve.return_value.command = None
        process_input('кто ты и что такое Docker', router=router, normalizer=VoiceCommandNormalizer(),
                      confirm=None, brain=brain, voice=True)
        brain.resolve.assert_called_once()

    @patch('voice.text_to_speech.platform.system', return_value='Darwin')
    @patch('voice.text_to_speech.subprocess.Popen')
    def test_actual_process_event_is_after_spawn_before_communicate_in_worker(self, popen, platform):
        audit = LatencyAudit()
        process = popen.return_value.__enter__.return_value
        process.returncode = 0
        def communicate(**kwargs):
            self.assertIn('first_tts_process_started', audit.events)
        process.communicate.side_effect = communicate
        token = current_audit.set(audit)
        try:
            self.assertNotIn('first_tts_process_started', audit.events)
            queue = SpeechQueue(MacOSTextToSpeech().speak)
            queue.submit('Первое.'); queue.submit('Второе.')
            queue.finish()
            self.assertFalse(queue.failed)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(process.communicate.call_count, 2)
        finally:
            current_audit.reset(token)


class ProducerConsumerTests(unittest.TestCase):
    def test_http_reader_continues_while_first_tts_chunk_is_blocked(self):
        # Reuse the mocked HTTP fixture, not a real Ollama server.
        fixture = streaming_fixtures.StreamingProviderTests()
        fixture.setUp()
        started, release, generation_done = threading.Event(), threading.Event(), threading.Event()
        spoken = []
        active, maximum_active = 0, 0
        def speak(text):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            spoken.append(text)
            started.set()
            release.wait(2)
            active -= 1
        queue = SpeechQueue(speak)
        lines = iter([fixture.line(HEADER + 'Я JARVIS,'),
                      fixture.line(' твой персональный ассистент."}'), fixture.line(done=True)])
        def read(_):
            if fixture.response.readline.call_count == 2:
                if not started.wait(1):
                    raise AssertionError('Early TTS did not start')
            return next(lines)
        fixture.response.readline.side_effect = read
        result = []
        def produce():
            result.append(fixture.brain.resolve('расскажи о себе', on_sentence=queue.submit))
            generation_done.set()
        producer = threading.Thread(target=produce)
        try:
            producer.start()
            self.assertTrue(generation_done.wait(1), 'HTTP producer blocked on TTS')
            self.assertFalse(release.is_set())
            self.assertTrue(result[0].streamed)
        finally:
            release.set()
            producer.join(2)
            queue.finish()
            fixture.doCleanups()
        self.assertEqual(spoken, ['Я JARVIS,', 'твой персональный ассистент.'])
        self.assertEqual(maximum_active, 1)
