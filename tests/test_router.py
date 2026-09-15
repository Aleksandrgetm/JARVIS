import unittest
from unittest.mock import Mock

from core.permissions import PermissionLevel, PermissionManager
from core.router import Command, CommandResult, Router, UnknownCommandError


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.router = Router(PermissionManager())

    def test_dispatch_normalizes_input(self):
        handler = Mock(return_value=CommandResult("ok"))
        self.router.register(Command("test", "Test", handler))
        self.assertEqual(self.router.dispatch("  TEST  ").message, "ok")
        handler.assert_called_once_with()

    def test_empty_input(self):
        self.assertEqual(self.router.dispatch(" \t "), CommandResult())

    def test_unknown_command_and_unsupported_arguments(self):
        self.router.register(Command("test", "Test", lambda: CommandResult()))
        for text in ("missing", "test extra"):
            with self.subTest(text=text), self.assertRaises(UnknownCommandError):
                self.router.dispatch(text)

    def test_duplicate_registration_rejected(self):
        command = Command("test", "Test", lambda: CommandResult())
        self.router.register(command)
        with self.assertRaises(ValueError):
            self.router.register(command)

    def test_invalid_names_rejected(self):
        for name in ("", "UPPER", " test", "two words"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.router.register(Command(name, "Test", lambda: CommandResult()))

    def test_permission_policy_prevents_handler_execution(self):
        handler = Mock(return_value=CommandResult("ok"))
        self.router.register(Command("confirm", "Test", handler, PermissionLevel.CONFIRM))
        self.router.register(Command("danger", "Test", handler, PermissionLevel.DANGEROUS))
        for name, confirmed in (("confirm", False), ("danger", False), ("danger", True)):
            with self.subTest(name=name, confirmed=confirmed), self.assertRaises(PermissionError):
                self.router.dispatch(name, confirmed=confirmed)
        handler.assert_not_called()
        self.assertEqual(self.router.dispatch("confirm", confirmed=True).message, "ok")

    def test_handler_exception_reaches_cli_boundary(self):
        self.router.register(Command("broken", "Test", Mock(side_effect=RuntimeError("boom"))))
        with self.assertRaises(RuntimeError):
            self.router.dispatch("broken")

    def test_invalid_result_rejected(self):
        self.router.register(Command("broken", "Test", lambda: None))
        with self.assertRaises(TypeError):
            self.router.dispatch("broken")
