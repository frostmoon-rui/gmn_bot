"""Offline checks for language-aware Wikipedia retrieval and nonfatal failures."""
import unittest
import urllib.error
from unittest.mock import patch

from search_worker import wikipedia, collect
from wikipedia_diagnostics import wikipedia_languages, wiki_error_label, WikipediaFetchError


def http_error(code):
    return urllib.error.HTTPError('https://en.wikipedia.org/private?never-log', code, 'private message', {}, None)


class WikipediaDiagnosticsTests(unittest.TestCase):
    def test_english_topic_is_not_requested_as_a_chinese_article_slug(self):
        self.assertEqual(wikipedia_languages('An original question', 'Thought'), ('en',))
        self.assertEqual(wikipedia_languages('nonverbal cognition'), ('en',))

    def test_english_wiki_mode_makes_only_one_language_request(self):
        page = {'url': 'https://en.wikipedia.org/wiki/Thought', 'text': 'Published evidence'}
        with patch('search_worker.wikipedia', return_value=[page]) as wiki, patch('search_worker.google') as google:
            result = collect('nonverbal cognition', 'wiki', 'Thought')
        wiki.assert_called_once_with('nonverbal cognition', 'en', 'Thought')
        google.assert_not_called()
        self.assertEqual(result['pages'][0]['text'], 'Published evidence')

    def test_missing_public_article_is_an_empty_result_not_an_http_failure(self):
        with patch('search_worker._wikipedia_api', return_value=[]), \
                patch('research_transport.crawl_public_page', side_effect=http_error(404)):
            self.assertEqual(wikipedia('missing topic', 'en', 'Missing article'), [])

    def test_api_429_is_not_followed_by_another_request_to_same_language(self):
        with patch('search_worker._wikipedia_api', side_effect=http_error(429)), \
                patch('research_transport.crawl_public_page') as article:
            with self.assertRaisesRegex(WikipediaFetchError, 'API HTTP 429'):
                wikipedia('thought', 'en', 'Thought')
        article.assert_not_called()

    def test_both_failed_stages_report_codes_without_private_exception_details(self):
        with patch('search_worker._wikipedia_api', side_effect=http_error(403)), \
                patch('research_transport.crawl_public_page', side_effect=http_error(503)):
            with self.assertRaises(WikipediaFetchError) as caught:
                wikipedia('thought', 'en', 'Thought')
        self.assertIn('article HTTP 503', str(caught.exception))
        self.assertIn('API HTTP 403', str(caught.exception))
        self.assertNotIn('never-log', str(caught.exception))

    def test_language_and_http_status_survive_collection_without_stopping_other_search(self):
        with patch('search_worker.wikipedia', side_effect=WikipediaFetchError('API HTTP 429')), \
                patch('search_worker.google', return_value=[]), patch('search_worker.alternative_search', return_value=[]):
            result = collect('nonverbal cognition', 'wiki', 'Thought')
        self.assertTrue(any('Wikipedia [en]: API HTTP 429' in text for text in result['warnings']))
        self.assertEqual(result['pages'], [])

    def test_safe_error_labels_include_http_status(self):
        self.assertEqual(wiki_error_label(http_error(403)), 'HTTP 403')
        self.assertEqual(wiki_error_label(TimeoutError('private detail')), 'timeout')
