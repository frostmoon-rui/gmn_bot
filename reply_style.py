"""Short group replies with optional sources; The model decides when to retrieve information, Python only validates the execution plan."""
import html
import json
import re
import urllib.parse


# Exact provider wire labels and legacy values. Never rename upstream JSON keys.
PROVIDER_TERMS = {
    "\u6674": "clear",
    "\u6674\u6717": "clear",
    "\u591a\u4e91": "cloudy",
    "\u9634": "overcast",
    "\u9634\u5929": "overcast",
    "\u5c0f\u96e8": "light rain",
    "\u4e2d\u96e8": "moderate rain",
    "\u5927\u96e8": "heavy rain",
    "\u66b4\u96e8": "rainstorm",
    "\u5927\u66b4\u96e8": "heavy rainstorm",
    "\u7279\u5927\u66b4\u96e8": "extreme rainstorm",
    "\u96f7\u9635\u96e8": "thundershowers",
    "\u9635\u96e8": "showers",
    "\u96e8": "rain",
    "\u96ea": "snow",
    "\u96e8\u5939\u96ea": "sleet",
    "\u5c0f\u96ea": "light snow",
    "\u4e2d\u96ea": "moderate snow",
    "\u5927\u96ea": "heavy snow",
    "\u66b4\u96ea": "blizzard",
    "\u9635\u96ea": "snow showers",
    "\u96fe": "fog",
    "\u973e": "haze",
    "\u6c99\u5c18\u66b4": "dust storm",
    "\u6d6e\u5c18": "airborne dust",
    "\u626c\u6c99": "blowing sand",
    "\u51bb\u96e8": "freezing rain",
    "\u96f7\u9635\u96e8\u5e76\u4f34\u6709\u51b0\u96f9": "thundershowers with hail",
    "\u4e1c\u5357": "southeast",
    "\u4e1c\u5317": "northeast",
    "\u897f\u5357": "southwest",
    "\u897f\u5317": "northwest",
    "\u4e1c": "east",
    "\u5357": "south",
    "\u897f": "west",
    "\u5317": "north",
    "\u65e0\u6301\u7eed\u98ce\u5411": "variable",
    "\u65cb\u8f6c\u98ce": "rotating wind",
    "\u5fae\u98ce": "light breeze",
    "\u7545\u901a": "free flowing",
    "\u57fa\u672c\u7545\u901a": "mostly free flowing",
    "\u7f13\u884c": "slow moving",
    "\u62e5\u5835": "congested",
    "\u4e25\u91cd\u62e5\u5835": "severely congested",
    "\u8f6c": " becoming ",
    "\u5230": " to ",
    "\u6709\u98ce": "windy",
    "\u5f3a\u98ce": "strong wind",
    "\u5c11\u4e91": "mostly clear",
    "\u4eca\u5929": "today",
    "\u660e\u5929": "tomorrow",
    "\u672a\u77e5": "unknown",
    "\u672a\u77e5\u65f6\u95f4": "unknown time",
    "\u5317\u4eac": "Beijing",
    "\u4e0a\u6d77": "Shanghai",
    "\u5929\u6d25": "Tianjin",
    "\u91cd\u5e86": "Chongqing",
    "\u5408\u80a5": "Hefei",
    "\u5b89\u5fbd": "Anhui",
    "\u6cb3\u5317": "Hebei",
    "\u9ed1\u9f99\u6c5f": "Heilongjiang",
    "\u5185\u8499\u53e4": "Inner Mongolia",
    "\u5e7f\u897f": "Guangxi",
    "\u56db\u5ddd": "Sichuan",
    "\u6210\u90fd": "Chengdu",
    "\u54c8\u5c14\u6ee8": "Harbin",
    "\u592a\u539f": "Taiyuan",
    "\u82cf\u5dde": "Suzhou",
    "\u5357\u4eac": "Nanjing",
    "\u5409\u6797": "Jilin",
    "\u8d35\u5dde": "Guizhou",
    "\u5e7f\u4e1c": "Guangdong",
    "\u5e7f\u5dde": "Guangzhou",
    "\u7701": " Province",
    "\u5e02": " City",
}


