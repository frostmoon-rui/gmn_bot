"""Official account catalogs: discovery is not a generation availability claim."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent / 'model_catalogs_cache'
EXCLUDE = ('image', 'banana', 'audio', 'asr', 'tts', 'transcri', 'live', 'realtime', 'embedding',
           'moderation', 'search', 'robotics', 'omni', 'veo', 'antigravity', 'codex', 'instruct', 'customtools', 'computer')
# Official OpenAI model directory marks these families shut down (checked 2026-10-28).
# Do not apply official retirement policy to a user-selected compatible endpoint.
OPENAI_RETIRED = re.compile(r'^(?:gpt-3\.5(?:-|$)|gpt-4(?:-|$)|gpt-4\.1-nano(?:-|$)|'
                           r'o1(?:-|$)|o3-mini(?:-|$)|o4-mini(?:-|$)|gpt-5(?:\.[123])?-chat(?:-|$)|chatgpt-4o(?:-|$))')


def account_blocked(status, detail):
    return status in {401, 402} or (status == 429 and any(word in detail.lower() for word in
        ('credit_balance_exhausted', 'insufficient_quota', 'no credits remaining')))


def eligible(provider, item, official=True):
    name = str(item.get('name' if provider == 'gemini' else 'id', '')).removeprefix('models/')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', name) or any(term in name.lower() for term in EXCLUDE):
        return None
    if str(item.get('lifecycle', '')).lower() in {'retired', 'shutdown', 'shut_down', 'deleted'}:
        return None
    patterns = {'gemini': r'^gemini-.*(?:flash|lite|pro|argon)', 'openai': r'^(?:gpt-|chatgpt|o\d)',
                'claude': r'^claude-', 'deepseek': r'^deepseek-', 'mimo': r'^mimo-'}
    if not re.search(patterns[provider], name):
        return None
    if provider == 'gemini' and 'generateContent' not in item.get('supportedGenerationMethods', ['generateContent']):
        return None
    if provider == 'openai' and official and OPENAI_RETIRED.match(name):
        return None
    return name


def model_rank(provider, name):
    version = re.search(r'(?:gpt-|gemini-|mimo-v)(\d+)(?:\.(\d+))?', name)
    if provider == 'claude':
        version = re.search(r'claude-(?:opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2})(?!\d))?', name)
    major, minor = (int(version[1]), int(version[2] or 0)) if version else (0, 0)
    family = 8 if any(term in name for term in ('astra', 'opus', 'pro', 'argon', 'reasoner')) else \
             6 if any(term in name for term in ('sol', 'sonnet')) else \
             5 if any(term in name for term in ('terra', 'fable')) else 4
    if any(term in name for term in ('mini', 'nano', 'lite', 'haiku', 'luna')):
        family = 1
    alias = not bool(re.search(r'-20\d{2}(?:-?\d{2}){2}$', name))
    # Same generation: the flagship family leads; lightweight variants remain fallbacks.
    return major, family, minor, 'preview' not in name, alias, 'ultraspeed' not in name, name


def fetch_catalog(provider, key, base, proxy='', reader=None):
    headers = {'Authorization': 'Bearer ' + key}
    path = '/models'
    if provider == 'gemini':
        headers = {'x-goog-api-key': key}
        path += '?pageSize=1000'
    elif provider == 'claude':
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        path = '/v1/models?limit=100'
    def read(url):
        request = urllib.request.Request(url, headers=dict(headers, **{'User-Agent': 'gmn-model-catalog/1.0'}))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy} if proxy else {}))
        with opener.open(request, timeout=12) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('catalog-too-large')
            return json.loads(raw)
    read = reader or read
    names, seen = set(), set()
    official = urllib.parse.urlsplit(base).hostname == 'api.openai.com'
    for _ in range(20):
        data = read(base.rstrip('/') + path)
        items = data.get('models' if provider == 'gemini' else 'data')
        if not isinstance(items, list):
            raise ValueError('invalid-catalog')
        names.update(name for item in items if isinstance(item, dict)
                     if (name := eligible(provider, item, official)))
        token = data.get('nextPageToken') if provider == 'gemini' else \
                data.get('last_id') if data.get('has_more') else None
        if not token:
            return sorted(names, key=lambda name: model_rank(provider, name), reverse=True)
        if token in seen:
            raise ValueError('repeated-catalog-page')
        seen.add(token)
        path = ('/models?pageSize=1000&pageToken=' if provider == 'gemini' else '/v1/models?limit=100&after_id=') + urllib.parse.quote(str(token), safe='')
    raise ValueError('catalog-page-limit')


def discover(provider, key, base, fallback, proxy='', cache_root=None):
    path = Path(cache_root or ROOT) / (provider + '.json')
    try:
        if not key:
            raise ValueError('key-not-configured')
        names = fetch_catalog(provider, key, base, proxy)
        if not names:
            raise ValueError('empty-text-catalog')
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'provider': provider,
                  'base': base, 'models': names, 'generation_tested': False}
        try:
            path.write_text(json.dumps(record, indent=2), encoding='utf-8')
        except OSError:
            pass
        print('[Model catalog]:', provider, len(names), 'text candidates; generation access is unverified')
        return prefer_verified(provider, key, base, names, cache_root)
    except Exception as error:
        print('[Model catalog fallback]:', provider, getattr(error, 'code', None) or type(error).__name__)
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if cached.get('base') == base and cached.get('models'):
                return prefer_verified(provider, key, base, cached['models'], cache_root)
        except (OSError, ValueError, TypeError):
            pass
        return list(dict.fromkeys(fallback))


def record_verified(provider, key, base, models, cache_root=None):
    """Explicit successful generation only; never blacklist failed candidates."""
    path = Path(cache_root or ROOT) / (provider + '-verified.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), 'base': base,
        'key_fingerprint': hashlib.sha256(key.encode()).hexdigest()[:16], 'models': list(dict.fromkeys(models))}), encoding='utf-8')


def prefer_verified(provider, key, base, names, cache_root=None):
    try:
        record = json.loads((Path(cache_root or ROOT) / (provider + '-verified.json')).read_text(encoding='utf-8'))
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(record['checked_at'])).total_seconds()
        if (record.get('base') == base and record.get('key_fingerprint') == hashlib.sha256(key.encode()).hexdigest()[:16]
                and 0 <= age <= 86400):
            successful = set(record['models'])
            return [name for name in names if name in successful] + [name for name in names if name not in successful]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return names
