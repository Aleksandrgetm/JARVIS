import unittest
from unittest.mock import Mock, patch

from core.assistant import Assistant, clear_terminal
from core.config import Config
from core.router import Command


class AssistantTests(unittest.TestCase):
    def make_assistant(self, inputs):
        self.output = []
        self.logger = Mock()
        self.clearer = Mock()
        return Assistant(Config(), self.logger, reader=Mock(side_effect=inputs),
                         writer=self.output.append, clearer=self.clearer)

    def test_cli_recovers_from_empty_and_unknown_input(self):
        assistant = self.make_assistant(["", "   ", "missing", "status", "version", "clear", "exit"])
        assistant.run()
        self.assertEqual(self.output[0], "JARVIS\nSystem online.\n")
        self.assertIn("Unknown command. Type 'help' for available commands.", self.output)
        self.assertIn("JARVIS is online.", self.output)
        self.assertIn("JARVIS 0.2.0", self.output)
        self.assertEqual(self.output[-1], "System offline.")
        self.clearer.assert_called_once_with()

    def test_interrupt_and_eof_exit_cleanly(self):
        for exception in (KeyboardInterrupt(), EOFError()):
            with self.subTest(exception=type(exception).__name__):
                self.make_assistant([exception]).run()
                self.assertEqual(self.output[-1], "\nSystem offline.")
                self.logger.info.assert_called_with("System offline")

    def test_handler_error_is_logged_and_next_command_works(self):
        assistant = self.make_assistant(["broken", "status", "exit"])
        assistant.router.register(Command("broken", "Test", Mock(side_effect=RuntimeError("boom"))))
        assistant.run()
        self.logger.exception.assert_called_once_with("Command processing failed")
        self.assertIn("Command failed. See data/logs/jarvis.log for details.", self.output)
        self.assertIn("JARVIS is online.", self.output)

    def test_clear_uses_ansi_only_for_terminal(self):
        for is_terminal in (True, False):
            with self.subTest(is_terminal=is_terminal), patch("core.assistant.sys.stdout") as stdout:
                stdout.isatty.return_value = is_terminal
                clear_terminal()
                if is_terminal:
                    stdout.write.assert_called_once_with("\033[2J\033[H")
                    stdout.flush.assert_called_once_with()
                else:
                    stdout.write.assert_not_called()
