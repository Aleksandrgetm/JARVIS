"""CLI entrypoints only; no service registration on import."""
import argparse
from pathlib import Path
import platform
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.launch_agent import LaunchAgent, ServiceError


def run(command=None):
    parser = argparse.ArgumentParser(description='Manage only com.jarvis.assistant for the current user')
    if command is None:
        parser.add_argument('command', choices=('status', 'start', 'stop', 'restart', 'logs'))
    args = parser.parse_args()
    command = command or args.command
    try:
        if platform.system() != 'Darwin':
            raise ServiceError('LaunchAgent management requires macOS.')
        agent = LaunchAgent()
        result = getattr(agent, command)()
        print(result if result is not None else 'JARVIS service: ' + command + ' completed.')
        return 0
    except (ServiceError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
