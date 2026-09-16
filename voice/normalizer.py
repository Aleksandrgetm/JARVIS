"""Small deterministic Russian intent adapter; never executes commands."""

import re
import shlex
from typing import Optional


class VoiceCommandNormalizer:
    SIMPLE = {
        "выключи звук": "mute", "включи звук": "unmute",
        "сделай скриншот": "screenshot", "информация о системе": "system info",
        "статус": "status", "версия": "version", "помощь": "help",
        "выход": "exit", "завершить работу": "exit",
    }
    WEBSITES = {"youtube": "https://youtube.com", "ютуб": "https://youtube.com",
                "github": "https://github.com", "гитхаб": "https://github.com"}
    FOLDERS = {"загрузки": "Downloads", "документы": "Documents",
               "рабочий стол": "Desktop", "проекты": "Projects"}
    APPS = {"safari": "Safari", "сафари": "Safari", "spotify": "Spotify",
            "спотифай": "Spotify", "finder": "Finder", "файндер": "Finder",
            "terminal": "Terminal", "терминал": "Terminal",
            "visual studio code": "Visual Studio Code"}
    NUMBERS = {"ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4,
               "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
               "десять": 10, "одиннадцать": 11, "двенадцать": 12,
               "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
               "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
               "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
               "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
               "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}

    @staticmethod
    def clean(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip()).strip(" ,.!?;:")

    def normalize(self, text: str) -> Optional[str]:
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            return None
        phrase = self.clean(text)
        phrase = re.sub(r"^(?:джарвис|jarvis)\b[\s,.:;!?—-]*", "", phrase, flags=re.I)
        key = phrase.casefold().replace("ё", "е")
        if key in self.SIMPLE:
            return self.SIMPLE[key]
        volume = re.fullmatch(r"(?:поставь )?громкость (?:на )?(.+?)(?:\s*%| процентов| процента| процент)?", key)
        if volume:
            value = volume.group(1)
            if value in self.NUMBERS:
                value = str(self.NUMBERS[value])
            else:
                words = value.split()
                if (len(words) == 2 and self.NUMBERS.get(words[0], 0) in range(20, 100, 10)
                        and self.NUMBERS.get(words[1], 0) in range(1, 10)):
                    value = str(self.NUMBERS[words[0]] + self.NUMBERS[words[1]])
            # Range validation remains the responsibility of the existing Router.
            if re.fullmatch(r"-?[0-9]+", value):
                return "volume " + value
            return None
        opening = re.fullmatch(r"(?:открой|запусти)\s+(.+)", phrase, flags=re.I)
        if opening:
            target = opening.group(1).strip('"«»')
            target_key = target.casefold()
            if target_key in self.WEBSITES:
                return "open url " + self.WEBSITES[target_key]
            if target_key in self.FOLDERS:
                return "open folder " + self.FOLDERS[target_key]
            target = self.APPS.get(target_key, target)
            # One literal application-name argument, including shell-looking text.
            return "open app " + (target if re.fullmatch(r"[\w .-]+", target) else shlex.quote(target))
        return None

    def is_confirmation(self, text: str) -> bool:
        # Exact replies only: "да нет", "да открой ..." must not grant permission.
        return self.clean(text).casefold() in ("да", "подтверждаю", "yes")
