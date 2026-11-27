"""Recognize a city sent as a direct reply to the bot's binding prompt."""
import re
from bot_features import mention_targets
from unicodedata import normalize


def city_from_binding_reply(text, quote, actor, bot, kind, prompts):
    if not quote or bot is None or str(quote.get('user_id')) != str(bot):
        return ''
    raw = quote.get('raw_message', '')
    if not isinstance(raw, str) or not any(prompt in raw for prompt in prompts):
        return ''
    if kind == 'group' and str(actor) not in mention_targets(raw):
        return ''
    value = normalize('NFKC', text).strip().rstrip('。.!！?？').strip()
    if not 2 <= len(value) <= 80:
        return ''
    if not all(c.isalpha() or c in " ,，-'·*|" for c in value):
        return ''
    return value
