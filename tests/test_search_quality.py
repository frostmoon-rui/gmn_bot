"""Offline irrelevant-SERP and off-topic-body regression tests."""
import unittest
import urllib.parse
import urllib.error
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

    def test_google_role_heading_lite_links_and_https_upgrade(self):
        for markup in (
            '<a href="/url?q=http://notice.example/a"><div role="heading">宁夏供热安排</div></a>',
            '<a class="result-link" href="//notice.example/a">宁夏供热安排</a>'):
            parser=OrganicResults('https://www.google.com')
            parser.feed(markup)
            self.assertEqual(parser.results(QUESTION),['https://notice.example/a'])

    def test_split_chinese_nodes_and_publication_year_are_not_mandatory(self):
        parser=OrganicResults()
        parser.feed('<h3><a href="https://notice.example/a">宁夏<span>供</span><b>热</b>安排</a></h3>')
        self.assertEqual(parser.results('宁夏 供暖时间 2026'),['https://notice.example/a'])
        self.assertEqual(keyword_query('宁夏 供暖时间 2026'),'宁夏 供暖')

    def test_rejections_report_title_and_missing_terms_without_page_dump(self):
        parser=OrganicResults()
        parser.feed('<h2><a href="https://people.example/a">Ramesh Mali</a></h2>')
        diag=parser.results(QUESTION).diagnostics[0]
        self.assertEqual(diag['rejected_samples'][0]['title'],'Ramesh Mali')
        self.assertIn('宁夏',diag['rejected_samples'][0]['missing'])
        self.assertEqual(diag['anchors'],1)

    def test_challenge_response_is_paused_without_repeating_that_engine(self):
        challenge='<div class="anomaly-modal">Challenge</div>'
        good='<h3><a href="/link?url=opaque">宁夏供热安排</a></h3>'
        with patch('search_worker.read',side_effect=[challenge,'',good]) as read:
            result=alternative_search('宁夏 供暖时间 2026')
        self.assertEqual(result,['https://www.baidu.com/link?url=opaque'])
        self.assertEqual(result.diagnostics[0]['state'],'verification-required')
        self.assertEqual(read.call_count,3)

    def test_empty_and_failed_engines_keep_their_diagnostics(self):
        error=urllib.error.HTTPError('https://www.bing.com/search',429,'limited',{},None)
        with patch('search_worker.read',side_effect=['',error]):
            result=alternative_search('specific subject')
        self.assertEqual(result,[])
        self.assertEqual(result.diagnostics[-1]['state'],'request-failed')

    def test_fallback_admission_budget_prevents_more_requests(self):
        with patch('search_worker.read',return_value='') as read, \
             patch('search_worker.time.monotonic',side_effect=[0,0,100,100,100,100,100]):
            result=alternative_search('宁夏 供暖时间 2026')
        self.assertEqual(result,[])
        self.assertEqual(read.call_count,1)

    def test_public_verification_page_is_not_evidence(self):
        from research_transport import crawl_public_page
        with patch('research_transport.read_public_page',return_value='<title>百度安全验证</title>宁夏供暖'):
            with self.assertRaisesRegex(ValueError,'verification'):
                crawl_public_page('https://notice.example/a')
