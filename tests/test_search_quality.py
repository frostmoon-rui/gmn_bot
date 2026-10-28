"""Offline irrelevant-SERP and off-topic-body regression tests."""
import unittest
import urllib.parse
from unittest.mock import patch
from search_quality import OrganicResults, relevant, keyword_query
from search_worker import alternative_search, collect


QUESTION = '宁夏什么时候供暖'


class SearchQualityTests(unittest.TestCase):
    def test_question_and_search_operators_do_not_pollute_topic_terms(self):
        self.assertTrue(relevant(QUESTION, '宁夏供热安排与采暖季时间'))
        self.assertTrue(relevant(QUESTION+' site:gov.cn', '宁夏供热公告'))
        self.assertFalse(relevant(QUESTION, 'Ramesh Mali - LinkedIn - India'))
        self.assertFalse(relevant(QUESTION, '宁夏的人口和旅游景点'))
        self.assertEqual(keyword_query(QUESTION), '宁夏 供暖')

    def test_organic_headings_and_snippets_exclude_unrelated_and_navigation(self):
        parser=OrganicResults()
        parser.feed('<a href="https://noise.example/nav">宁夏供暖</a>'
            '<li class="b_algo"><h2><a href="https://noise.example/person">Ramesh Mali</a></h2><p>India profile</p></li>'
            '<li class="b_algo"><h2><a href="https://notice.example/heating">供热通知</a></h2><p>宁夏采暖季时间</p></li>')
        result=parser.results(QUESTION)
        self.assertEqual(result, ['https://notice.example/heating'])
        self.assertEqual(result.diagnostics[0]['rejected'],1)

    def test_google_heading_anchor_and_duckduckgo_result_anchor(self):
        for markup in (
            '<a href="/url?q=https://notice.example/a"><h3>宁夏供热安排</h3></a>',
            '<div class="result"><a class="result__a" href="https://notice.example/a">供热安排</a><p>宁夏采暖</p></div>'):
            parser=OrganicResults()
            parser.feed(markup)
            self.assertEqual(parser.results(QUESTION),['https://notice.example/a'])

    def test_off_topic_ddg_does_not_prevent_bing_fallback_and_locale_is_explicit(self):
        bad='<a class="result__a" href="https://people.example/ramesh">Ramesh Mali</a>'
        good='<li class="b_algo"><h2><a href="https://notice.example/a">宁夏供暖公告</a></h2></li>'
        with patch('search_worker.read',side_effect=[bad,good]) as read:
            result=alternative_search(QUESTION)
        self.assertEqual(result,['https://notice.example/a'])
        params=urllib.parse.parse_qs(urllib.parse.urlsplit(read.call_args.args[0]).query)
        self.assertEqual(params['q'],[QUESTION])
        self.assertEqual(params['mkt'],['zh-CN'])
        self.assertEqual(result.diagnostics[0]['rejected'],1)

    def test_body_relevance_is_checked_even_if_search_title_looked_relevant(self):
        with patch('search_worker.google',return_value=['https://people.example/a']), \
             patch('search_worker.alternative_search',return_value=[]),patch('search_worker.time.sleep'), \
             patch('research_transport.crawl_public_page',return_value='Ramesh Mali professional profile'):
            result=collect(QUESTION,'latest')
        self.assertEqual(result['pages'],[])
        self.assertIn('off-topic',result['warnings'][0])

    def test_keyword_retry_keeps_original_question_as_relevance_anchor(self):
        good='<a class="result__a" href="https://notice.example/a">宁夏供暖时间</a>'
        with patch('search_worker.read',side_effect=['','','',good]) as read:
            result=alternative_search(QUESTION)
        self.assertEqual(result,['https://notice.example/a'])
        self.assertEqual(read.call_count,4)
        self.assertEqual(result.diagnostics[-1]['query'],'宁夏 供暖')
