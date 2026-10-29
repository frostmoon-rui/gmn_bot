"""Offline news discovery, publisher identity and full-body fallback checks."""
import unittest
from unittest.mock import patch
from news_search import parse_feed, news_pages
from search_worker import collect
from search_browser import allowed_news_request


FEED='''<rss><channel><item><title>宁夏供热安排</title>
<link>https://news.google.com/rss/articles/test</link>
<pubDate>Tue, 13 Oct 2026 12:48:00 GMT</pubDate>
<source url="https://publisher.example">Publisher</source></item></channel></rss>'''
BODY='宁夏某市发布本采暖季供暖安排；各市可能不同，需要核对所在地公告。'*20


class NewsSearchTests(unittest.TestCase):
    def test_feed_preserves_publication_metadata_without_claiming_article_content(self):
        entries=parse_feed(FEED,'2026年宁夏供暖时间')
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['publisher'],'https://publisher.example')
        self.assertNotIn('text',entries[0])
        for raw in ('<!DOCTYPE rss>'+FEED,FEED.replace('https://news.google.com/rss/articles/test','https://evil.example/redirect')):
            if raw.startswith('<!DOCTYPE'):
                with self.assertRaises(ValueError):
                    parse_feed(raw,'宁夏供暖')
            else:
                self.assertEqual(parse_feed(raw,'宁夏供暖'),[])

    def test_only_reached_publisher_body_becomes_evidence(self):
        doc={'state':'rendered','url':'https://publisher.example/article','text':BODY,'title':'Heating notice'}
        with patch('research_transport.read_public_page',return_value=FEED), \
             patch('search_browser.render_news_article',return_value=doc):
            result=news_pages('2026年宁夏供暖时间')
        self.assertEqual(result['pages'][0]['url'],doc['url'])
        self.assertEqual(result['pages'][0]['text'],BODY)
        self.assertIn('Feed publication time',result['pages'][0]['date_note'])

    def test_aggregator_shell_or_wrong_publisher_is_not_article_evidence(self):
        for doc in ({'state':'rendered','url':'https://news.google.com/rss/articles/test','text':BODY},
                    {'state':'rendered','url':'https://different.example/a','text':BODY},
                    {'state':'verification-required','url':'https://publisher.example/a','text':BODY}):
            with patch('research_transport.read_public_page',return_value=FEED), \
                 patch('search_browser.render_news_article',return_value=doc):
                self.assertEqual(news_pages('宁夏供暖')['pages'],[])

    def test_latest_uses_full_news_pages_when_web_engines_return_nothing(self):
        page={'url':'https://publisher.example/a','text':BODY}
        with patch('search_worker.google',return_value=[]),patch('search_worker.alternative_search',return_value=[]), \
             patch('search_worker.time.sleep'),patch('news_search.news_pages',return_value={'pages':[page],'diagnostics':[]}) as news:
            result=collect('2026年宁夏供暖时间','latest')
        news.assert_called_once()
        self.assertEqual(result['retrieval_stats']['news_pages'],1)
        self.assertEqual(result['pages'][0]['url'],page['url'])

    def test_news_browser_allows_only_needed_rpc_and_publisher_navigation(self):
        publisher='https://publisher.example'
        self.assertTrue(allowed_news_request('https://news.google.com/_/DotsSplashUi/data/batchexecute',publisher,method='POST',resource='xhr'))
        self.assertTrue(allowed_news_request('https://publisher.example/a',publisher,navigation=True))
        self.assertFalse(allowed_news_request('https://publisher.example/login',publisher,method='POST'))
        self.assertFalse(allowed_news_request('https://unrelated.example/a',publisher,navigation=True))
        self.assertFalse(allowed_news_request('http://publisher.example/a',publisher,navigation=True))

    def test_short_direct_answer_headline_precedes_long_incidental_match(self):
        competition=FEED.replace('宁夏供热安排','宁夏首届供热行业职业技能竞赛和培训活动顺利举行').replace('13 Oct','20 Oct')
        combined=competition.replace('</channel></rss>',FEED.split('<channel>')[1])
        entries=parse_feed(combined,'2026年宁夏供暖时间')
        self.assertEqual(entries[0]['title'],'宁夏供热安排')
