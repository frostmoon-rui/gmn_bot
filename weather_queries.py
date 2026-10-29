"""Explicit child*parent weather selectors, without changing saved location records."""
import re
import unicodedata


def weather_query(value):
    if not isinstance(value, str):
        raise ValueError('Weather location must be text')
    value = unicodedata.normalize('NFKC', value).strip()
    if not value or any(ord(c) < 32 for c in value):
        raise ValueError('Weather location is empty or malformed')
    if '*' not in value:
        if '|' in value and (value.count('|') != 1 or not all(part.strip() for part in value.split('|'))):
            raise ValueError('Use one parent|child weather selector')
        return value
    parts = [part.strip() for part in value.split('*')]
    if len(parts) != 2 or not all(parts) or any('|' in part for part in parts):
        raise ValueError('Use one child*parent weather selector')
    child, parent = parts
    return parent + '|' + child


def topic_request(text, aliases=None, local_aliases=None, prefixes=()):
    """Retain bare national commands; explicit places or local prefixes select city charts."""
    from weather_maps import ALIASES
    if not isinstance(text, str):
        return None
    value = unicodedata.normalize('NFKC', text).strip().removeprefix('/').strip()
    local = False
    for prefix in ('local ', *prefixes):
        if value.casefold().startswith(prefix.casefold()):
            value = value[len(prefix):].strip()
            local = True
            break
    local_only = {'temperature forecast': 'temperature_forecast', 'precipitation forecast': 'precipitation_forecast', **(local_aliases or {})}
    names = {**ALIASES, **(aliases or {}), **local_only}
    for name in sorted(names, key=len, reverse=True):
        if not value.casefold().startswith(name.casefold()):
            continue
        tail = value[len(name):]
        if tail and not (tail[0].isspace() or tail[0] == ':'):
            continue
        location = tail.strip().removeprefix(':').strip()
        if len(location) > 120 or any(ord(c) < 32 for c in location):
            return None
        return {'product': names[name], 'scope': 'local' if local or location or name in local_only else 'national',
                'location': location}
    return None
