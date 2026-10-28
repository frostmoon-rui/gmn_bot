"""Read public response text without reasoning parts or leaked style notes."""
import re


def visible_response_text(response):
    candidates = getattr(response, 'candidates', None) or []
    parts = getattr(getattr(candidates[0], 'content', None), 'parts', None) if candidates else None
    if parts and any(getattr(part, 'thought', False) for part in parts):
        text = ''.join(getattr(part, 'text', '') or '' for part in parts if not getattr(part, 'thought', False))
    else:
        text = getattr(response, 'text', '') or ''
    text = re.sub(r'<(?:think|analysis)>.*?</(?:think|analysis)>', '', text, flags=re.I | re.S)
    text = re.sub(r'<(?:think|analysis)>.*$', '', text, flags=re.I | re.S).strip()
    if re.search(r'\bvery short per bubble\b|\betc\.\s*\)\s*,?\s*friendly\s*\(', text, re.I):
        return ''
    return text
