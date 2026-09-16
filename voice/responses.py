"""Russian presentation of existing results and confirmation prompts."""

import re


def confirmation_text(prompt: str) -> str:
    volume = re.fullmatch(r"Set volume to ([0-9]+)%\? \[y/N\] ", prompt)
    if volume:
        return "Установить громкость " + volume.group(1) + " процентов?"
    return {"Mute audio? [y/N] ": "Выключить звук?",
            "Unmute audio? [y/N] ": "Включить звук?",
            "Take screenshot? [y/N] ": "Сделать скриншот?"}.get(prompt, "Выполнить это действие?")


def response_text(message: str) -> str:
    translations = {"Cancelled.": "Действие отменено.", "System offline.": "Завершаю работу.",
                    "JARVIS is online.": "Система готова.", "Muted.": "Звук выключен.",
                    "Unmuted.": "Звук включен.", "Opening website.": "Открываю сайт.",
                    "Opening folder.": "Открываю папку."}
    if message in translations:
        return translations[message]
    if message.startswith("Opening ") and message.endswith("."):
        return "Открываю " + message[len("Opening "):]
    if message.startswith("Volume set to "):
        return "Команда выполнена."
    return message or "Команда выполнена."
