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
from wikipedia_diagnostics import wikipedia_languages, wiki_error_label, WikipediaFetchError
from search_links import decode_result_link
from search_quality import OrganicResults, relevant, SearchResults, keyword_query


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
        not has_domain(p['url'], 'openai.com'), not has_domain(p['url'], 'gov.cn'), has_domain(p['url'], 'wikipedia.org')))


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        href = decode_result_link(dict(attrs).get('href', ''))
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
            for domain in ('google.com', 'googleusercontent.com', 'gstatic.com', 'duckduckgo.com', 'bing.com')):
            if href not in self.links:
                self.links.append(href)


def read(url, limit=900000):
    from research_transport import read_public_page
    return read_public_page(url, limit)


def google(query, recent=False):
    params = {'q': query, 'num': 15, 'hl': 'zh-CN' if re.search(r'[\u4e00-\u9fff]',query) else 'en', 'gbv': 1}
    if recent:
        params['tbs'] = 'qdr:m'
    raw = read('https://www.google.com/search?' + urllib.parse.urlencode(params))
    if 'unusual traffic' in raw.lower() or '/sorry/' in raw or 'g-recaptcha' in raw:
        raise RuntimeError('Google requires verification; retry later. No CAPTCHA bypass was attempted.')
    parser = OrganicResults('https://www.google.com')
    parser.feed(raw)
    return parser.results(query)


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
    api_failure = 'no matching article'
    try:
        pages = _wikipedia_api(query, language, topic)
        if pages:
            return pages
    except Exception as error:
        api_failure = wiki_error_label(error)
        if isinstance(error, urllib.error.HTTPError) and error.code == 429:
            retry = (error.headers or {}).get('Retry-After', '')
            seconds = min(3600, max(60, int(retry))) if str(retry).isdigit() else 600
            raise WikipediaFetchError('API HTTP 429; retry after ' + str(seconds) + ' seconds') from None
    from research_transport import crawl_public_page
    title = topic or search_topic(query)
    url = f'https://{language}.wikipedia.org/wiki/' + urllib.parse.quote(title.replace(' ', '_'), safe='')
    try:
        text = crawl_public_page(url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise WikipediaFetchError('article ' + wiki_error_label(error) + '; API ' + api_failure) from None
    except Exception as error:
        raise WikipediaFetchError('article ' + wiki_error_label(error) + '; API ' + api_failure) from None
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
    diagnostics=[]
    paused=set()
    deadline=time.monotonic()+75
    chinese=bool(re.search(r'[\u3400-\u9fff]',query))
    endpoints=['https://html.duckduckgo.com/html/?','https://www.bing.com/search?']
    if chinese:
        endpoints.append('https://www.baidu.com/s?')
    for attempt in dict.fromkeys((query, keyword_query(query))):
        for endpoint in endpoints:
            host=urllib.parse.urlsplit(endpoint).hostname
            if host in paused or time.monotonic() >= deadline:
                continue
            try:
                params={'wd' if 'baidu.com' in host else 'q':attempt}
                if chinese:
                    if 'bing.com' in host:
                        params.update({'mkt':'zh-CN','setlang':'zh-hans'})
                    elif 'duckduckgo.com' in host:
                        params['kl']='cn-zh'
                parser=OrganicResults(endpoint)
                parser.feed(read(endpoint+urllib.parse.urlencode(params)))
                result=parser.results(query)
                diagnostics.extend(dict(row, engine=host, query=attempt) for row in result.diagnostics)
                if parser.page_state != 'parsed':
                    paused.add(host)
                if result:
                    return SearchResults(result.entries,diagnostics)
            except Exception as error:
                diagnostics.append({'engine':host,'query':attempt,'state':'request-failed','error':wiki_error_label(error)})
                if isinstance(error,urllib.error.HTTPError) and error.code in {403,429,999}:
                    paused.add(host)
    # Keep per-engine diagnostics even when every channel fails.
    return SearchResults(diagnostics=diagnostics)


def collect(query, mode='web', topic='', skip_wikipedia=()):
    warnings = []
    stats = {'google_links':0,'fallback_links':0,'wikipedia_pages':0,'rejected_pages':0,'fetch_failures':0}
    diagnostics = []
    def search_google(text, recent):
        links=google(text,recent)
        diagnostics.extend(dict(row,engine='google',query=text) for row in getattr(links,'diagnostics',[]))
        stats['google_links']+=len(links)
        return links
    if mode == 'wiki':
        # Fetch model-selected encyclopedia topics first; sufficient articles skip search engines.
        wiki_pages = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [(pool.submit(wikipedia, query, language, topic or None), language)
                    for language in wikipedia_languages(query, topic) if language not in skip_wikipedia]
            for job, language in jobs:
                try:
                    values = job.result()
                    wiki_pages.extend(values)
                    if not values:
                        warnings.append('Wikipedia [' + language + ']: no matching article')
                except Exception as error:
                    warnings.append('Wikipedia [' + language + ']: ' + wiki_error_label(error))
        if wiki_pages:
            pages = prepare_pages(wiki_pages)
            if pages:
                return {'pages': pages, 'warnings': warnings, 'urls': [page['url'] for page in pages],
                        'note': 'Model-selected encyclopedia articles provide background, not current-event verification.'}
    seeds = official_seeds(query)
    urls = list(seeds)
    pages = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        # Serialize Google requests and stop this round on throttling; do not bypass verification.
        search_query = ('site:openai.com OR site:developers.openai.com ' + query) if seeds else query
        try:
            recent = mode == 'latest' or is_recent_query(query)
            urls.extend(url for url in search_google(search_query, recent) if url not in urls)
            if recent:
                time.sleep(2)
                urls.extend(url for url in search_google(search_query, False) if url not in urls)
            if mode == 'latest' and re.search(r'[\u4e00-\u9fff]',query) and not seeds:
                urls.extend(url for url in search_google(query+' site:gov.cn',False) if url not in urls)
        except urllib.error.HTTPError as error:
            warnings.append(f'Google HTTP {error.code}: stopped this Google round; using fallback search and direct official pages')
            # Respect throttling and use independent fallback channels.
            time.sleep(3)
        except Exception as error:
            warnings.append('Google: ' + str(error))
        jobs = [(pool.submit(alternative_search, search_query), 'Fallback web search')]
        if mode == 'web' and topic:
            jobs += [(pool.submit(wikipedia, query, language, topic or None), 'Wikipedia [' + language + ']')
                     for language in wikipedia_languages(query, topic) if language not in skip_wikipedia]
        for task, source in jobs:
            try:
                values = task.result()
                if source.startswith('Wikipedia ['):
                    pages.extend(values)
                    stats['wikipedia_pages']+=len(values)
                else:
                    diagnostics.extend(getattr(values,'diagnostics',[]))
                    stats['fallback_links']+=len(values)
                    urls.extend(url for url in values if url not in urls)
            except Exception as error:
                warnings.append(source + ': ' + (wiki_error_label(error) if source.startswith('Wikipedia [') else str(error)))
        # Reuse public-address checks and bounded retrieval without loading the live bot.
        from research_transport import crawl_public_page
        def fetch(url):
            try:
                text = crawl_public_page(url)
                if text.strip() and relevant(query, text[:16000]):
                    return {'url': url, 'text': text, 'source': 'Official source' if url in seeds else 'Search page',
                        'dates_found': sorted(set(re.findall(r'20\d{2}[-/]\d{1,2}[-/]\d{1,2}', text)))[:30]}
            except Exception as error:
                return {'url': url, 'text': '', 'error': str(error)}
            return {'url': url, 'text': '', 'error': 'empty-or-off-topic-page', 'rejected':True}
        limited = set(skip_wikipedia)
        for warning in warnings:
            match = re.search(r'Wikipedia \[(en|zh)\]:.*HTTP 429', warning)
            if match:
                limited.add(match[1])
        urls = [url for url in urls if urllib.parse.urlsplit(url).hostname not in
                {language + '.wikipedia.org' for language in limited}]
        if mode == 'latest':
            urls = [url for url in urls if not has_domain(url,'wikipedia.org')]
        urls.sort(key=lambda url:(url not in seeds,not has_domain(url,'gov.cn')))
        for page in pool.map(fetch, urls[:24]):
            if page.get('text'):
                pages.append(page)
            else:
                stats['rejected_pages' if page.get('rejected') else 'fetch_failures'] += 1
                warnings.append(page['url'] + ': ' + page['error'])
    # Prioritize primary pages; Wikipedia supplies background only.
    stats['fetched_pages'] = len(pages)
    return {'pages': prepare_pages(pages), 'warnings': warnings, 'urls': urls[:24], 'retrieval_stats':stats,
        'search_diagnostics':diagnostics,
        'note': 'Supplement recent searches with general results; verify relevance, event dates and publication dates from the text.'}


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    request = json.load(sys.stdin)
    json.dump(collect(request['query'], request.get('mode', 'web'), request.get('topic', ''),
                      request.get('skip_wikipedia', [])), sys.stdout, ensure_ascii=False)
