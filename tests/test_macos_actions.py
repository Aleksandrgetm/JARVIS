from datetime import datetime
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from actions.apps import open_app
from actions.browser import normalize_url, open_url
from actions.filesystem import resolve_folder
from actions.macos import ActionError, MacOSRunner
from actions.system import screenshot_path, take_screenshot, validate_volume
from core.assistant import Assistant
from core.config import Config
from core.permissions import PermissionLevel
from core.router import CommandInputError


class MacOSActionTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.runner = MacOSRunner(self.logger)
        self.platform_patch = patch('actions.macos.platform.system', return_value='Darwin')
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)
        self.process_patch = patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
        self.process = self.process_patch.start()
        self.addCleanup(self.process_patch.stop)
        self.assistant = Assistant(Config(), self.logger)
        self.router = self.assistant.router

    def test_application_parsing_preserves_case_and_spaces(self):
        for text in ('open app Visual Studio Code', 'OPEN APP "Visual Studio Code"',
                     "open app 'Visual Studio Code'"):
            with self.subTest(text=text):
                result = self.router.dispatch(text)
                self.assertEqual(result.message, 'Opening Visual Studio Code.')
                self.assertEqual(self.process.call_args.args[0], ['/usr/bin/open', '-a', 'Visual Studio Code'])
                self.assertIs(self.process.call_args.kwargs['shell'], False)

    def test_app_option_injection_rejected(self):
        for value in ('--args', '/Applications/Safari.app', 'file:Safari'):
            with self.subTest(value=value), self.assertRaises(CommandInputError):
                open_app(self.runner, value)
        self.process.assert_not_called()

    def test_shell_syntax_is_only_literal_app_name(self):
        self.router.dispatch('open app "Safari; whoami"')
        self.assertEqual(self.process.call_args.args[0], ['/usr/bin/open', '-a', 'Safari; whoami'])

    def test_missing_app_is_a_handled_action_error(self):
        self.process.return_value.returncode = 1
        with self.assertRaisesRegex(ActionError, 'Application not found'):
            self.router.dispatch('open app MissingApplication')

    def test_url_normalization(self):
        for value, expected in (('youtube.com', 'https://youtube.com'),
                                ('https://github.com/a?x=1&b=2', 'https://github.com/a?x=1&b=2'),
                                ('HTTP://example.com', 'http://example.com'),
                                ('http://localhost:8080', 'http://localhost:8080'),
                                ('https://[::1]/', 'https://[::1]/')):
            with self.subTest(value=value):
                self.assertEqual(normalize_url(value), expected)

    def test_invalid_urls_never_open(self):
        values = ('', 'javascript:alert(1)', 'file:///tmp/a', 'ftp://example.com',
                  'https://', 'https:///example.com', 'https://a b.com',
                  'https://user:secret@example.com', 'https://example.com:99999',
                  'https://[broken', 'https://-bad.com', '//example.com',
                  'https://example.com\\evil', 'https://example.com\n')
        for value in values:
            with self.subTest(value=value), self.assertRaises(CommandInputError):
                open_url(self.runner, value)
        self.process.assert_not_called()

    def test_url_open_uses_single_argument_and_does_not_log_query(self):
        self.router.dispatch('open url "https://example.com/?token=private&x=1"')
        self.assertEqual(self.process.call_args.args[0],
                         ['/usr/bin/open', 'https://example.com/?token=private&x=1'])
        self.assertNotIn('private', str(self.logger.mock_calls))

    def test_folder_aliases_tilde_spaces_and_absolute_paths(self):
        self.assertEqual(resolve_folder('~'), str(Path.home().resolve()))
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            for name in ('Desktop', 'Downloads', 'Documents', 'Desktop/Projects', 'Folder With Spaces'):
                (home / name).mkdir(parents=True, exist_ok=True)
            with patch('actions.filesystem.Path.home', return_value=home):
                for alias in ('Desktop', 'Downloads', 'Documents'):
                    self.assertEqual(resolve_folder(alias), str(home / alias))
                self.assertEqual(resolve_folder('Projects'), str(home / 'Desktop/Projects'))
                for argument in (str(home / 'Folder With Spaces'), '"' + str(home / 'Folder With Spaces') + '"'):
                    self.router.dispatch('open folder ' + argument)
                    self.assertEqual(self.process.call_args.args[0], ['/usr/bin/open', str(home / 'Folder With Spaces')])

    def test_nonexistent_folder_and_regular_file(self):
        with TemporaryDirectory() as temporary:
            file = Path(temporary) / 'file'
            file.touch()
            for value in (str(file), str(file / 'missing')):
                with self.subTest(value=value), self.assertRaises(CommandInputError):
                    self.router.dispatch('open folder ' + value)
        self.process.assert_not_called()

    def test_invalid_commands_validate_before_confirmation(self):
        confirm = Mock(return_value=True)
        for text in ('open app', 'open url', 'open folder', 'volume', 'volume abc',
                     'volume -10', 'volume 101', 'volume 1.5', 'volume 30;whoami',
                     'open app "unclosed', 'open app Safari\x00'):
            with self.subTest(text=text), self.assertRaises(CommandInputError):
                self.router.dispatch(text, confirm=confirm)
        confirm.assert_not_called()
        self.process.assert_not_called()

    def test_volume_boundaries_and_fixed_script(self):
        for value in ('0', '30', '100'):
            self.assertEqual(validate_volume(value), value)
            self.router.dispatch('volume ' + value, confirmed=True)
            self.assertEqual(self.process.call_args.args[0],
                             ['/usr/bin/osascript', '-e', 'set volume output volume ' + value])

    def test_permission_classification(self):
        confirm_names = {'volume', 'mute', 'unmute', 'screenshot'}
        for command in self.router.commands:
            expected = PermissionLevel.CONFIRM if command.name in confirm_names else PermissionLevel.SAFE
            self.assertIs(command.permission, expected)

    def test_confirm_actions_cannot_run_without_permission(self):
        for text in ('volume 30', 'mute', 'unmute', 'screenshot'):
            with self.subTest(text=text), self.assertRaises(PermissionError):
                self.router.dispatch(text)
        self.process.assert_not_called()

    def test_confirmation_and_cancellation_for_each_action(self):
        for text in ('volume 30', 'mute', 'unmute', 'screenshot'):
            result = self.router.dispatch(text, confirm=Mock(return_value=False))
            self.assertEqual(result.message, 'Cancelled.')
        self.process.assert_not_called()
        callback = Mock(return_value=True)
        self.router.dispatch('volume 30', confirm=callback)
        callback.assert_called_once_with('Set volume to 30%? [y/N] ')
        for text, state in (('mute', 'true'), ('unmute', 'false')):
            self.router.dispatch(text, confirm=Mock(return_value=True))
            self.assertEqual(self.process.call_args.args[0],
                             ['/usr/bin/osascript', '-e', 'set volume output muted ' + state])

    def test_native_errors_are_sanitized(self):
        for failure in (subprocess.TimeoutExpired(['private'], 15), FileNotFoundError('private')):
            self.process.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(ActionError) as caught:
                self.router.dispatch('open url https://example.com/?secret=private')
            self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('private', str(self.logger.mock_calls))

    def test_non_macos_is_explicitly_rejected(self):
        with patch('actions.macos.platform.system', return_value='Linux'):
            with self.assertRaisesRegex(ActionError, 'requires macOS'):
                self.router.dispatch('system info')
        self.process.assert_not_called()

    def test_system_info_has_only_expected_fields(self):
        result = self.router.dispatch('system info')
        self.assertEqual([line.split(':')[0] for line in result.message.splitlines()],
                         ['macOS version', 'Architecture', 'Hostname', 'Python version'])
        self.process.assert_not_called()

    def test_help_contains_both_stages(self):
        text = self.router.dispatch('help').message
        for value in ('SYSTEM', 'MACOS', 'status', 'open app <name>', 'volume <0-100>', 'system info'):
            self.assertIn(value, text)

    def test_screenshot_name_and_success(self):
        with TemporaryDirectory() as temporary, patch('actions.system.datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 16, 12, 34, 56)
            directory = Path(temporary).resolve() / 'screenshots'
            expected = directory / 'screenshot_2026-09-16_12-34-56.png'
            self.assertEqual(screenshot_path(directory), expected)
            def capture(arguments, **kwargs):
                Path(arguments[-1]).write_bytes(b'fake png for unit test')
                return Mock(returncode=0)
            self.process.side_effect = capture
            result = take_screenshot(self.runner, directory)
            self.assertIn(str(expected), result.message)
            self.assertTrue(expected.is_file())
            self.assertEqual(self.process.call_args.args[0],
                             ['/usr/sbin/screencapture', '-x', '-m', '-t', 'png', str(expected)])
            self.process.reset_mock()
            with self.assertRaisesRegex(ActionError, 'already exists'):
                take_screenshot(self.runner, directory)
            self.process.assert_not_called()
            self.assertEqual(expected.read_bytes(), b'fake png for unit test')

    def test_screenshot_without_file_or_with_process_failure(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for code in (0, 1):
                self.process.return_value.returncode = code
                with self.subTest(code=code), self.assertRaises(ActionError):
                    take_screenshot(self.runner, directory)
                self.assertEqual(list(directory.iterdir()), [])

    def test_screenshot_missing_output_is_not_reported_as_success(self):
        with TemporaryDirectory() as temporary:
            def capture(arguments, **kwargs):
                Path(arguments[-1]).unlink()
                return Mock(returncode=0)
            self.process.side_effect = capture
            with self.assertRaisesRegex(ActionError, 'was not created'):
                take_screenshot(self.runner, Path(temporary))

    def test_screenshot_failure_removes_partial_output(self):
        with TemporaryDirectory() as temporary:
            def capture(arguments, **kwargs):
                Path(arguments[-1]).write_bytes(b'partial output')
                return Mock(returncode=1)
            self.process.side_effect = capture
            with self.assertRaises(ActionError):
                take_screenshot(self.runner, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])


class ConfirmationCLITests(unittest.TestCase):
    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=0))
    def test_only_yes_answers_execute(self, process, platform_mock):
        for answer in ('y', 'yes', 'Y', ' YES ', '', 'n', 'no', 'sure'):
            with self.subTest(answer=answer):
                output = []
                reader = Mock(side_effect=['volume 30', answer, 'exit'])
                Assistant(Config(), Mock(), reader=reader, writer=output.append).run()
                self.assertEqual(process.call_count, int(answer.strip().lower() in ('y', 'yes')))
                self.assertIn('System offline.', output)
                process.reset_mock()

    @patch('actions.macos.subprocess.run')
    def test_interrupt_and_eof_during_confirmation(self, process):
        for exception in (KeyboardInterrupt(), EOFError()):
            output = []
            Assistant(Config(), Mock(), reader=Mock(side_effect=['screenshot', exception]),
                      writer=output.append).run()
            self.assertIn('\nSystem offline.', output)
        process.assert_not_called()

    @patch('actions.macos.platform.system', return_value='Darwin')
    @patch('actions.macos.subprocess.run', return_value=Mock(returncode=1))
    def test_action_failure_and_invalid_input_do_not_stop_cli(self, process, platform_mock):
        output = []
        logger = Mock()
        Assistant(Config(), logger, reader=Mock(side_effect=[
            'open url javascript:alert(1)', 'open app Missing', 'status', 'exit']),
            writer=output.append).run()
        self.assertTrue(any('Invalid URL' in line for line in output))
        self.assertTrue(any('Application not found' in line for line in output))
        self.assertIn('JARVIS is online.', output)
        logger.exception.assert_not_called()
