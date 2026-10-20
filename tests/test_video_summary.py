import asyncio
import html
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from video_summary import extract_videos, webpage_metadata, VideoSummaries, PublicVideoFetcher


def card(url='https://b23.tv/Example'):
    return {'app': 'com.tencent.miniapp_01', 'meta': {'detail_1': {
        'title': 'Test video', 'desc': 'Description', 'qqdocurl': url, 'preview': 'https://example.com/cover.jpg'}}}


def event(message, user=10, identifier=100):
    return {'post_type': 'message', 'message_type': 'group', 'group_id': 20,
            'user_id': user, 'message_id': identifier, 'self_id': 999, 'message': message}


class VideoExtractTests(unittest.TestCase):
    def test_json_object_and_string_with_cq_escaping(self):
        for data in (card(), json.dumps(card(), ensure_ascii=False)):
            result = extract_videos(event([{'type': 'json', 'data': {'data': data}}]))
            self.assertEqual(result[0]['url'], 'https://b23.tv/Example')
            self.assertEqual(result[0]['title'], 'Test video')
            self.assertEqual(len(result), 1)
        raw = json.dumps(card(), ensure_ascii=False).replace('&', '&amp;').replace('[', '&#91;').replace(']', '&#93;').replace(',', '&#44;')
        self.assertEqual(extract_videos({'raw_message': '[CQ:json,data=' + raw + ']'})[0]['url'], 'https://b23.tv/Example')

    def test_xml_plain_link_and_raw_video(self):
        result = extract_videos(event([{'type': 'xml', 'data': {'data':
            '<msg url="https://www.bilibili.com/video/BV1234567890"><item><title>Title</title></item></msg>'}}]))
        self.assertEqual(result[0]['title'], 'Title')
        result = extract_videos({'message': 'https://www.youtube.com/watch?v=example'})
        self.assertEqual(len(result), 1)
        raw = extract_videos(event([{'type': 'video', 'data': {'file': 'opaque-file-id'}}]))
        self.assertEqual(raw[0]['file'], 'opaque-file-id')
        self.assertEqual(raw[0]['url'], '')

    def test_nonvideo_and_cover_urls_are_not_treated_as_videos(self):
        self.assertEqual(extract_videos(event([{'type': 'json', 'data': {'data': {'url': 'https://example.com/news'}}}])), [])
        self.assertEqual(extract_videos({'message': 'https://bilibili.com.evil.example/video/1'}), [])

    def test_webpage_metadata_distinguishes_comments(self):
        raw = '<meta property="og:title" content="Video title"><meta name="description" content="Description">' + \
            '<script type="application/ld+json">' + json.dumps({'@type': 'VideoObject',
                'name': 'Official title', 'description': 'Official description', 'comment': [{'text': 'Viewer comment'}]}) + '</script>'
        result = webpage_metadata(raw)
        self.assertEqual(result['title'], 'Official title')
        self.assertEqual(result['comments'], ['Viewer comment'])
        self.assertEqual(webpage_metadata('<title>x</title>')['comments'], [])

    def test_youtube_player_extract_does_not_execute_javascript(self):
        raw = '<script>var ytInitialPlayerResponse = ' + json.dumps({'videoDetails': {
            'title': 'Title', 'shortDescription': 'Description', 'author': 'Author'}}) + '; anything()</script>'
        self.assertEqual(webpage_metadata(raw)['description'], 'Description')

    def test_public_network_guard(self):
        with patch('video_summary.socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                PublicVideoFetcher.validate_target('https://example.com/video')
        with self.assertRaises(ValueError):
            PublicVideoFetcher.validate_target('file:///secret')

    def test_bilibili_comments_failure_keeps_title(self):
        fetcher = PublicVideoFetcher()
        calls = []
        def api(url):
            calls.append(url)
            if 'reply?' in url:
                raise RuntimeError('closed')
            return {'aid': 123, 'title': 'Title', 'desc': 'Description', 'owner': {'name': 'UP'}}
        fetcher.api_json = api
        data = fetcher.bilibili('https://www.bilibili.com/video/BV1234567890')
        self.assertEqual(data['title'], 'Title')
        self.assertEqual(data['comments'], [])
        self.assertTrue(data['warnings'])


class VideoFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.jobs, self.sent = [], []
        def spawn(coro):
            task = asyncio.create_task(coro)
            self.jobs.append(task)
            return task
        async def send(ws, kind, group, user, text):
            self.sent.append(text)
        self.ai = AsyncMock(return_value='The video may discuss test content.')
        self.bot = SimpleNamespace(extensions=SimpleNamespace(spawn=spawn, send=send), call_mimo_direct_question=self.ai)
        self.service = VideoSummaries(self.bot)
        self.ws = SimpleNamespace(send=AsyncMock())

    async def asyncTearDown(self):
        if self.jobs:
            await asyncio.gather(*self.jobs, return_exceptions=True)

    async def test_share_silent_then_request_summarizes(self):
        original = event([{'type': 'json', 'data': {'data': card()}}])
        self.service.observe(original)
        self.assertIsNone(await self.service.command(original, '', False, self.ws))
        self.assertEqual(self.sent, [])
        self.service.fetcher.collect = lambda candidate: {'url': candidate['url'], 'title': 'Title',
            'description': 'Description', 'comments': ['A viewer comment'], 'warnings': []}
        reply = await self.service.command(event('summarize video'), 'summarize video', True, self.ws)
        self.assertIn('background', reply)
        await asyncio.gather(*self.jobs)
        self.assertIn('video footage was not watched', self.sent[0])
        self.assertEqual(self.ai.call_args.args[1], 'mimo-v2.6-flash')
        self.assertIn('in English', self.ai.call_args.args[0])
        self.assertFalse(self.service.busy)

    async def test_long_video_reply_never_appends_share_url(self):
        original = event('https://www.bilibili.com/video/BV1joa361Efo/?buvid=track')
        self.service.fetcher.collect = lambda candidate: {'url': candidate['url'], 'title': 'Title',
            'description': '-', 'comments': [], 'warnings': ['Comments unavailable']}
        self.ai.return_value = '**What the video may discuss**: ' + 'The title suggests an explanation of how corn responds to heat.'*100 + '\nhttps://example.com/long?x=1'
        await self.service.command(original, 'summarize video', True, self.ws)
        await asyncio.gather(*self.jobs)
        self.assertLessEqual(len(self.sent[0]), 180)
        self.assertNotIn('http', self.sent[0])
        self.assertNotIn('**', self.sent[0])

    async def test_explicit_reply_uses_get_msg_and_rejects_other_group(self):
        reply = event([{'type': 'reply', 'data': {'id': '321'}}])
        other = event([{'type': 'json', 'data': {'data': card()}}])
        other['group_id'] = 999
        self.ws.send.return_value = other
        response = await self.service.command(reply, 'summarize video', True, self.ws)
        self.assertIn('No video link was found', response)
        self.assertEqual(json.loads(self.ws.send.call_args.args[0])['action'], 'get_msg')
        self.assertEqual(self.jobs, [])

    async def test_cached_explicit_quote_wins_over_a_newer_card(self):
        self.service.observe(event([{'type': 'json', 'data': {'data': card('https://b23.tv/Quoted')}}], identifier=321))
        self.service.observe(event([{'type': 'json', 'data': {'data': card('https://b23.tv/Newer')}}], identifier=322))
        selected = []
        def collect(candidate):
            selected.append(candidate['url'])
            return {'url': candidate['url'], 'title': 'Published title', 'description': 'Published description', 'comments': [], 'warnings': []}
        self.service.fetcher.collect = collect
        quoted = event([{'type': 'reply', 'data': {'id': '321'}},
                        {'type': 'at', 'data': {'qq': '999'}},
                        {'type': 'text', 'data': {'text': 'summarize video'}}])
        await self.service.command(quoted, 'summarize video', True, self.ws)
        await asyncio.gather(*self.jobs)
        self.assertEqual(selected, ['https://b23.tv/Quoted'])
        self.ws.send.assert_not_called()
        self.ai.assert_awaited_once()

    async def test_raw_qq_video_not_sent_to_model(self):
        original = event([{'type': 'video', 'data': {'url': 'https://example.com/raw.mp4', 'file': 'id'}}])
        await self.service.command(original, 'summarize video', True, self.ws)
        await asyncio.gather(*self.jobs)
        self.ai.assert_not_called()
        self.assertIn('native QQ video', self.sent[0])

    async def test_same_group_other_person_does_not_replace_own_recent_card(self):
        self.service.observe(event([{'type': 'json', 'data': {'data': card('https://b23.tv/One')}}], user=10))
        self.service.observe(event([{'type': 'json', 'data': {'data': card('https://b23.tv/Two')}}], user=11, identifier=101))
        self.assertEqual(self.service.recent[(('group', 20), 10)][1][0]['url'], 'https://b23.tv/One')