def english_provider_text(value, fallback='Provider text unavailable in English'):
    """Translate known provider labels and provide an explicit English fallback for others."""
    if value in (None, '', [], {}):
        return fallback
    original = str(value)
    result = original
    # Replace each wire label once, matching longer labels before their components.
    # Spaces keep adjacent labels readable, for example mixed rain/snow conditions.
    pattern = '|'.join(re.escape(term) for term in sorted(PROVIDER_TERMS, key=len, reverse=True))
    result = re.sub(pattern, lambda match: ' ' + PROVIDER_TERMS[match[0]] + ' ', result)
    if re.search(r'[\u3400-\u9fff]', result):
        return fallback
    return re.sub(r'\s+', ' ', result).strip()


def localize_provider_data(value):
    """Localize values recursively while retaining all provider schema keys and numeric data."""
    if isinstance(value, dict):
        return {key: localize_provider_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [localize_provider_data(item) for item in value]
    if isinstance(value, str):
        return english_provider_text(value, 'unknown')
    return value


def compact_reply(text, limit=220):
    text = re.sub(r'<think>.*?</think>', '', str(text), flags=re.S | re.I)
    text = re.sub(r'\[([^\]]+)\]\(https?://[^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://[^\s<>]+', '', text)
    text = re.sub(r'\[SEARCH_REQUEST:[\s\S]*', '', text)
    text = text.replace('**', '').replace('[SPLIT]', ' ')
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*]\s+)', '', text)
    text = html.unescape(re.sub(r'\s+', ' ', text)).strip()
    if len(text) <= limit:
        return text
    short = text[:limit-1]
    ends = [match.end() for match in re.finditer(r'[.!?;](?=\s|$)|[。！？；]', short)]
    if ends and ends[-1] >= limit // 2:
        return short[:ends[-1]]
    return short.rstrip('，,：: ') + '…'


def concise_source(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username:
            return ''
        params = urllib.parse.parse_qs(parsed.query)
        if host == 'bilibili.com' or host.endswith('.bilibili.com'):
            match = re.search(r'/video/(BV[A-Za-z0-9]+|av\d+)', parsed.path)
            if match:
                part = params.get('p', ['1'])[0]
                return 'https://www.bilibili.com/video/' + match[1] + ('?p=' + part if part.isdigit() and int(part) > 1 else '')
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com'} and params.get('v'):
            return 'https://www.youtube.com/watch?' + urllib.parse.urlencode({'v': params['v'][0]})
        ignored = {'buvid', 'spmid', 'from_spmid', 'timestamp', 'unique_k', 'share_session_id',
                   'share_source', 'share_tag', 'share_medium', 'share_plat', 'share_from', 'fbclid', 'gclid'}
        query = urllib.parse.urlencode([(key, value) for key, value in urllib.parse.parse_qsl(parsed.query)
            if key not in ignored and not key.startswith('utm_')])
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ''))
    except ValueError:
        return ''


def parse_search_request(text):
    """Only accept a standalone model-generated JSON plan, never read identically named tags from user text."""
    match = re.fullmatch(r'\s*\[SEARCH_REQUEST:\s*(\{[\s\S]*\})\s*\]\s*', str(text))
    if not match or len(match[1]) > 4000:
        return None
    try:
        plan = json.loads(match[1])
    except ValueError:
        return None
    if not isinstance(plan, dict) or plan.get('mode') not in {'wiki', 'latest', 'web'}:
        return None
    query, topic = plan.get('query'), plan.get('topic', '')
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500 or not isinstance(topic, str) or len(topic) > 100:
        return None
    # Do not allow arbitrary model selection/permissions/or executable scripts, return only fixed fields.
    return {'query': query.strip(), 'mode': plan['mode'], 'topic': topic.strip()}
