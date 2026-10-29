"""Organic search extraction and conservative lexical relevance checks.

These checks screen evidence for topic overlap, not truth or publication dates.
"""
import math
import re
import logging
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urlsplit
from search_links import decode_result_link


STOP_WORDS = set('the a an is are was were what when where why how do does did '
                 'for of in on at to from and or with by latest current today '
                 'local official please search find information about this year'.split())
HAN_QUESTIONS = re.compile(r'什么时候|什么时间|何时|时间|最新|现在|目前|今年|请问|查询|搜索|是什么|有哪些|如何|怎么|的|吗|呢')


def normalize(text):
    text = str(text).casefold()
    text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)
    # Geographic strings are upstream query data, not interface translations.
    return text.replace('供热', '供暖').replace('采暖', '供暖')


def terms(query):
    query = re.sub(r'\b(?:site|filetype|inurl):\S+', ' ', query, flags=re.I)
    query = re.sub(r'(?<!\d)20\d{2}年?(?!\d)', ' ', query)
    query = query.casefold().replace('供热', '供暖').replace('采暖', '供暖')
    chunks = re.findall(r'[a-z][a-z0-9-]*|[\u3400-\u9fff]+', query)
    result = set()
    for chunk in chunks:
        if chunk.isascii():
            if chunk not in STOP_WORDS and len(chunk) > 1:
                result.add(chunk)
        else:
            for word in HAN_QUESTIONS.split(chunk):
                if len(word) >= 2:
                    result.update(token for token in chinese_tokens(word) if len(token) >= 2)
    return result


@lru_cache(maxsize=256)
def chinese_tokens(word):
    import jieba
    jieba.setLogLevel(logging.ERROR)
    return tuple(jieba.cut(word, HMM=False))


def keyword_query(query):
    if not re.search(r'[\u3400-\u9fff]', query):
        return query
    query = re.sub(r'(?<!\d)20\d{2}(?:年)?(?!\d)', ' ', query)
    query = ' '.join(part.strip() for part in HAN_QUESTIONS.split(query) if part.strip())
    query = re.sub(r'[\u3400-\u9fff]+',lambda match:' '.join(chinese_tokens(match[0])),query)
    return ' '.join(query.split())


def relevant(query, text):
    return relevance_detail(query, text)['relevant']


def relevance_detail(query, text):
    needles = terms(query)
    haystack = normalize(text)
    matched = sorted(needle for needle in needles if needle in haystack)
    missing = sorted(needles-set(matched))
    return {'relevant':bool(needles) and len(matched) >= max(1, math.ceil(len(needles)*0.6)),
            'matched':matched[:20], 'missing':missing[:20]}


class SearchResults(list):
    def __init__(self, entries=(), diagnostics=()):
        self.entries = list(entries)
        self.diagnostics = list(diagnostics)
        super().__init__(entry['url'] for entry in self.entries)


class OrganicResults(HTMLParser):
    """Read result headings/anchors; ignore navigation, ads and arbitrary links."""
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self, base=''):
        super().__init__()
        self.stack = []
        self.block = None
        self.anchor = None
        self.entries = []
        self.base = base
        self.anchors = 0
        self.invalid_links = 0
        self.raw_size = 0
        self.page_state = 'parsed'

    def feed(self, data):
        self.raw_size += len(data)
        lowered = data.lower()
        if any(marker in lowered for marker in ('g-recaptcha', 'unusual traffic', '/sorry/',
                'anomaly.js', 'anomaly-modal', '百度安全验证', 'verify you are human')):
            self.page_state = 'verification-required'
        elif 'consent.google.com' in lowered and ('before you continue' in lowered or '同意' in lowered):
            self.page_state = 'consent-required'
        elif '/httpservice/retry/enablejs' in lowered:
            self.page_state = 'javascript-required'
        super().feed(data)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if tag not in self.VOID:
            self.stack.append(tag)
        if self.block is None and (classes & {'b_algo', 'result', 'tF2Cxc', 'MjjYud', 'Ww4FFb'}):
            self.block = {'depth':len(self.stack), 'text':[], 'entries':[]}
        if tag == 'a':
            self.anchors += 1
            href = attrs.get('href', '')
            if self.base and href.startswith(('/link?', '/url?')):
                from urllib.parse import urljoin
                href = urljoin(self.base, href)
            url = decode_result_link(href, upgrade_http=True)
            if not url and href:
                self.invalid_links += 1
            self.anchor = {'url':url, 'text':[],
                           'organic':bool(classes & {'result__a','result-link'}) or any(t in {'h2','h3'} for t in self.stack[:-1])}
        if (tag in {'h2','h3'} or attrs.get('role') == 'heading') and self.anchor:
            self.anchor['organic'] = True

    def handle_data(self, data):
        if self.block:
            self.block['text'].append(data)
        if self.anchor:
            self.anchor['text'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.anchor:
            item = self.anchor
            self.anchor = None
            url = item['url']
            if item['organic'] and url:
                host = (urlsplit(url).hostname or '').lower()
                if not any(host == d or host.endswith('.'+d) for d in (
                        'google.com','googleusercontent.com','gstatic.com','bing.com','duckduckgo.com')):
                    entry = {'url':url, 'title':' '.join(item['text']).strip(), 'snippet':''}
                    if self.block:
                        self.block['entries'].append(entry)
                    else:
                        self.entries.append(entry)
        if tag in self.stack:
            index = len(self.stack)-1-self.stack[::-1].index(tag)
            if self.block and index < self.block['depth']:
                snippet = ' '.join(self.block['text'])[:6000]
                for entry in self.block['entries']:
                    entry['snippet'] = snippet
                    self.entries.append(entry)
                self.block = None
            del self.stack[index:]

    def results(self, query):
        accepted = {}
        rejected = []
        for entry in self.entries:
            detail = relevance_detail(query, entry['title']+' '+entry['snippet'])
            if detail['relevant'] and self.page_state == 'parsed':
                accepted.setdefault(entry['url'], entry)
            elif len(rejected) < 3:
                rejected.append({'title':re.sub(r'\s+', ' ', entry['title'])[:140],
                    'host':urlsplit(entry['url']).hostname, 'matched':detail['matched'], 'missing':detail['missing']})
        return SearchResults(list(accepted.values())[:18], [
            {'candidates':len(self.entries), 'accepted':len(accepted),
             'rejected':len(self.entries)-len(accepted), 'state':self.page_state,
             'html_chars':self.raw_size, 'anchors':self.anchors, 'invalid_links':self.invalid_links,
             'rejected_samples':rejected}])
