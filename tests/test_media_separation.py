import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from media_separation import (
    MediaError, MediaSeparation, _connect_public, decode_silk, kugou_search_url, media_source, parse_command,
    fetch_napcat_media, is_qq_video_url, open_public_response, public_address, separate_media,
)


class MediaTests(unittest.TestCase):
    def test_input_modes_and_cq_escaping(self):
        message = {'message': '[CQ:file,file=sample&#44;track.mp3,file_id=opaque,file_size=500]'}
        self.assertEqual(media_source(message, ''), ('file', 'opaque'))
        message = {'message': [{'type': 'record', 'data': {'file': 'opaque'}}]}
        self.assertEqual(media_source(message, ''), ('record', 'opaque'))
        self.assertEqual(media_source({}, '[CQ:image,url=https://image.invalid/] https://example.test/x?a=1&amp;b=2'),
                         ('url', 'https://example.test/x?a=1&b=2'))
        self.assertEqual(parse_command('/separate audio voice: https://example.test/file', 'separate audio', 'voice'),
                         (True, 'https://example.test/file'))
        with self.assertRaisesRegex(MediaError, 'multiple'):
            media_source({}, 'https://one.test/file https://two.test/file')
        self.assertTrue(kugou_search_url('Artist & Song').endswith('Artist%20%26%20Song'))

    def test_qq_silk_prefixed_voice_uses_the_local_decoder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'media'
            source.write_bytes(bytes.fromhex('03232153494c4b5f5633'))
            with patch('media_separation.shutil.which', return_value='node'):
                with self.assertRaisesRegex(MediaError, 'voice_decoder'):
                    decode_silk(source, root, None)

    def test_rejects_nonpublic_dns_targets_before_connecting(self):
        for ip in ('127.0.0.1', '192.168.1.1', '198.18.0.1', '169.254.169.254', '::ffff:10.0.0.1',
                   '64:ff9b::a00:1', 'ff02::1'):
            self.assertFalse(public_address(ip))
        self.assertTrue(public_address('8.8.8.8'))
        result = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
        with (
            patch('media_separation.socket.getaddrinfo', return_value=result),
            patch('media_separation.socket.create_connection') as connect,
        ):
            with self.assertRaisesRegex(MediaError, 'source'):
                _connect_public('http://media.example.test/file')
            connect.assert_not_called()
        fake_ip = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('198.18.0.1', 80))]
        with (
            patch('media_separation.socket.getaddrinfo', return_value=fake_ip),
            patch('media_separation._doh_addresses', return_value=('127.0.0.1',)) as resolve,
            patch('media_separation.socket.create_connection') as connect,
        ):
            with self.assertRaisesRegex(MediaError, 'source'):
                _connect_public('http://media.example.test/file')
            resolve.assert_called_once_with('media.example.test')
            connect.assert_not_called()

    def test_qq_media_redirect_cannot_leave_the_qq_cdn(self):
        conn, response = Mock(), Mock(status=302)
        response.getheader.return_value = 'https://outside.example/video.mp4'
        with patch('media_separation._connect_public', return_value=(conn, response)) as connect:
            with self.assertRaisesRegex(MediaError, 'source'):
                with open_public_response('https://multimedia.nt.qq.com.cn/video', allowed_url=is_qq_video_url):
                    self.fail('Unexpected media response')
            connect.assert_called_once_with('https://multimedia.nt.qq.com.cn/video')

    def test_silent_video_never_reaches_spleeter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = SimpleNamespace(returncode=1, stderr=b'Stream map 0:a:0 matches no streams.')
            with patch('media_separation._run', return_value=result) as run:
                source = root / 'video.mp4'
                source.write_bytes(b'video')
                with self.assertRaisesRegex(MediaError, 'audio'):
                    separate_media('spleeter', 'ffmpeg', source, root)
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0][0], 'ffmpeg')


class MediaDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_quoted_video_uses_qq_playback_when_get_file_fails(self):
        url = 'https://multimedia.nt.qq.com.cn/video'
        original = {'message': [{'type': 'video', 'data': {'file': 'opaque', 'url': url}}], 'group_id': 12}
        actions = []
        tasks = []

        async def send(raw, **kwargs):
            request = json.loads(raw)
            action = request['action']
            actions.append(action)
            if action == 'get_msg':
                return original
            if action == 'get_file':
                raise RuntimeError('NapCat could not resolve the file')
            if action == 'upload_group_file':
                self.assertEqual(Path(request['params']['file']).read_bytes(), b'separated')

        def download(source, target, *, allowed_url):
            self.assertEqual(source, url)
            self.assertTrue(allowed_url(url))
            target.write_bytes(b'QQ video')

        def separate(engine, ffmpeg, incoming, workspace, silk_module):
            self.assertEqual(incoming.read_bytes(), b'QQ video')
            outputs = {name: workspace / f'{name}.mp3' for name in ('vocals', 'accompaniment')}
            for output in outputs.values():
                output.write_bytes(b'separated')
            return 12, outputs

        with (
            tempfile.TemporaryDirectory() as temp,
            patch('media_separation.download_media', side_effect=download),
            patch('media_separation.separate_media', side_effect=separate),
        ):
            executable = Path(temp) / 'tool.exe'
            executable.touch()
            event = {'message_type': 'group', 'group_id': 12, 'user_id': 34, 'self_id': 56}
            worker = MediaSeparation(executable, executable, lambda coro: tasks.append(asyncio.create_task(coro)), AsyncMock())
            self.assertIsNone(worker.start(SimpleNamespace(send=send), event, '42', None, False,
                belongs=lambda msg, src: msg['group_id'] == src['group_id'], text=lambda msg: ''))
            await tasks[0]
            self.assertEqual(actions, ['get_msg', 'get_file', 'upload_group_file', 'upload_group_file'])

    async def test_video_with_only_a_native_cache_path_is_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'nt_data' / 'Video' / 'clip.mp4'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'local QQ video')
            message = {'message': [{'type': 'video', 'data': {'url': str(source)}}]}
            call = AsyncMock()
            target = root / 'incoming'
            await fetch_napcat_media(call, message, media_source(message, ''), target)
            call.assert_not_awaited()
            self.assertEqual(target.read_bytes(), b'local QQ video')

    async def test_voice_reply_fetches_and_returns_two_files_and_records(self):
        sent = []
        tasks = []
        notices = []

        async def notify(*args):
            notices.append(args[-1])

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'voice.wav'
            source.write_bytes(b'audio')

            async def send(payload, **kwargs):
                req = json.loads(payload)
                action, params = req['action'], req['params']
                sent.append((action, params))
                if action == 'get_msg':
                    return {'message': '[CQ:record,file=voice-id]', 'group_id': 12}
                if action == 'get_file':
                    return {'file': str(source)}
                if action == 'upload_group_file':
                    self.assertTrue(Path(params['file']).is_file())

            def separate(engine, ffmpeg, incoming, workspace, silk_module):
                self.assertEqual(incoming.read_bytes(), b'audio')
                outputs = {name: workspace / f'{name}.mp3' for name in ('vocals', 'accompaniment')}
                for output in outputs.values():
                    output.write_bytes(b'separated')
                return 3, outputs

            event = {'message_type': 'group', 'group_id': 12, 'user_id': 34, 'self_id': 56}
            job = MediaSeparation(source, source, lambda coro: tasks.append(asyncio.create_task(coro)), notify)
            with patch('media_separation.separate_media', side_effect=separate):
                self.assertIsNone(job.start(SimpleNamespace(send=send), event, '42', None, True,
                                            belongs=lambda message, event: message['group_id'] == event['group_id'],
                                            text=lambda message: message['message']))
                await tasks[0]
            self.assertEqual([action for action, _ in sent], [
                'get_msg', 'get_file', 'upload_group_file', 'upload_group_file', 'send_group_msg', 'send_group_msg',
            ])
            self.assertEqual(sent[1][1], {'file': 'voice-id'})
            self.assertFalse(job.queue)
            self.assertEqual(notices, [])
