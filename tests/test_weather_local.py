"""Local weather chart regressions: periods, missing samples, DST and caller privacy."""
import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageDraw
from weather_queries import topic_request
from weather_local_data import fetch_local, local_series, LocalWeatherData
from weather_local_charts import draw_local
from weather_local_delivery import deliver_local_topic
from weather_delivery import report_font
from bot_actions import parse_action, dispatch

NOW = datetime(2026, 10, 29, 13, 45, tzinfo=timezone.utc)
POINT = {'status': 'resolved', 'latitude': 31.2, 'longitude': 121.5, 'coordinate_system': 'WGS84'}


def fixture(now=NOW):
    floor=now.replace(minute=0,second=0,microsecond=0)
    times=[int((floor+timedelta(hours=i)).timestamp()) for i in range(-36,73)]
    return {'timezone':'Asia/Shanghai','utc_offset_seconds':28800,
            'hourly':{'time':times,'temperature_2m':[10+i*.1 for i in range(len(times))],
                      'apparent_temperature':[8+i*.1 for i in range(len(times))],
                      'precipitation':[.04]*len(times),'precipitation_probability':[40]*len(times)},
            'hourly_units':{'temperature_2m':'°C','apparent_temperature':'°C','precipitation':'mm','precipitation_probability':'%'},
            'current':{'time':int(now.timestamp()),'interval':900,'temperature_2m':13.6,'precipitation':.01},
            'current_units':{'temperature_2m':'°C','precipitation':'mm'}}


class LocalTopicTests(unittest.TestCase):
    def test_bare_national_commands_are_not_replaced(self):
        self.assertEqual(topic_request('/24h temperature'),{'product':'temperature_24h','scope':'national','location':''})
        self.assertEqual(topic_request('/24h temperature Paris'),{'product':'temperature_24h','scope':'local','location':'Paris'})
        self.assertEqual(topic_request('/local current temperature')['scope'],'local')
        self.assertEqual(topic_request('/temperature forecast London')['product'],'temperature_forecast')
        self.assertIsNone(topic_request('current temperatureless'))

    def test_hourly_requests_use_real_epochs_and_monthly_requests_use_dates(self):
        calls=[]
        def request(url):
            calls.append(parse_qs(urlsplit(url).query))
            return fixture()
        fetch_local(POINT,False,request)
        fetch_local(POINT,True,request)
        fetch_local(dict(POINT,country_code='CN'),False,request)
        self.assertEqual(calls[0]['timeformat'],['unixtime'])
        self.assertEqual(calls[0]['past_days'],['1'])
        self.assertEqual(calls[1]['past_days'],['30'])
        self.assertNotIn('timeformat',calls[1])
        self.assertNotIn('minutely_15',calls[0])
        self.assertEqual(calls[2]['timezone'],['Asia/Shanghai'])

    def test_past_24_hours_are_sorted_and_zero_is_different_from_missing(self):
        raw=fixture()
        # Current 13 UTC is index 36. Keep zero; remove a different hour without bridging it.
        raw['hourly']['precipitation'][36]=0
        raw['hourly']['precipitation'][35]=None
        series=local_series(raw,'precipitation_24h',NOW)
        self.assertEqual(len(series['rows']),24)
        self.assertEqual(series['coverage'],23)
        self.assertEqual(series['rows'][-1]['precipitation'],0)
        self.assertNotIn('precipitation',series['rows'][-2])
        self.assertEqual(datetime.fromisoformat(series['rows'][-1]['time']).hour,21)

    def test_current_15_minute_precipitation_is_not_used_as_an_hour_total(self):
        series=local_series(fixture(),'precipitation_current',NOW)
        self.assertEqual(series['current']['precipitation'],.01)
        self.assertEqual(series['current']['interval_seconds'],900)
        self.assertTrue(all(row['precipitation']==.04 for row in series['rows']))

    def test_future_rain_uses_24_complete_future_intervals(self):
        series=local_series(fixture(),'precipitation_forecast',NOW)
        first=datetime.fromisoformat(series['rows'][0]['time'])
        self.assertGreaterEqual(first-timedelta(hours=1),NOW)
        self.assertEqual(len(series['rows']),24)
        self.assertTrue(all(row['kind']=='forecast' for row in series['rows']))

    def test_monthly_series_excludes_today_and_preserves_missing_days(self):
        raw={'timezone':'Asia/Shanghai','daily':{'time':['2026-10-28','2026-10-29'], 'precipitation_sum':[1.2,999]}}
        series=local_series(raw,'precipitation_30d',NOW)
        self.assertEqual(series['rows'][-1]['time'][:10],'2026-10-28')
        self.assertEqual(series['rows'][-1]['precipitation_sum'],1.2)
        self.assertEqual(series['coverage'],1)
        self.assertEqual(series['expected'],30)

    def test_dst_repeated_hours_keep_distinct_instants(self):
        now=datetime(2026,11,1,8,30,tzinfo=timezone.utc)
        raw=fixture(now);raw['timezone']='America/New_York'
        series=local_series(raw,'temperature_24h',now)
        repeats=[r['time'] for r in series['rows'] if r['time'].startswith('2026-11-01T01:00')]
        self.assertEqual(len(repeats),2)
        self.assertNotEqual(repeats[0][-6:],repeats[1][-6:])

    def test_stale_current_estimate_is_not_presented_as_live(self):
        raw=fixture();raw['current']['time']=int((NOW-timedelta(hours=3)).timestamp())
        self.assertEqual(local_series(raw,'temperature_current',NOW)['current'],{})

    def test_large_chart_labels_all_hourly_samples_and_keeps_small_rain_amounts(self):
        series=local_series(fixture(),'precipitation_24h',NOW)
        texts=[];original=ImageDraw.ImageDraw.text
        def record(draw,xy,text,*args,**kwargs):
            texts.append(str(text));return original(draw,xy,text,*args,**kwargs)
        with patch.object(ImageDraw.ImageDraw,'text',record):
            image=Image.open(BytesIO(draw_local(series,report_font)))
        self.assertEqual(image.width,1920)
        self.assertGreater(image.height,1400)
        self.assertEqual(sum(text=='0.04' for text in texts),24)
        self.assertEqual(sum(len(text)==5 and text[2]==':' for text in texts),24)
        self.assertTrue(any('model' in text for text in texts))


class LocalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_region_is_read_only_and_only_caller_receives_image(self):
        regions={'10':{'location':'PRIVATE PLACE','note':'keep'}}
        ext=SimpleNamespace(b=SimpleNamespace(regions_data=regions),resolve_weather_point=AsyncMock(return_value=POINT),
            local_weather_data=SimpleNamespace(get=AsyncMock(return_value=fixture())),send=AsyncMock())
        ws=SimpleNamespace(send=AsyncMock())
        with patch('weather_local_delivery.local_series',return_value=local_series(fixture(),'temperature_24h',NOW)), \
             patch('weather_local_delivery.draw_local',return_value=b'fixture-image'):
            await deliver_local_topic(ext,ws,'group',20,10,'temperature_24h')
        ext.resolve_weather_point.assert_awaited_once_with('PRIVATE PLACE')
        self.assertEqual(regions,{'10':{'location':'PRIVATE PLACE','note':'keep'}})
        payload=json.loads(ws.send.call_args.args[0])
        self.assertEqual(payload['params']['group_id'],20)
        self.assertNotIn('PRIVATE PLACE',ws.send.call_args.args[0])

    async def test_missing_binding_asks_for_city_without_fetching(self):
        ext=SimpleNamespace(b=SimpleNamespace(regions_data={}),send=AsyncMock(),resolve_weather_point=AsyncMock())
        await deliver_local_topic(ext,None,'private',None,10,'temperature_current')
        ext.send.assert_awaited_once()
        ext.resolve_weather_point.assert_not_called()

    async def test_local_dataset_is_shared_by_multiple_hourly_products(self):
        store=LocalWeatherData()
        with patch('weather_local_data.fetch_local',return_value=fixture()) as fetch:
            first,second=await asyncio.gather(store.get(POINT,False,None),store.get(POINT,False,None))
            third=await store.get(POINT,False,None)
        self.assertIs(first,second);self.assertIs(first,third)
        fetch.assert_called_once()

    async def test_local_plugin_does_not_accept_other_account_or_arbitrary_product(self):
        def marker(args):
            return parse_action('[BOT_ACTION: '+json.dumps({'tool':'weather_chart','args':args})+']')
        for args in ({'product':'shell'},{'product':'temperature_current','user_id':'999'}):
            self.assertIsNone(marker(args))
        ext=SimpleNamespace(weather_local_job=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext),marker({'product':'temperature_current'}),
                       {'message_type':'group','group_id':20,'user_id':10},None)
        ext.weather_local_job.assert_awaited_once_with(None,'group',20,10,'temperature_current','')
