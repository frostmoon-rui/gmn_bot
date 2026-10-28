"""Language routing and safe, stage-specific Wikipedia failure descriptions."""
import re
import urllib.error
import time


class WikipediaFetchError(RuntimeError):
    pass


class WikipediaCooldowns:
    """Parent-owned state survives individual search-worker processes."""
    def __init__(self):
        self.until = {}

    def active(self):
        now = time.monotonic()
        self.until = {language: end for language, end in self.until.items() if end > now}
        return sorted(self.until)

    def observe(self, warnings):
        for warning in warnings:
            match = re.search(r'Wikipedia \[(en|zh)\]:.*HTTP 429', str(warning))
            if match:
                delay = re.search(r'retry after (\d+) seconds', str(warning))
                seconds = min(3600, max(60, int(delay[1]))) if delay else 600
                self.until[match[1]] = time.monotonic() + seconds


def wikipedia_languages(query, topic=''):
    value = topic.strip() or query.strip()
    if re.search(r'[A-Za-z]', value) and not re.search(r'[\u4e00-\u9fff]', value):
        return ('en',)
    return ('zh', 'en')


def wiki_error_label(error):
    if isinstance(error, WikipediaFetchError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return 'HTTP ' + str(error.code)
    if isinstance(error, TimeoutError):
        return 'timeout'
    return type(error).__name__
