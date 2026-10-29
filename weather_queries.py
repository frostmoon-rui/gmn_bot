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
