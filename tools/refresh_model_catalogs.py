"""Explicit read-only account catalog refresh; no generation, bot imports or paid fallback."""
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_settings import setting
from model_catalogs import ROOT, fetch_catalog

PROVIDERS = {
    'gemini': ('GEMINI_API_KEY', 'GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta'),
    'openai': ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'https://api.openai.com/v1'),
    'claude': ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'https://api.anthropic.com'),
    'deepseek': ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1'),
    'mimo': ('MIMO_API_KEY', 'MIMO_BASE_URL', 'https://api.xiaomimimo.com/v1')}


def refresh(provider):
    key_name, base_name, default = PROVIDERS[provider]
    key = setting(key_name, '')
    base = str(setting(base_name, default)).rstrip('/')
    proxy = str(setting('TARGET_PROXY', 'http://127.0.0.1:7897'))
    path = ROOT / (provider + '.json')
    try:
        previous = json.loads(path.read_text(encoding='utf-8')).get('models', [])
    except (OSError, ValueError):
        previous = []
    result = {'provider': provider, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'base': base, 'generation_tested': False}
    try:
        if not key:
            raise ValueError('Key not configured')
        names = fetch_catalog(provider, key, base, proxy)
        if not names:
            raise ValueError('Empty text catalog')
        result.update(status='refreshed', models=names, added=[name for name in names if name not in previous])
        ROOT.mkdir(exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    except Exception as error:
        result.update(status='unavailable', error=getattr(error, 'code', None) or type(error).__name__,
                      cached_models_preserved=len(previous))
    print(provider, result['status'], len(result.get('models', previous)), 'text candidates;',
          'added:', ', '.join(result.get('added', [])) or 'none', flush=True)
    return result


def main():
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(refresh, PROVIDERS))
    ROOT.mkdir(exist_ok=True)
    (ROOT / 'catalog_refresh_report.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    return 0 if all(result['status'] == 'refreshed' for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
