"""Organic search extraction and conservative lexical relevance checks.

These checks screen evidence for topic overlap, not truth or publication dates.
"""
import math
import re
from html.parser import HTMLParser
from search_links import decode_result_link


STOP_WORDS = set('the a an is are was were what when where why how do does did '
                 'for of in on at to from and or with by latest current today '
                 'local official please search find information about this year'.split())
HAN_QUESTIONS = re.compile(r'什么时候|什么时间|何时|时间|最新|现在|目前|今年|请问|查询|搜索|是什么|有哪些|如何|怎么|的|吗|呢')


def normalize(text):
    text = str(text).casefold()
    # Geographic strings are upstream query data, not interface translations.
    return text.replace('供热', '供暖').replace('采暖', '供暖')


def terms(query):
    query = re.sub(r'\b(?:site|filetype|inurl):\S+', ' ', query, flags=re.I)
    chunks = re.findall(r'[a-z][a-z0-9-]*|[\u3400-\u9fff]+', normalize(query))
    result = set()
    for chunk in chunks:
        if chunk.isascii():
            if chunk not in STOP_WORDS and len(chunk) > 1:
                result.add(chunk)
        else:
            for word in HAN_QUESTIONS.split(chunk):
                if len(word) >= 2:
                    result.update(word[i:i+2] for i in range(len(word)-1))
    return result


def keyword_query(query):
    if not re.search(r'[\u3400-\u9fff]', query):
        return query
    return ' '.join(part.strip() for part in HAN_QUESTIONS.split(query) if part.strip())


def relevant(query, text):
    needles = terms(query)
    if not needles:
        return False
    haystack = normalize(text)
    matches = sum(needle in haystack for needle in needles)
    return matches >= max(1, math.ceil(len(needles)*0.6))


class SearchResults(list):
    def __init__(self, entries=(), diagnostics=()):
        self.entries = list(entries)
        self.diagnostics = list(diagnostics)
        super().__init__(entry['url'] for entry in self.entries)


class OrganicResults(HTMLParser):
    """Read result headings/anchors; ignore navigation, ads and arbitrary links."""
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.block = None
        self.anchor = None
        self.entries = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if tag not in self.VOID:
            self.stack.append(tag)
        if self.block is None and (classes & {'b_algo', 'result', 'tF2Cxc', 'MjjYud', 'Ww4FFb'}):
            self.block = {'depth':len(self.stack), 'text':[], 'entries':[]}
        if tag == 'a':
            self.anchor = {'url':decode_result_link(attrs.get('href', '')), 'text':[],
                           'organic':bool(classes & {'result__a'}) or any(t in {'h2','h3'} for t in self.stack[:-1])}
        if tag in {'h2','h3'} and self.anchor:
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
                from urllib.parse import urlsplit
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
        for entry in self.entries:
            if relevant(query, entry['title']+' '+entry['snippet']):
                accepted.setdefault(entry['url'], entry)
        return SearchResults(list(accepted.values())[:18], [
            {'candidates':len(self.entries), 'accepted':len(accepted),
             'rejected':len(self.entries)-len(accepted)}])
