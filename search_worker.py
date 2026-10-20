"""Independent Python Google/Wikipedia retrieval process without AI browsing interfaces."""
import concurrent.futures
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
import time
from html.parser import HTMLParser


OPENAI_TOPIC = re.compile(r'(?<![a-z0-9_])(?:chatgpt|gpt)(?![a-z_])|(?<![a-z0-9_])openai(?![a-z0-9_])', re.I)


def has_domain(url, domain):
    """Identify sources by URL hostname only; body text and source labels are not verification."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or '').lower()
        return parsed.scheme == 'https' and not parsed.username and (
            host == domain or host.endswith('.' + domain))
    except ValueError:
        return False


def is_recent_query(query):
    return bool(re.search(r'(?<![a-z])(?:latest|current|now|today|this month|this year)(?![a-z])', query, re.I))


def prepare_pages(pages):
    """Remove empty pages, invalid links and duplicates; official hosting does not verify every claim."""
    unique = {}
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get('text'), str) or not page['text'].strip():
            continue
        try:
            parsed = urllib.parse.urlsplit(page.get('url', ''))
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username:
                continue
            url = urllib.parse.urlunsplit(parsed._replace(fragment=''))
        except (ValueError, TypeError):
            continue
        clean = dict(page, url=url)
        clean['source'] = ('OpenAI official site (verify content and recency)' if has_domain(url, 'openai.com') else
            'Wikipedia (background only)' if has_domain(url, 'wikipedia.org') else 'Search page (not independently verified)')
        if url not in unique or len(clean['text']) > len(unique[url]['text']):
            unique[url] = clean
    return sorted(unique.values(), key=lambda p: (
        not has_domain(p['url'], 'openai.com'), has_domain(p['url'], 'wikipedia.org')))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = dict(attrs).get('href', '')
        if 'duckduckgo.com/l/' in href or href.startswith('/l/?'):
            href = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get('uddg', [''])[0]
        if href.startswith('/url?'):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            href = (query.get('q') or query.get('url') or [''])[0]
        try:
            parsed = urllib.parse.urlsplit(href)
        except ValueError:
            return
        host = parsed.hostname or ''
        if parsed.scheme == 'https' and host and not any(
            host == domain or host.endswith('.' + domain)
            for domain in ('google.com', 'googleusercontent.com', 'gstatic.com', 'duckduckgo.com')):
            if href not in self.links:
                self.links.append(href)


def read(url, limit=900000):
    from research_transport import read_public_page
    return read_public_page(url, limit)


def google(query, recent=False):
    params = {'q': query, 'num': 15, 'hl': 'en', 'gbv': 1}
    if recent:
        params['tbs'] = 'qdr:m'
    raw = read('https://www.google.com/search?' + urllib.parse.urlencode(params))
    if 'unusual traffic' in raw.lower() or '/sorry/' in raw or 'g-recaptcha' in raw:
        raise RuntimeError('Google requires verification; retry later. CAPTCHA was not bypassed.')
    parser = Links()
    parser.feed(raw)
    return parser.links[:18]


def _wikipedia_api(query, language, topic=None):
    topic = topic or search_topic(query)
    root = f'https://{language}.wikipedia.org/w/api.php?'
    found = json.loads(read(root + urllib.parse.urlencode({'action': 'query', 'list': 'search',
        'srsearch': topic, 'srlimit': 3, 'format': 'json', 'utf8': 1})))
    pages = []
    for hit in found.get('query', {}).get('search', []):
        if not relevant_title(hit.get('title', ''), topic):
            continue
        data = json.loads(read(root + urllib.parse.urlencode({'action': 'query', 'pageids': hit['pageid'],
            'prop': 'extracts|info', 'explaintext': 1, 'exchars': 20000, 'inprop': 'url', 'format': 'json'})))
        for item in data.get('query', {}).get('pages', {}).values():
            pages.append({'url': item.get('fullurl', ''), 'title': item.get('title', ''),
                'text': item.get('extract', ''), 'source': 'Wikipedia (background article; verify recency)'})
    return pages


def wikipedia(query, language, topic=None):
    try:
        pages = _wikipedia_api(query, language, topic)
        if pages:
            return pages
    except Exception:
        pass
    from research_transport import crawl_public_page
    title = topic or search_topic(query)
    url = f'https://{language}.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'), safe='')
    text = crawl_public_page(url)
    if not text.strip() or 'Wikipedia does not have an article with this exact name' in text:
        return []
    return [{'url': url, 'title': title, 'text': text, 'source': 'Wikipedia background page'}]


def search_topic(query):
    if OPENAI_TOPIC.search(query):
        return 'GPT'
    return re.sub(r'\b(?:latest|what is|what are|please|query|search|now|current)\b', ' ', query, flags=re.I).strip() or query


def relevant_title(title, topic):
    if topic == 'GPT':
        return bool(OPENAI_TOPIC.search(title) or re.search(r'generative pre.train', title, re.I))
    tokens = re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]{2,}', topic.lower())
    return any(token in title.lower() for token in tokens)


def official_seeds(query):
    if OPENAI_TOPIC.search(query):
        return ['https://developers.openai.com/api/docs/models',
            'https://openai.com/news/', 'https://help.openai.com/en/articles/9624314-model-release-notes']
    return []


def alternative_search(query):
    raw = read('https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query}))
    parser = Links()
    parser.feed(raw)
    return parser.links[:18]


def collect(query, mode='web', topic=''):
    warnings = []
    if mode == 'wiki':
        # The model selects encyclopedia intent and topic. Fetch relevant articles first; sufficient results skip search engines.
        wiki_pages = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(wikipedia, query, language, topic or None) for language in ('en', 'zh')]
            for job in jobs:
                try:
                    wiki_pages.extend(job.result())
                except Exception as error:
                    warnings.append('Wikipedia: ' + type(error).__name__)
        if wiki_pages:
            pages = prepare_pages(wiki_pages)
            if pages:
                return {'pages': pages, 'warnings': warnings, 'urls': [page['url'] for page in pages],
                        'note': 'Model-selected encyclopedia lookup prioritizes relevant Wikipedia articles; this does not verify current developments.'}
    seeds = official_seeds(query)
    urls = list(seeds)
    pages = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        # Serialize Google requests and stop this round on rate limits; never bypass CAPTCHA.
        search_query = ('site:openai.com OR site:developers.openai.com ' + query) if seeds else query
        try:
            recent = mode == 'latest' or is_recent_query(query)
            urls.extend(url for url in google(search_query, recent) if url not in urls)
            if recent:
                time.sleep(2)
                urls.extend(url for url in google(search_query, False) if url not in urls)
        except urllib.error.HTTPError as error:
            warnings.append(f'Google HTTP {error.code}: stopped Google requests this round; using fallback search and direct official pages')
            # Respect rate limits, back off and use an independent fallback instead of hammering Google.
            time.sleep(3)
        except Exception as error:
            warnings.append('Google: ' + str(error))
        jobs = [(pool.submit(alternative_search, search_query), 'Fallback web search')]
        if mode != 'wiki':
            jobs += [(pool.submit(wikipedia, query, language, topic or None), 'Wikipedia') for language in ('zh', 'en')]
        for task, source in jobs:
            try:
                values = task.result()
                if source == 'Wikipedia':
                    pages.extend(values)
                else:
                    urls.extend(url for url in values if url not in urls)
            except Exception as error:
                warnings.append(source + ': ' + str(error))
        # Reuse public-address validation and bounded page retrieval without loading the live bot.
        from research_transport import crawl_public_page
        def fetch(url):
            try:
                text = crawl_public_page(url)
                if text.strip():
                    return {'url': url, 'text': text, 'source': 'Direct official page' if url in seeds else 'Search page',
                        'dates_found': sorted(set(re.findall(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}', text)))[:30]}
            except Exception as error:
                return {'url': url, 'text': '', 'error': str(error)}
            return {'url': url, 'text': '', 'error': 'Page has no body text or uses an unsupported format'}
        for page in pool.map(fetch, urls[:24]):
            if page.get('text'):
                pages.append(page)
            else:
                warnings.append(page['url'] + ': ' + page['error'])
    # Prioritize official content; Wikipedia supplements background without crowding out primary evidence.
    return {'pages': prepare_pages(pages), 'warnings': warnings, 'urls': urls[:24],
        'note': 'Google results prioritize the last month, then general results. Verify relevance, event time and publication time from the page text.'}


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    request = json.load(sys.stdin)
    json.dump(collect(request['query'], request.get('mode', 'web'), request.get('topic', '')), sys.stdout, ensure_ascii=False)
