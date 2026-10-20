"""Bounded public comment sampling tests; no real comments or network calls."""
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from video_comments import fetch_comments
from video_summary import PublicVideoFetcher


def comment(identifier, text, children=None):
    return {'rpid': identifier, 'content': {'message': text}, 'replies': children or []}


class VideoCommentTests(unittest.TestCase):
    def test_pinned_hot_and_embedded_replies_are_merged_and_deduplicated(self):
        def api(_):
            return {'page': {'count': 2, 'size': 20}, 'top': {'upper': comment(1, 'Pinned')},
                    'hots': [comment(2, 'Hot')], 'replies': [comment(1, 'Pinned'),
                    comment(3, 'Discussion', [comment(4, 'Nested'), comment(5, 'Hot')])]}
        texts, stats = fetch_comments(api, 123)
        self.assertEqual(texts, ['Pinned', 'Hot', 'Discussion', 'Nested'])
        self.assertEqual(stats['nested_collected'], 1)
        self.assertEqual(stats['reported_total'], 2)

    def test_a_short_page_with_a_large_reported_total_still_gets_paged(self):
        calls = []
        def api(url):
            query = parse_qs(urlsplit(url).query)
            sort, page = int(query['sort'][0]), int(query['pn'][0])
            calls.append((sort, page))
            self.assertEqual(query['ps'], ['20'])
            return {'page': {'count': 1322, 'size': 20},
                    'replies': [comment(sort * 100 + page, f'Comment {sort}-{page}')]}
        texts, stats = fetch_comments(api, 123)
        self.assertEqual(calls, [(2, 1), (0, 1), (2, 2), (0, 2)])
        self.assertEqual(len(texts), 4)
        self.assertEqual(stats['requests'], 4)
        self.assertEqual(stats['stop_reason'], 'request-limit')

    def test_three_top_level_comments_can_expose_eight_additional_replies(self):
        roots = [comment(1, 'Root A', [comment(10+i, f'Child A{i}') for i in range(4)]),
                 comment(2, 'Root B', [comment(20+i, f'Child B{i}') for i in range(4)]),
                 comment(3, 'Root C')]
        texts, stats = fetch_comments(lambda _: {'page': {'count': 1322, 'size': 20}, 'replies': roots}, 123)
        self.assertEqual(len(texts), 11)
        self.assertEqual(stats['nested_collected'], 8)
        self.assertEqual(stats['requests'], 4)
        self.assertEqual(stats['stop_reason'], 'repeated-public-sample')

    def test_sample_count_is_capped_and_text_is_cleaned(self):
        roots = [comment(i+1, f'<b>Comment {i}</b>' + 'x' * 400) for i in range(50)]
        texts, stats = fetch_comments(lambda _: {'replies': roots}, 123, max_comments=12)
        self.assertEqual(len(texts), 12)
        self.assertTrue(all(len(text) <= 240 and '<b>' not in text for text in texts))
        self.assertEqual(stats['requests'], 1)
        self.assertEqual(stats['stop_reason'], 'sample-limit')

    def test_threads_expand_only_when_inline_replies_are_incomplete(self):
        calls = []
        def api(url):
            query = parse_qs(urlsplit(url).query)
            calls.append(query)
            if 'root' in query:
                return {'page': {'count': 3, 'size': 20}, 'replies': [
                    comment(10, 'Inline'), comment(11, 'Extra one'), comment(12, 'Extra two')]}
            root = comment(1, 'Root', [comment(10, 'Inline')])
            root['rcount'] = 3
            return {'page': {'count': 1, 'size': 20}, 'replies': [root]}
        texts, stats = fetch_comments(api, 123)
        self.assertEqual(texts, ['Root', 'Inline', 'Extra one', 'Extra two'])
        self.assertEqual(stats['thread_requests'], 1)
        self.assertEqual(stats['nested_collected'], 3)
        self.assertEqual(sum('root' in query for query in calls), 1)
        self.assertEqual(stats['requests'], 3)

    def test_small_visible_subset_is_not_labeled_as_all_comments(self):
        texts, stats = fetch_comments(lambda _: {'page': {'count': 1322}, 'replies': []}, 123)
        self.assertEqual(texts, [])
        self.assertEqual(stats['reported_total'], 1322)
        self.assertEqual(stats['stop_reason'], 'public-sample-limited')

    def test_failure_stops_requests_and_retains_the_acquired_sample(self):
        calls = []
        def api(url):
            calls.append(url)
            if len(calls) == 2:
                raise RuntimeError('closed or rate-limited')
            return {'replies': [comment(1, 'Keep this')], 'page': {'count': 200}}
        texts, stats = fetch_comments(api, 123)
        self.assertEqual(texts, ['Keep this'])
        self.assertEqual(len(calls), 2)
        self.assertTrue(stats['request_failed'])

    def test_time_budget_is_checked_before_another_request(self):
        with patch('video_comments.time.monotonic', side_effect=[0, 0, 20]):
            texts, stats = fetch_comments(lambda _: {'replies': [comment(1, 'Keep this')]}, 123, time_budget=15)
        self.assertEqual(texts, ['Keep this'])
        self.assertEqual(stats['requests'], 1)
        self.assertEqual(stats['stop_reason'], 'time-budget')

    def test_empty_and_unexpected_shapes_do_not_fabricate_comments(self):
        texts, stats = fetch_comments(lambda _: {'replies': None, 'top': None, 'hots': 'not a list'}, 123)
        self.assertEqual(texts, [])
        self.assertEqual(stats['requests'], 2)
        texts, stats = fetch_comments(lambda _: [], 123)
        self.assertEqual(texts, [])
        self.assertTrue(stats['request_failed'])

    def test_bilibili_metadata_keeps_the_expanded_sample_and_sampling_status(self):
        fetcher = PublicVideoFetcher()
        def api(url):
            if 'web-interface/view' in url:
                return {'aid': 123, 'title': 'Title', 'desc': 'Description'}
            return {'replies': [comment(1, 'Root', [comment(2, 'Child')])]}
        fetcher.api_json = api
        result = fetcher.bilibili('https://www.bilibili.com/video/BV1234567890')
        self.assertEqual(result['comments'], ['Root', 'Child'])
        self.assertEqual(result['comment_sample']['collected'], 2)
        self.assertEqual(result['title'], 'Title')
