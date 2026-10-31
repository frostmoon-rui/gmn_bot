"""Offline disposable-file boundaries, expiry and shared-search regressions."""
import ast
import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from runtime_maintenance import cleanup_generated,release_expired,DAY


class MaintenanceTests(unittest.TestCase):
    def test_dry_run_and_apply_preserve_backups_records_and_active_files(self):
        now=2_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('logs/startup/old.log','logs/startup/active.log','backup/old.log',
                         'stickers/original.png','people.json','logs/startup/source.py'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'keep')
                os.utime(path,(now-30*DAY,now-30*DAY))
            os.utime(root/'logs/startup/active.log',(now-2,now-2))
            result=cleanup_generated(root,now=now)
            self.assertEqual(result['files'],1)
            self.assertTrue((root/'logs/startup/old.log').exists())
            result=cleanup_generated(root,now=now,dry_run=False)
            self.assertEqual(result['bytes'],4)
            self.assertFalse((root/'logs/startup/old.log').exists())
            for name in ('logs/startup/active.log','backup/old.log','stickers/original.png','people.json','logs/startup/source.py'):
                self.assertTrue((root/name).exists())

    def test_budget_expires_old_files_but_never_recent_ones(self):
        now=2_000_000_000
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);directory=root/'logs/startup';directory.mkdir(parents=True)
            for name,age in (('old.log',2*DAY),('new.log',1)):
                path=directory/name;path.write_bytes(b'x'*12);os.utime(path,(now-age,now-age))
            with patch('runtime_maintenance.POLICIES',(('logs/startup',('*.log',),14*DAY,16),)):
                self.assertEqual(cleanup_generated(root,now=now,dry_run=False)['files'],1)
            self.assertTrue((directory/'new.log').exists())

    def test_linked_directory_cannot_escape_the_project(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as outside:
            root=Path(folder);target=Path(outside);old=target/'old.log';old.write_bytes(b'keep')
            os.utime(old,(1,1))
            (root/'logs').mkdir()
            try:
                (root/'logs/startup').symlink_to(target,target_is_directory=True)
            except OSError:
                self.skipTest('Directory symlinks are unavailable for this Windows account')
            self.assertEqual(cleanup_generated(root,dry_run=False)['files'],0)
            self.assertTrue(old.exists())

    def test_memory_expiry_does_not_clear_live_jobs_or_conversation(self):
        live=object()
        vision=SimpleNamespace(cache={'old':(1,'old'),'new':(200,'new')},backoff={'done':1,'waiting':200},inflight={'live':live})
        bot=SimpleNamespace(vision_recognizer=vision,chat_histories={'person':['context']})
        self.assertEqual(release_expired(bot,now=100),2)
        self.assertEqual(vision.cache,{'new':(200,'new')})
        self.assertIs(vision.inflight['live'],live)
        self.assertEqual(bot.chat_histories,{'person':['context']})


class SearchReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_matching_search_reuses_result_without_caching_chat_answers(self):
        path=Path(__file__).resolve().parents[1]/'bot_extensions.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Extensions')
        method=next(node for node in cls.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='python_search')
        scope={'asyncio':asyncio}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'fixture','exec'),scope)
        call=AsyncMock(return_value={'pages':[{'title':'Public source'}]})
        worker=SimpleNamespace(search_cache={},search_gate=asyncio.Semaphore(1),_python_search_process=call)
        result=await asyncio.gather(*(scope['python_search'](worker,'weather models') for _ in range(3)))
        self.assertEqual(call.call_count,1)
        self.assertTrue(all(row==result[0] for row in result))
