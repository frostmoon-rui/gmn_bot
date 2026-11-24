"""Keep passive chat brief without silently chopping off ordinary sentences."""
import re

from response_visibility import strip_internal_tags


SENTENCE_END = re.compile(r"""(?:[。！？]|[.!?](?=[\s”’"）)]|\[SPLIT\]|$))[”’"）)]*""")


def prepare_passive_reply(text, limit=120):
    text=re.sub(r'<(?:think|analysis)>.*?</(?:think|analysis)>','',str(text),flags=re.I|re.S)
    text=re.sub(r'<(?:think|analysis)>.*$','',text,flags=re.I|re.S)
    text = strip_internal_tags(text)
    text='[SPLIT]'.join(part.strip() for part in text.split('[SPLIT]') if part.strip())
    if len(text)<=limit:
        return text
    ends = [match.end() for match in SENTENCE_END.finditer(text)]
    before=[end for end in ends if end<=limit]
    if before:
        return text[:before[-1]]
    after=[end for end in ends if end<=limit*2]
    if after:
        return text[:after[0]]
    if len(text)<=limit*2:
        return text
    return text[:limit].rstrip()+'…'


def compact_interjection(text, limit, fallback):
    """Produce a single plain reaction with a strict character budget."""
    text = prepare_passive_reply(text).split('[SPLIT]', 1)[0].splitlines()
    line = text[0].strip() if text else ''
    if not line:
        return ''
    if len(line) <= limit and '@' not in line:
        return line
    match = SENTENCE_END.search(line)
    first = line[:match.end()] if match else ''
    return first if 0 < len(first) <= limit and '@' not in first else fallback


def generation_hit_limit(response):
    if isinstance(response, dict):
        choices = response.get('choices') or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        details = response.get('incomplete_details') or {}
        return (first.get('finish_reason') == 'length'
                or response.get('stop_reason') == 'max_tokens'
                or details.get('reason') == 'max_output_tokens')
    candidates = getattr(response, 'candidates', None) or []
    reason = getattr(candidates[0], 'finish_reason', None) if candidates else None
    return str(getattr(reason, 'value', reason) or '').upper().split('.')[-1] == 'MAX_TOKENS'
