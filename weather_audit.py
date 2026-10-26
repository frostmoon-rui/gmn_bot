"""Task-local weather response capture; secrets never belong in exported diagnostics."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import json
import math
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_calls = ContextVar('weather_response_audit', default=None)
_secret = re.compile(r'^(?:key|api[_-]?key|authorization|password|.*token.*|.*secret.*|signature)$', re.I)


@lru_cache(maxsize=1)
def configured_secrets():
    from local_settings import setting
    names = ('GEMINI_API_KEY', 'GEMINI_RESEARCH_PAID_API_KEY', 'GOOGLE_MAPS_API_KEY', 'AMAP_API_KEY',
             'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY', 'MIMO_API_KEY',
             'MOJI_WEATHER_BRIDGE_TOKEN', 'MSN_WEATHER_BRIDGE_TOKEN', 'AMAP_WEB_KEY')
    return [value for name in names if isinstance(value := setting(name, ''), str) and len(value) > 6]


def redact(value):
    if isinstance(value, float) and not math.isfinite(value):
        return '[non-finite]'
    if isinstance(value, dict):
        return {str(key): '[REDACTED]' if _secret.match(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for secret in configured_secrets():
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?:AIza[\w-]{20,}|AQ\.[\w-]{20,}|sk-[\w-]{20,}|Bearer\s+[^\s"<>]+)', '[REDACTED]', value)
        value = re.sub(r'([?&](?:key|api_key|token|access_token)=)[^&\s"<>]+', r'\1[REDACTED]', value, flags=re.I)
    return value


def safe_endpoint(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path,
                       urlencode([(key, '[REDACTED]' if _secret.match(key) else value)
                                  for key, value in parse_qsl(parsed.query)]), ''))


@contextmanager
def capture_calls():
    records = []
    token = _calls.set(records)
    try:
        yield records
    finally:
        _calls.reset(token)


def record_response(endpoint, data, status=200):
    records = _calls.get()
    if records is not None:
        records.append({'endpoint': safe_endpoint(endpoint), 'status': status, 'response': redact(data)})


def error_code(error):
    code = getattr(error, 'code', None)
    return 'HTTP ' + str(code) if isinstance(code, int) else type(error).__name__


def write_diagnostic(path, data):
    """An explicit diagnostic export, never a public-source asset."""
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return path
