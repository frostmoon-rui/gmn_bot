"""Parse explicit music commands separately from song titles and media links."""
import re
from unicodedata import normalize

ENGLISH = {
    'play music': 'search', 'kugou music': 'kugou', 'netease music': 'netease',
    'get track': 'select', 'download audio': 'download',
}


def parse_music_command(text, aliases=None):
    text = re.sub(r'[\u200b-\u200f\u2060\ufeff]', '', text).strip()
    slash = text.startswith(('/', '／'))
    if slash:
        text = text[1:].lstrip()
    commands = ENGLISH | (aliases or {})
    for name in sorted(commands, key=len, reverse=True):
        phrase = r'[ \t\u3000]+'.join(re.escape(word) for word in name.split())
        # Chinese commands support compact form; English needs a boundary after its verb.
        boundary = r'(?=$|[\s:：,，。!！?？])' if name.isascii() else ''
        match = re.match(phrase + boundary, text, re.I)
        if match:
            value = text[match.end():].lstrip(' \t\r\n\u3000:：,，')
            if re.fullmatch(r'[。.!！?？]*', value):
                value = ''
            if '\n' in value or '\r' in value:
                return None
            return slash, commands[name], value
    return None


def music_selection(value, file_word='file'):
    value = normalize('NFKC', value).strip().rstrip('。.!！?？').strip()
    match = re.fullmatch(
        r'(?:[#第]\s*)?([1-3])(?:\s*号)?(?:[\s,，:：]*(' + re.escape(file_word) + r'))?', value, re.I)
    return (int(match[1]), bool(match[2])) if match else None
