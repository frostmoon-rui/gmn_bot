"""Isolated paid-key access for explicit research planning and Argon questions."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from local_settings import setting

ALLOWED_MODELS = {'gemini-4-argon', 'gemini-3.8-flash'}
ALLOWED_PURPOSES = {'research-plan', 'argon-answer'}


def reserve_paid_call(path, limit):
    """Count attempts before sending, even when an upstream response later fails."""
    if limit <= 0:
        return False
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS daily_calls (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)')
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT calls FROM daily_calls WHERE day=?', (day,)).fetchone()
        count = row[0] if row else 0
        if count >= limit:
            return False
        connection.execute('INSERT INTO daily_calls(day,calls) VALUES (?,?) '
                           'ON CONFLICT(day) DO UPDATE SET calls=excluded.calls', (day, count + 1))
        return True


async def generate_paid(bot, model, prompt, config, purpose):
    if purpose not in ALLOWED_PURPOSES or model not in ALLOWED_MODELS:
        raise ValueError('Paid Gemini request outside permitted scope')
    if purpose == 'argon-answer' and model != 'gemini-4-argon':
        raise ValueError('Direct Argon questions cannot substitute Flash')
    if not getattr(bot, 'RESEARCH_PAID_ENABLED', False):
        raise RuntimeError('Paid research integration disabled')
    key = setting('GEMINI_RESEARCH_PAID_API_KEY', '').strip()
    if not key:
        raise RuntimeError('Paid research key not configured')
    limit = max(0, min(20, int(setting('GEMINI_PAID_DAILY_CALL_LIMIT', 4))))
    usage = Path(__file__).with_name('paid_gemini_usage.sqlite3')
    if not await asyncio.to_thread(reserve_paid_call, usage, limit):
        print('[Paid Gemini]: Daily attempt allowance exhausted; no API request sent')
        raise RuntimeError('Paid Gemini daily attempt limit reached')
    client = getattr(bot, '_paid_research_client', None)
    if client is None:
        from google import genai
        client = genai.Client(api_key=key)
        bot._paid_research_client = client
    options = dict(config, tools=[], max_output_tokens=min(config.get('max_output_tokens', 4096), 4096),
                   http_options={'timeout': 600000, 'retry_options': {'attempts': 1}})
    print('[Paid Gemini]: Reserved one daily attempt;', purpose, model)
    return await asyncio.wait_for(client.aio.models.generate_content(
        model=model, contents=prompt[:24000], config=bot.gemini_content_config(**options)), timeout=600)
