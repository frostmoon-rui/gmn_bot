"""Language routing and safe, stage-specific Wikipedia failure descriptions."""
import re
import urllib.error


class WikipediaFetchError(RuntimeError):
    pass


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
