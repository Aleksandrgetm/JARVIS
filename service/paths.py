from dataclasses import dataclass, field
from pathlib import Path

LABEL = 'com.jarvis.assistant'


@dataclass(frozen=True)
class ServicePaths:
    home: Path = field(default_factory=Path.home)

    @property
    def config(self):
        return self.home / '.config/jarvis/config.json'

    @property
    def logs(self):
        return self.home / 'Library/Logs/JARVIS'

    @property
    def runtime(self):
        return self.home / 'Library/Application Support/JARVIS'

    @property
    def plist(self):
        return self.home / 'Library/LaunchAgents' / (LABEL + '.plist')

    @property
    def lock(self):
        return self.runtime / 'voice.lock'
