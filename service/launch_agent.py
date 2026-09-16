"""Fixed-label LaunchAgent management; all launchctl arguments are application-owned."""
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time
from service.instance import is_locked

from core.settings import DEFAULT_SETTINGS
from service.paths import LABEL, ServicePaths
from service.state import atomic_write, read_json, write_json

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ServiceError(RuntimeError):
    pass


def generate_plist(project_root, python_executable, paths=None):
    paths = paths or ServicePaths()
    project = Path(project_root).resolve()
    # Do NOT resolve the executable symlink: that would escape a venv.
    python = Path(os.path.abspath(python_executable))
    if not python.is_file() or not (project / 'main.py').is_file():
        raise ServiceError('Python executable or project main.py not found.')
    return {
        'Label': LABEL,
        'ProgramArguments': [str(python), '-u', str(project / 'main.py'), '--daemon'],
        'WorkingDirectory': str(project),
        'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False, 'Crashed': True},
        'ThrottleInterval': 60,
        'ExitTimeOut': 10,
        'LimitLoadToSessionType': 'Aqua',
        'ProcessType': 'Background',
        'Umask': 0o077,
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'JARVIS_SERVICE_MANAGED': '1'},
        # Capture interpreter/argparse/import failures before Python logging starts.
        'StandardInPath': '/dev/null',
        'StandardOutPath': str(paths.logs.absolute() / 'launchd-stdout.log'),
        'StandardErrorPath': str(paths.logs.absolute() / 'launchd-stderr.log'),
    }


def check_managed(path):
    if path.is_symlink():
        raise ServiceError('Refusing to replace/remove a symlink LaunchAgent.')
    if not path.exists():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
        if data.get('Label') != LABEL or data.get('EnvironmentVariables', {}).get('JARVIS_SERVICE_MANAGED') != '1':
            raise ValueError()
        return data
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException):
        raise ServiceError('Existing plist is not managed by JARVIS; it was left untouched.') from None


class LaunchAgent:
    def __init__(self, paths=None, uid=None, runner=None):
        self.paths = paths or ServicePaths()
        self.uid = os.getuid() if uid is None else uid
        self.runner = runner or subprocess.run
        if self.uid == 0:
            raise ServiceError('Run as your normal macOS user, without sudo.')
        self.domain = f'gui/{self.uid}'
        self.target = self.domain + '/' + LABEL

    def _call(self, *args):
        try:
            return self.runner(['/bin/launchctl', *args], capture_output=True, text=True,
                               shell=False, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise ServiceError('launchctl unavailable or timed out; retry status from your logged-in macOS session.') from None

    def _require(self, *args):
        result = self._call(*args)
        if result.returncode:
            raise ServiceError(f'launchctl {args[0]} failed (code {result.returncode}). '
                               'Check status and macOS Login Items; no root privileges are needed.')
        return result

    def inspect(self):
        result = self._call('print', self.target)
        if result.returncode:
            # ESRCH is normal for an unregistered service; do not treat permission failures as absent.
            if result.returncode in (3, 113):
                return None
            raise ServiceError(f'Cannot inspect LaunchAgent (launchctl code {result.returncode}).')
        return result.stdout

    def start(self):
        if check_managed(self.paths.plist) is None:
            raise ServiceError('LaunchAgent is not installed. Run scripts/install_launch_agent.py.')
        self._require('enable', self.target)
        current = self.inspect()
        if current is None:
            self._require('bootstrap', self.domain, str(self.paths.plist))
        elif re.search(r'^\s*pid = \d+', current, re.M) is None:
            self._require('kickstart', self.target)

    def stop(self):
        if self.inspect() is not None:
            self._require('bootout', self.target)
            deadline = time.monotonic() + 10
            while is_locked(self.paths.lock):
                if time.monotonic() >= deadline:
                    raise ServiceError('JARVIS voice lock is still held. Check logs/manual voice before restarting.')
                time.sleep(0.1)

    def restart(self):
        # bootout permits SIGTERM cleanup; do not kill the old process with kickstart -k.
        if check_managed(self.paths.plist) is None:
            raise ServiceError('LaunchAgent is not installed.')
        self.stop()
        self.start()

    def install(self, project_root=PROJECT_ROOT, python_executable=None):
        data = generate_plist(project_root, python_executable or sys.executable, self.paths)
        old = check_managed(self.paths.plist)
        self.paths.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.paths.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Preserve user configuration, including edits, on every subsequent install.
        if not self.paths.config.exists():
            write_json(self.paths.config, DEFAULT_SETTINGS)
        if old != data:
            if old is not None:
                self.stop()
            atomic_write(self.paths.plist, plistlib.dumps(data), mode=0o600)
        self.start()

    def uninstall(self):
        check_managed(self.paths.plist)
        self.stop()
        if self.paths.plist.exists():
            self.paths.plist.unlink()

    def status(self):
        raw = self.inspect()
        pid = re.search(r'^\s*pid = (\d+)', raw or '', re.M)
        lines = ['JARVIS Service', 'Status: ' + ('running' if pid else 'stopped' if raw else 'not registered')]
        if pid:
            lines.append('PID: ' + pid[1])
            health = read_json(self.paths.runtime / 'health.json')
            if health.get('pid') == int(pid[1]) and health.get('status') == 'RUNNING':
                for key in ('core', 'tts', 'stt', 'ai', 'ollama', 'model'):
                    lines.append(key.upper() + ': ' + str(health.get(key, 'UNKNOWN')))
            else:
                lines.append('Health: initializing or stale; check logs.')
        last_exit = re.search(r'^\s*last exit code = (.+)', raw or '', re.M)
        if last_exit:
            lines.append('Last exit: ' + last_exit[1])
        return '\n'.join(lines)

    def logs(self):
        # Snapshot, no shell/tail process and no unbounded read.
        sections = []
        for name in ('jarvis.log', 'jarvis-error.log', 'launchd-stdout.log', 'launchd-stderr.log'):
            path = self.paths.logs / name
            try:
                with path.open('rb') as handle:
                    handle.seek(0, 2)
                    handle.seek(max(0, handle.tell() - 16384))
                    text = handle.read().decode('utf-8', errors='replace') or '(empty file)'
            except FileNotFoundError:
                text = '(no log yet)'
            sections.append(str(path) + '\n' + text)
        return '\n\n'.join(sections)
