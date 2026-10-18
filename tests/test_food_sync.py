import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from food_sources import FoodSources


class FoodSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'food_map').mkdir()
        self.source = {'id': 'example', 'name': 'Source', 'url': 'https://example.org/map.json', 'enabled': True}
        self.config = self.root / 'food_map' / 'sources.json'
        self.config.write_text(json.dumps({'sources': [self.source]}), encoding='utf-8')
        self.document = {'schemaVersion': 1, 'spots': [{'name': 'Venue', 'city': 'city', 'lng': 116.4, 'lat': 39.9}]}

    def test_restart_restores_last_success(self):
        first = FoodSources(self.root, fetch=lambda url: self.document)
        first.refresh()
        second = FoodSources(self.root, fetch=lambda url: self.document)
        self.assertEqual(second.cache, first.cache)

    def test_failure_keeps_cache_and_disabled_source_removed(self):
        sync = FoodSources(self.root, fetch=lambda url: self.document)
        sync.refresh()
        def fail(url):
            raise OSError('offline')
        sync.fetch = fail
        sync.refresh()
        self.assertEqual(len(sync.cache), 1)
        self.assertEqual(len(FoodSources(self.root).cache), 1)
        self.config.write_text(json.dumps({'sources': []}), encoding='utf-8')
        sync.refresh()
        self.assertEqual(sync.cache, [])

    def test_background_starts_without_page_request(self):
        called = threading.Event()
        def fetch(url):
            called.set()
            return self.document
        sync = FoodSources(self.root, fetch=fetch)
        sync.start()
        try:
            self.assertTrue(called.wait(2))
            original = sync.worker
            sync.start()
            self.assertIs(sync.worker, original)
        finally:
            sync.stop()
            sync.worker.join(2)
        self.assertFalse(sync.worker.is_alive())

    def test_periodic_refresh_and_interval_bounds(self):
        sync = FoodSources(self.root, fetch=lambda url: self.document)
        with patch.object(sync, 'refresh') as refresh, patch.object(sync.stopped, 'wait', side_effect=[False, True]) as wait:
            sync._loop()
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(wait.call_args.args, (86400,))
        self.config.write_text(json.dumps({'sync_interval_seconds': 1}), encoding='utf-8')
        with patch.object(sync, 'refresh'), patch.object(sync.stopped, 'wait', return_value=True) as wait:
            sync._loop()
        self.assertEqual(wait.call_args.args, (60,))
