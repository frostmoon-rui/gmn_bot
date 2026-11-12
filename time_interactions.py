"""Short time-aware greetings and ephemeral, self-reported sleep state."""
import asyncio
import re

from response_visibility import visible_response_text


async def model_greeting(client, config, models, now, period, language, fallback):
    if not client or not config or not models:
        return fallback
    phase = ('late night', 'morning', 'midday', 'afternoon', 'evening')[period]
    try:
        response = await asyncio.wait_for(client.aio.models.generate_content(
            model=models[0],
            contents=f'Beijing time is {now:%H:%M}. The current period is {phase}. '
                     f'Write one short friendly cat greeting for this period in language {language}. '
                     'Use a single line of plain text with no tags or mentions.',
            config=config(tools=[], max_output_tokens=300, temperature=0.6)), timeout=5)
        text = visible_response_text(response).strip()
        wrong_morning = period != 1 and re.search(
            r'^(?:good morning\b|\u65e9\u5b89|\u65e9\u4e0a\u597d|\u304a\u306f\u3088\u3046|\u0434\u043e\u0431\u0440\u043e\u0435 \u0443\u0442\u0440\u043e\b)', text, re.I)
        if 1 <= len(text) <= 80 and not any(char in text for char in '[]<>@\r\n') and not wrong_morning:
            return text
    except Exception as error:
        print('[Time greeting fallback]:', type(error).__name__)
    return fallback


def sleep_update(features, group, user, action, now):
    records = getattr(features, 'sleep_records', None)
    if records is None:
        records = features.sleep_records = {}
    for key, recorded_at in list(records.items()):
        if now - recorded_at >= 12 * 3600:
            del records[key]
    key = (group, user)
    if action == 0:
        records.pop(key, None)
        records[key] = now
        if len(records) > 512:
            records.pop(next(iter(records)))
    elif action == 1:
        records.pop(key, None)
    return sum(other_group == group for other_group, _ in records)
