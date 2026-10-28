import ast
import base64
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from location_integrity import parse_binding, choose_geocode, city_query, VALIDATION_VERSION
from search_worker import Links, collect
from bot_extensions import Extensions


def city(chinese=True):
    return {'partial_match':True,'place_id':'place-test','types':['locality','political'],
        'geometry':{'location':{'lat':31.46,'lng':104.74}},
        'address_components':[{'types':['locality'],'long_name':'绵阳市' if chinese else 'Mianyang'},
            {'types':['administrative_area_level_1'],'long_name':'四川省' if chinese else 'Sichuan'},
            {'types':['country'],'long_name':'中国' if chinese else 'China','short_name':'CN'}]}


class LocationSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_binding_supports_id_before_or_after_place(self):
        expected={'target':'123456789','location':'Sichuan Mianyang'}
        self.assertEqual(parse_binding('set location:123456789 Sichuan Mianyang','set location'),expected)
        self.assertEqual(parse_binding('set location:Sichuan Mianyang 123456789','set location'),expected)
        self.assertEqual(city_query('四川省绵阳市'),'绵阳市')
        self.assertEqual(city_query('New York'),'New York')
        self.assertEqual(city_query('Antananarivo, MG'),'Antananarivo')

    def test_partial_city_requires_all_supplied_components(self):
        self.assertEqual(choose_geocode('四川省绵阳市',[city()])['place_id'],'place-test')
        for place in ('四川省银川市','四川省绵阳市不存在路123号'):
            with self.assertRaises(ValueError):
                choose_geocode(place,[city()])

    async def test_geocoder_retries_language_and_retains_verified_english_city(self):
        ext=Extensions.__new__(Extensions)
        ext.locations={}
        ext.key='dummy'
        ext.b=SimpleNamespace(regions_data={},REGIONS_FILE='unused.json',save_json_data_async=AsyncMock())
        ext.get_json=Mock(side_effect=[{'results':[city(False)]},{'results':[city(True)]}])
        data=await ext.normalize_location('四川省绵阳市')
        self.assertEqual(data['status'],'resolved')
        self.assertEqual(data['locality_en'],'Mianyang')
        self.assertEqual(data['validation_version'],VALIDATION_VERSION)
        self.assertEqual(ext.get_json.call_count,2)

    def test_model_region_tag_cannot_overwrite_binding(self):
        path=Path(__file__).resolve().parents[1]/'gemini_bot.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='clean_and_process_tags')
        save=Mock()
        scope={'re':re,'admins':SimpleNamespace(phrasing=lambda t,u:t),
               'regions_data':{'123456789':{'location':'Saved city'}},'save_json_data':save,
               'portraits_data':{},'feelings_data':{},'print':Mock()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
        result=scope['clean_and_process_tags']('Hello [REGION_UPDATE: 123456789 | Wrong city]',123456789,'Test')
        self.assertNotIn('REGION_UPDATE',result)
        self.assertEqual(scope['regions_data']['123456789']['location'],'Saved city')
        save.assert_not_called()

    def test_google_and_bing_redirects_decode_to_original_pages(self):
        target='https://example.gov.cn/notice.html'
        encoded='a1'+base64.urlsafe_b64encode(target.encode()).decode().rstrip('=')
        for href in ('https://www.google.com/url?q='+target,'/url?url='+target,
                     'https://www.bing.com/ck/a?u='+encoded):
            parser=Links()
            parser.feed('<a href="'+href+'">result</a>')
            self.assertEqual(parser.links,[target])

    def test_latest_search_skips_wikipedia_and_prioritizes_government_pages(self):
        url='https://example.gov.cn/notice.html'
        with patch('search_worker.wikipedia') as wiki, patch('search_worker.google',return_value=[url]), \
             patch('search_worker.alternative_search',return_value=[]),patch('search_worker.time.sleep'), \
             patch('research_transport.crawl_public_page',return_value='Current official heating notice'):
            result=collect('current local heating schedule','latest')
        wiki.assert_not_called()
        self.assertEqual(result['pages'][0]['url'],url)
        self.assertEqual(result['warnings'],[])
        self.assertGreater(result['retrieval_stats']['google_links'],0)

    def test_empty_first_fallback_search_tries_bing_without_navigation_links(self):
        from search_worker import alternative_search
        target='https://example.gov.cn/heating.html'
        encoded='a1'+base64.urlsafe_b64encode(target.encode()).decode().rstrip('=')
        html='<a href="https://www.bing.com/search?q=other">nav</a><a href="https://www.bing.com/ck/a?u='+encoded+'">notice</a>'
        with patch('search_worker.read',side_effect=['<html></html>',html]) as read:
            self.assertEqual(alternative_search('local heating schedule'),[target])
        self.assertEqual(read.call_count,2)
