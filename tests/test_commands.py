import unittest

from core.commands import register_system_commands
from core.config import Config
from core.permissions import PermissionLevel, PermissionManager
from core.router import Router


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.router = Router(PermissionManager())
        register_system_commands(self.router, Config())

    def test_help_lists_all_five_commands(self):
        result = self.router.dispatch("help")
        names = [line.split()[0] for line in result.message.splitlines()[1:]]
        self.assertEqual(names, ["help", "status", "version", "clear", "exit"])

    def test_status(self):
        self.assertEqual(self.router.dispatch("status").message, "JARVIS is online.")

    def test_version(self):
        self.assertEqual(self.router.dispatch("version").message, "JARVIS 0.1.0")

    def test_clear(self):
        result = self.router.dispatch("clear")
        self.assertTrue(result.should_clear)
        self.assertFalse(result.should_exit)

    def test_exit(self):
        result = self.router.dispatch("exit")
        self.assertTrue(result.should_exit)
        self.assertEqual(result.message, "System offline.")

    def test_all_commands_are_safe(self):
        self.assertTrue(all(c.permission is PermissionLevel.SAFE for c in self.router.commands))
