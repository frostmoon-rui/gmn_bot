import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from bot_extensions import Extensions
from reply_style import compact_reply, concise_source, parse_search_request, english_provider_text, localize_provider_data
from search_worker import collect


class ResearchStyleTests(unittest.TestCase):
    def test_english_sentence_boundary_preserves_decimal_values(self):
        text = 'The measured value is 12.5 degrees. ' + 'Additional details follow. ' * 10
        self.assertEqual(compact_reply(text, 50), 'The measured value is 12.5 degrees.')

    def test_wire_labels_translate_without_changing_schema(self):
        label = '\u6674\u8f6c\u591a\u4e91'
        self.assertEqual(english_provider_text(label), 'clear becoming cloudy')
        self.assertEqual(english_provider_text('\u96e8'), 'rain')
        self.assertEqual(english_provider_text('\u96f7\u9635\u96e8\u5e76\u4f34\u6709\u51b0\u96f9'), 'thundershowers with hail')
        source = {'\u5929\u6c14': label, 'temp': 0, 'desc': '\u672a\u6536\u5f55\u5929\u6c14'}
        localized = localize_provider_data(source)
        self.assertEqual(localized, {'\u5929\u6c14': 'clear becoming cloudy', 'temp': 0, 'desc': 'unknown'})
        self.assertEqual(source['desc'], '\u672a\u6536\u5f55\u5929\u6c14')

    def test_clean_short_answer_and_canonical_video_source(self):
        result = compact_reply('**Conclusion**: ' + 'This content is explanatory.'*100 + '\nSource: https://example.com/?utm_source=test', 180)
        self.assertLessEqual(len(result), 180)
        self.assertNotIn('http', result)
        self.assertNotIn('**', result)
        self.assertEqual(concise_source('https://www.bilibili.com/video/BV1joa361Efo/?buvid=secret&p=1&timestamp=999'),
                         'https://www.bilibili.com/video/BV1joa361Efo')
        self.assertTrue(concise_source('https://www.bilibili.com/video/BV1joa361Efo/?p=2&buvid=secret').endswith('?p=2'))

    def test_model_plan_is_strict_and_cannot_request_scripts(self):
        plan = parse_search_request('[SEARCH_REQUEST: {"query":"What capacitors do", "mode":"wiki", "topic":"Capacitance", "script":"forbidden"}]')
        self.assertEqual(set(plan), {'query', 'mode', 'topic'})
        for text in ['This is a quote: [SEARCH_REQUEST: {"query":"x","mode":"wiki"}]',
                     '[SEARCH_REQUEST: {"query":"x","mode":"shell"}]',
                     '[SEARCH_REQUEST: {"query":[],"mode":"wiki"}]']:
            self.assertIsNone(parse_search_request(text))

    def test_wiki_selected_by_model_returns_without_google(self):
        with patch('search_worker.wikipedia', return_value=[{'url': 'https://zh.wikipedia.org/wiki/Capacitance',
                'title': 'Capacitance', 'text': 'A component that stores charge.'}]) as wiki, patch('search_worker.google') as google:
            result = collect('Its uses and principles', 'wiki', 'Capacitance')
            self.assertTrue(result['pages'])
            google.assert_not_called()
            self.assertEqual(wiki.call_args.args[2], 'Capacitance')


class SearchReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = SimpleNamespace(load_json_data=lambda name, default: copy.deepcopy(default),
            call_mimo_direct_question=AsyncMock(return_value='**Definition**: ' + 'Capacitors store charge.'*80 + '\nhttps://long.example/a?track=1'),
            describe_api_error=str)
        self.ext = Extensions(self.bot)
        self.ext.python_search = AsyncMock(return_value={'pages': [{'url': 'https://zh.wikipedia.org/wiki/Capacitance',
            'title': 'Capacitance', 'text': 'Capacitors store charge'}], 'warnings': []})
        self.ext.send = AsyncMock()

    async def test_report_is_bounded_without_appended_links_sources_are_opt_in(self):
        await self.ext.search_job(None, 'group', 20, 10, 'Principles of capacitors', mode='wiki', topic='Capacitance')
        answer = self.ext.send.call_args.args[-1]
        self.assertLessEqual(len(answer), 240)
        self.assertNotIn('https://', answer)
        sources = await self.ext.command('view sources', None, 'group', 20, 10, False, False, mentioned=True)
        self.assertIn('wikipedia.org', sources)
        other = await self.ext.command('view sources', None, 'group', 21, 10, False, False, mentioned=True)
        self.assertNotIn('wikipedia.org', other)

    async def test_latest_refuses_wiki_only_result_even_without_latest_keyword(self):
        await self.ext.search_job(None, 'group', 20, 10, 'Its current regulations', mode='latest')
        self.bot.call_mimo_direct_question.assert_not_called()
        self.assertIn('not been verified', self.ext.send.call_args.args[-1])
