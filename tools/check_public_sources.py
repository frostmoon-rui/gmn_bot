"""Explicit read-only Wolfx/blog checks without starting the bot or sending QQ messages."""
import argparse
import asyncio
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from earthquake_feed import normalize


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.current = [dict(attrs).get('href', ''), '']
    def handle_data(self, value):
        if self.current:
            self.current[1] += value
    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            self.links.append(self.current)
            self.current = None


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'GmnBot/1.0 public-source-check'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(1024*1024).decode('utf-8', 'replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        print('No network requests; pass --live for real checks.')
        return 0
    import json
    for source, path in [('cenc_report', 'cenc_eqlist.json'), ('cenc', 'cenc_eew.json'), ('sc', 'sc_eew.json')]:
        try:
            data = json.loads(fetch('https://api.wolfx.jp/'+path))
            if source == 'cenc_report':
                entries = [normalize(value, source) for key, value in data.items() if key.startswith('No') and isinstance(value, dict)]
                print('[Live source]', source, 'parseable events', len(entries))
            else:
                event = normalize(data, source, True)
                print('[Live source]', source, 'fields parseable; stale:', event['stale'], '(stale events do not trigger notifications)')
        except Exception as error:
            print('[Live source failed]', source, type(error).__name__, str(error)[:120])
    try:
        links = Links()
        links.feed(fetch('https://cachetide.top'))
        related = [(url, title.strip()) for url, title in links.links if re.search(r'food|restaurant|dining|snack|\u7f8e\u98df|\u63a2\u5e97|\u9910\u5385|\u5c0f\u5403|\u5403\u559d|\u98df\u8bb0', title, re.I)]  # external blog article labels
        print('[Blog reference]', 'https://cachetide.top', 'homepage readable; food article candidates:', len(related))
        for url, title in related[:5]:
            absolute = urllib.parse.urljoin('https://cachetide.top', url)
            if urllib.parse.urlsplit(absolute).hostname == 'cachetide.top':
                print('[Article needs manual verification]', title[:70], absolute)
        print('[Blog import rule] Do not create venue markers before verifying name/address, or copy full articles.')
    except Exception as error:
        print('[Blog reference unavailable]', type(error).__name__)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
