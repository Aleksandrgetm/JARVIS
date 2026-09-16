import unittest

from voice.normalizer import VoiceCommandNormalizer
from voice.responses import confirmation_text


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = VoiceCommandNormalizer()

    def test_requested_phrases(self):
        cases = {
            'Джарвис открой Safari': 'open app Safari',
            'Джарвис, открой Safari.': 'open app Safari',
            'открой Safari': 'open app Safari',
            'запусти Spotify': 'open app Spotify',
            'открой Finder': 'open app Finder',
            'открой YouTube': 'open url https://youtube.com',
            'открой ютуб': 'open url https://youtube.com',
            'открой GitHub': 'open url https://github.com',
            'открой загрузки': 'open folder Downloads',
            'открой документы': 'open folder Documents',
            'открой рабочий стол': 'open folder Desktop',
            'открой проекты': 'open folder Projects',
            'громкость 30': 'volume 30',
            'поставь громкость 50': 'volume 50',
            'выключи звук': 'mute', 'включи звук': 'unmute',
            'сделай скриншот': 'screenshot',
            'информация о системе': 'system info',
            'статус': 'status', 'версия': 'version', 'помощь': 'help',
            'выход': 'exit', 'Джарвис завершить работу': 'exit',
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(self.normalizer.normalize(phrase), expected)

    def test_address_and_case(self):
        for phrase in ('Jarvis открой Safari', 'JARVIS, открой Safari!',
                       '  джарвис,   ОТКРОЙ   сафари  ', 'открой «Safari»'):
            self.assertEqual(self.normalizer.normalize(phrase), 'open app Safari')
        self.assertEqual(self.normalizer.normalize('открой Visual Studio Code'), 'open app Visual Studio Code')

    def test_numeric_transcriptions(self):
        cases = {'поставь громкость тридцать': 'volume 30',
                 'Джарвис, поставь громкость на тридцать процентов': 'volume 30',
                 'громкость двадцать пять процентов': 'volume 25',
                 'громкость 30%': 'volume 30', 'громкость сто': 'volume 100',
                 'громкость ноль': 'volume 0', 'громкость 101': 'volume 101',
                 'громкость -10': 'volume -10'}
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(self.normalizer.normalize(phrase), expected)

    def test_empty_unknown_and_arbitrary_execution_not_forwarded(self):
        for phrase in ('', '  ', 'Джарвис', 'Jarvis!', 'какая погода',
                       'shell whoami', 'run ls', 'exec rm -rf', 'sudo reboot',
                       'выключи Mac', 'удали файл', 'громкость очень много',
                       'джарвисстатус', 'статус\x00'):
            with self.subTest(phrase=phrase):
                self.assertIsNone(self.normalizer.normalize(phrase))

    def test_confirmations_are_exact(self):
        for answer in ('да', 'Да.', ' подтверждаю ', 'YES!'):
            self.assertTrue(self.normalizer.is_confirmation(answer))
        for answer in ('', 'нет', 'отмена', 'no', 'может быть', 'да нет',
                       'да открой Safari', 'yes no', 'не подтверждаю', 'y'):
            self.assertFalse(self.normalizer.is_confirmation(answer))

    def test_confirmation_prompts(self):
        cases = {'Set volume to 30%? [y/N] ': 'Установить громкость 30 процентов?',
                 'Mute audio? [y/N] ': 'Выключить звук?',
                 'Unmute audio? [y/N] ': 'Включить звук?',
                 'Take screenshot? [y/N] ': 'Сделать скриншот?'}
        for prompt, expected in cases.items():
            self.assertEqual(confirmation_text(prompt), expected)
