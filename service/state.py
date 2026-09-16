"""Atomic technical runtime state, not conversation memory."""
import json
import os
from pathlib import Path
import tempfile


def read_json(path):
    try:
        if path.stat().st_size > 65536:
            return {}
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
