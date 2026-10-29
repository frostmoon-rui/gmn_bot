"""Fixture-only official bulletin and cadence tests; no live API requests."""
import asyncio
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch

from earthquake_feed import EarthquakeFeed, OFFICIAL_URL, FALLBACK_URL, REPORT_INTERVAL, TZ, normalize, matches_province


class OfficialTests(unittest.IsolatedAsyncioTestCase):
    def test_subscriber_region_matches_codes_across_languages(self):
        self.assertTrue(matches_province({'location':'Sichuan'}, {'location':'四川宜宾市'}))
        self.assertFalse(matches_province({'location':'Shaanxi'}, {'location':'山西太原市'}))
        self.assertFalse(matches_province({'location':'Sichuan Beijing'}, {'location':'四川宜宾市'}))
        self.assertFalse(matches_province({'location':'Sichuan Road','google_location':{
            'original':'Sichuan Road','country_code':'US'}}, {'location':'四川宜宾市'}))

    def row(self,**changes):
        row={'id':'official-id','time':(datetime.now(TZ)-timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S'),
             'location':'Example Region','magnitude':'4.9','latitude':'30','longitude':'100','depth':'12'}
        return dict(row,**changes)

    async def test_official_is_preferred_and_concurrent_queries_share_30min_refresh(self):
        calls=[]
        def fetch(url):
            calls.append(url);return [self.row()]
        feed=EarthquakeFeed(fetch)
        results=await asyncio.gather(*(feed.reports() for _ in range(6)))
        self.assertEqual(calls,[OFFICIAL_URL])
        self.assertEqual(results[0][0]['depth'],12)
        self.assertIn('official',results[0][0]['source'])
        self.assertEqual(REPORT_INTERVAL,1800)
        feed.report_cache=(feed.report_cache[0]-1801,feed.report_cache[1])
        feed.cache.clear()
        await feed.reports()
        self.assertEqual(calls,[OFFICIAL_URL,OFFICIAL_URL])

    async def test_fallback_is_labelled_and_failure_also_waits_30min(self):
        calls=[]
        def fetch(url):
            calls.append(url)
            if url==OFFICIAL_URL:
                raise OSError('fixture unavailable')
            return {'No1':self.row()}
        feed=EarthquakeFeed(fetch)
        events=await feed.reports()
        self.assertIn('relayed',events[0]['source'])
        await feed.reports()
        self.assertEqual(calls,[OFFICIAL_URL,FALLBACK_URL])

    async def test_age_recalculated_not_frozen_and_legacy_delivery_ids_preserved(self):
        row=self.row(); feed=EarthquakeFeed(lambda url:[row])
        first=(await feed.reports())[0]
        later=datetime.now(TZ)+timedelta(minutes=20)
        with patch('earthquake_feed.datetime', wraps=datetime) as clock:
            clock.now.return_value=later
            second=(await feed.reports())[0]
        self.assertGreater(second['age_seconds'],first['age_seconds']+1100)
        legacy=normalize(row,'cenc_report')
        self.assertEqual(first['id'],legacy['id'])
        self.assertTrue(set(first['dedup_ids']) & set(legacy['dedup_ids']))

    async def test_malformed_measurements_do_not_become_valid_reports(self):
        for row in (self.row(magnitude=True),self.row(location=None),self.row(latitude=999),
                    self.row(magnitude='NaN'),self.row(depth=-10)):
            with self.assertRaises((TypeError,ValueError)):
                normalize(row,'official')

    async def test_empty_official_list_is_not_fabricated_from_fallback(self):
        calls=[]
        def fetch(url):calls.append(url);return []
        self.assertEqual(await EarthquakeFeed(fetch).reports(),[])
        self.assertEqual(calls,[OFFICIAL_URL])
