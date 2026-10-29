"""Public news-feed discovery with verified publisher-page retrieval, no AI search."""
import time
import re
from email.utils import parsedate_to_datetime
import urllib.parse
import xml.etree.ElementTree as ET
from search_links import decode_result_link
from search_quality import relevant, relevance_detail, keyword_query, terms


def parse_feed(raw, query):
    if '<!DOCTYPE' in raw.upper() or '<!ENTITY' in raw.upper() or len(raw)>900000:
        raise ValueError('Unsupported news feed')
    root=ET.fromstring(raw)
    entries=[]
    requested_year=re.search(r'(?<!\d)(20\d{2})(?!\d)',query)
    for item in root.findall('./channel/item')[:100]:
        title=item.findtext('title','')[:500]
        detail=relevance_detail(query,title)
        if not detail['matched']:
            continue
        url=item.findtext('link','')
        target=urllib.parse.urlsplit(url)
        source=item.find('source')
        publisher=decode_result_link(source.get('url',''),upgrade_http=True) if source is not None else ''
        if not publisher or target.scheme!='https' or target.hostname!='news.google.com' or target.username or target.password or target.port not in (None,443) or not target.path.startswith('/rss/articles/'):
            continue
        published=item.findtext('pubDate','')[:100]
        try:
            published_date=parsedate_to_datetime(published)
            stamp=published_date.timestamp()
            same_year=not requested_year or published_date.year==int(requested_year[1])
        except (ValueError,TypeError,OverflowError):
            stamp=0
            same_year=not requested_year
        density=len(detail['matched'])/max(1,len(terms(title)))
        entries.append({'title':title,'feed_url':url,'publisher':publisher,'published':published,
            '_rank':(detail['relevant'],same_year,density,stamp)})
    entries.sort(key=lambda item:item['_rank'],reverse=True)
    return entries[:3]


def news_pages(query):
    from research_transport import read_public_page
    from search_browser import render_news_article
    started=time.monotonic()
    diagnostics=[]
    pages=[]
    feed_query=keyword_query(query)
    if re.search(r'时间|何时|什么时候',query) and '时间' not in feed_query:
        feed_query+=' 时间'
    url='https://news.google.com/rss/search?'+urllib.parse.urlencode({
        'q':feed_query,'hl':'zh-CN' if any('\u3400'<=c<='\u9fff' for c in query) else 'en-US',
        'gl':'CN','ceid':'CN:zh-Hans'})
    try:
        entries=parse_feed(read_public_page(url),query)
        diagnostics.append({'engine':'google-news','state':'feed-read','candidates':len(entries)})
    except Exception as error:
        return {'pages':[],'diagnostics':[{'engine':'google-news','state':'request-failed','error':type(error).__name__}]}
    for entry in entries:
        if time.monotonic()-started>65:
            break
        try:
            doc=render_news_article(entry['feed_url'],entry['publisher'])
            text=doc.get('text','')
            canonical=doc.get('url','')
            expected=urllib.parse.urlsplit(entry['publisher']).hostname.removeprefix('www.')
            actual=(urllib.parse.urlsplit(canonical).hostname or '').removeprefix('www.')
            path=urllib.parse.urlsplit(canonical).path
            if doc.get('state')=='rendered' and actual==expected and path not in ('','/') and len(text)>200 and relevant(query,text[:16000]):
                pages.append({'url':canonical,'title':doc.get('title') or entry['title'],'text':text,
                    'source':'Publisher page discovered through public news RSS',
                    'feed_published_at':entry['published'],
                    'date_note':'Feed publication time is not the event date or independently verified article publication time.'})
                diagnostics.append({'engine':'google-news','state':'publisher-read','host':actual})
            else:
                diagnostics.append({'engine':'google-news','state':'publisher-unavailable','error':doc.get('error','off-topic-or-empty')})
        except Exception as error:
            diagnostics.append({'engine':'google-news','state':'publisher-unavailable','error':type(error).__name__})
    return {'pages':pages,'diagnostics':diagnostics}
