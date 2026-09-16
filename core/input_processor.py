"""Hybrid routing shared by CLI and voice; Router remains the execution boundary."""

import shlex
import re
from core.performance import mark, note

from core.router import CommandResult, UnknownCommandError


def process_input(text, *, router, normalizer, confirm, brain=None, voice=False, on_sentence=None):
    if not voice:
        # Recognize explicit command syntax without running handlers or probing actions.
        try:
            tokens = [token.lower() for token in shlex.split(text)]
        except ValueError:
            return router.dispatch(text, confirm=confirm)  # Existing quoting error.
        if any(tokens[:len(c.name.split())] == c.name.split() for c in router.commands):
            return router.dispatch(text, confirm=confirm)
    command = normalizer.normalize_confident(text)
    mark("command_normalized")
    if command:
        note('[ROUTER] deterministic match')
        note('[AI] skipped')
        try:
            return router.dispatch(command, confirm=confirm)
        finally:
            mark('deterministic_router_done')
    identity = normalizer.clean(text).casefold()
    identity = re.sub(r"^(?:джарвис|jarvis)\b[\s,.:;!?—-]*", "", identity)
    if identity in ("кто ты", "как тебя зовут"):
        note('[ROUTER] local identity match')
        note('[AI] skipped')
        return CommandResult('Я JARVIS, твой персональный ассистент.', conversational=True)
    if brain is None or not text.strip():
        raise UnknownCommandError()
    decision = brain.resolve(text, on_sentence=on_sentence) if on_sentence is not None else brain.resolve(text)
    if decision.command is not None:
        return router.dispatch(decision.command, confirm=confirm)
    return CommandResult(decision.response or "", conversational=True, streamed=decision.streamed)
