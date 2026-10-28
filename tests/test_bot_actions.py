import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from bot_actions import parse_action, dispatch, english_weather_request, capital_name
from bot_extensions import Extensions


def marker(tool,args):
    return '[BOT_ACTION: '+json.dumps({'tool':tool,'args':args})+']'


class BotActionTests(unittest.IsolatedAsyncioTestCase):
    def test_madagascar_capital_is_resolved_from_offline_data(self):
        self.assertEqual(capital_name('MG'),'Antananarivo')
        self.assertIsNone(capital_name('../MG'))
    async def test_country_query_uses_verified_capital_and_marks_reference(self):
        ext=Extensions.__new__(Extensions)
        ext.weather_cache={}
        ext.locations={'Madagascar':{'country_code':'MG'}}
        ext.key='dummy'
        ext.unsupported_weather=set()
        ext.normalize_location=AsyncMock(side_effect=[
            {'status':'needs_city','country_code':'MG','location_types':['country','political']},
            {'status':'resolved','country_code':'MG','latitude':-18.9,'longitude':47.5}])
        ext.google_weather_source=AsyncMock(return_value={'local_date':'2026-10-28','temp':20})
        async def collect(location, providers):
            return await next(call for name,call in providers if name=='Google')()
        ext.weather_aggregator=SimpleNamespace(collect=collect)
        ext.weather_history=SimpleNamespace(enrich=lambda place,data:data)
        with patch('bot_actions.capital_name',return_value='Antananarivo'):
            data=await ext._weather_for_location('Madagascar')
        self.assertTrue(data['capital_reference'])
        self.assertEqual(ext.normalize_location.call_args.args[0],'Antananarivo, MG')
        self.assertEqual(ext.google_weather_source.call_args.args[0]['country_code'],'MG')

    def test_strict_schema_and_no_cross_user_or_arbitrary_commands(self):
        for tool,args in [('shell',{'command':'whoami'}),('weather',{'location':'Madagascar','user_id':999}),
                          ('research',{'query':'test','model':'paid-model'}),('map',{'operation':'delete map','query':'x'}),
                          ('weather',{'target':'yesterday'}),('website',{'page':'studio'}),('image',{})]:
            self.assertIsNone(parse_action(marker(tool,args)))
        self.assertIsNone(parse_action('quoted '+marker('weather',{'location':'Madagascar'})))
        self.assertEqual(parse_action(marker('weather',{'location':'Madagascar'}))['tool'],'weather')

    def test_weather_word_order_and_temporary_location(self):
        for text in ('weather forecast Madagascar','Madagascar weather forecast','weather in Madagascar'):
            self.assertEqual(english_weather_request(text)['location'],'Madagascar')

    async def test_weather_action_is_bound_to_current_conversation(self):
        ext=SimpleNamespace(weather_at=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext),parse_action(marker('weather',{'location':'Madagascar'})),
            {'message_type':'group','group_id':20,'user_id':10},None)
        ext.weather_at.assert_awaited_once_with(None,'group',20,10,'Madagascar','today')

    async def test_research_reuses_native_whitelist_checks(self):
        ext=SimpleNamespace(command=AsyncMock(return_value='permission denied'),send=AsyncMock())
        bot=SimpleNamespace(extensions=ext,features=SimpleNamespace(command=AsyncMock(return_value=None)))
        await dispatch(bot,parse_action(marker('research',{'query':'research question'})),
            {'message_type':'group','group_id':20,'user_id':10},None,whitelist=False)
        self.assertEqual(ext.command.call_args.args[6],False)
        self.assertIn('research question',ext.command.call_args.args[0])

    async def test_explicit_weather_does_not_replace_saved_home_binding(self):
        ext=Extensions.__new__(Extensions)
        ext.b=SimpleNamespace(regions_data={'10':{'location':'Saved home'}})
        ext.important={}
        ext.google_weather=AsyncMock(return_value={'local_date':'2026-10-28'})
        ext.weather_report=lambda *args,**kwargs:'weather card'
        ext.send_weather=AsyncMock()
        ext.send=AsyncMock()
        await ext.weather_at(None,'group',20,10,'Madagascar')
        ext.google_weather.assert_awaited_once_with('Madagascar')
        self.assertEqual(ext.b.regions_data['10']['location'],'Saved home')
