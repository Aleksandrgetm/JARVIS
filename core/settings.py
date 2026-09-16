"""Small non-secret user configuration; environment remains the override layer."""
import json
import math
from pathlib import Path


class ConfigurationError(ValueError):
    pass


DEFAULT_SETTINGS = dict(ai_provider='ollama', ai_model='qwen3:8b',
                        ollama_url='http://127.0.0.1:11434', ai_timeout=30,
                        voice_end_silence=0.7, startup_greeting_delay=3, tts_voice='Milena', user_title='сэр')


def load_settings(path=None):
    path = Path(path) if path is not None else Path.home() / '.config/jarvis/config.json'
    try:
        if not path.exists():
            return {}
        if path.stat().st_size > 16384:
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) - set(DEFAULT_SETTINGS):
            raise ValueError()
        for key, value in data.items():
            if key in ('ai_timeout', 'voice_end_silence', 'startup_greeting_delay'):
                if key == 'ai_timeout':
                    low, high = (1, 60)
                elif key == 'voice_end_silence':
                    low, high = (0.6, 3)
                else:
                    low, high = (0, 30)
                if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError()
            elif not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
                raise ValueError()
        if 'user_title' in data and (len(data['user_title']) > 32 or
                not all(c.isalpha() or c in ' -' for c in data['user_title'])):
            raise ValueError()
        return data
    except (OSError, ValueError, TypeError):
        raise ConfigurationError('Invalid JARVIS config.json. Check documented fields and types; secrets belong in environment only.') from None
