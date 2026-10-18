"""Private local configuration; environment variables take precedence."""
import json
import os
from pathlib import Path


def _load():
    path = Path(__file__).resolve().with_name('local_settings.json')
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('configuration must be an object')
        return data
    except (OSError, ValueError) as error:
        raise RuntimeError('Cannot read local_settings.json; repair the configuration. The original file was not modified.') from error


_VALUES = _load()


def setting(name, default=''):
    return os.environ.get(name, _VALUES.get(name, default))
