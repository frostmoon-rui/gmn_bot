"""Offline consent, aggregation, membership and rendering boundary tests."""
import copy
import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from community_maps import CommunityMaps, aggregate, boundaries, coarse_region, parse_command, render_map
from bot_actions import parse_action, dispatch


class GeographyTests(unittest.TestCase):
    def test_complete_boundaries_and_closed_rings(self):
        self.assertEqual(len(boundaries('china')), 34)
        self.assertGreater(len(boundaries('world')), 220)
        for scope in ('china','world'):
            for feature in boundaries(scope):
                for polygon in feature['geometry']['coordinates']:
                    for ring in polygon:
                        self.assertGreaterEqual(len(ring),4)
                        self.assertEqual(ring[0],ring[-1])

    def test_coarse_locations_never_include_city_or_coordinates(self):
        self.assertEqual(coarse_region('Sichuan'),{'country':'CN','province':'CN-SC'})
        self.assertEqual(coarse_region('Shaanxi')['province'],'CN-SN')
        self.assertEqual(coarse_region('France'),{'country':'FR','province':''})
        hint={'original':'Example City','country_code':'FR','formatted_address':'Example City, France'}
        self.assertEqual(coarse_region('Example City',hint)['country'],'FR')
        with self.assertRaises(ValueError):
            coarse_region('Different City',hint)
        with self.assertRaises(ValueError):
            coarse_region('Sichuan Beijing')
        with self.assertRaises(ValueError):
            coarse_region('Sichuan 1234567')

    def test_foreign_authoritative_country_does_not_match_chinese_province(self):
        hint={'original':'Sichuan Road','country_code':'US'}
        self.assertEqual(coarse_region('Sichuan Road',hint),{'country':'US','province':''})

    def test_single_participant_is_visible_and_nonmembers_are_excluded(self):
        rows={str(i):{'country':'CN','province':'CN-SC','private_city':'do not render'} for i in range(4)}
        rows['4']={'country':'FR','province':''}
        self.assertEqual(aggregate(rows,{'0'},'china'),{'CN-SC':1})
        self.assertEqual(aggregate(rows,{'0','1'},'china'),{'CN-SC':2})
        self.assertEqual(aggregate(rows,{'0','1','2','4'},'china'),{'CN-SC':3})
        self.assertEqual(aggregate(rows,{'0','1','2','4'},'world'),{'CN':3,'FR':1})
        self.assertEqual(aggregate(rows,set(),'china'),{})

    def test_renderer_only_accepts_sanitized_publishable_counts(self):
        for invalid in ({'CN-SC':0},{'CN-SC':-1},{'CN-SC':True},{'a user':3},{'CN-SC':'3'}):
            with self.assertRaises(ValueError):
                render_map(invalid,'china')
        for scope,counts in [('china',{'CN-SC':1}),('world',{'FR':1}),('china',{})]:
            image=Image.open(BytesIO(render_map(counts,scope)))
            self.assertEqual(image.format,'PNG')
            self.assertEqual(image.width,1800)

    def test_command_is_exact_not_conversational_interception(self):
        self.assertIsNone(parse_command('we discussed a heatmap yesterday'))
        self.assertEqual(parse_command('heatmap world'),('world',''))
        self.assertEqual(parse_command('heatmap join France'),('join','France'))
        self.assertIsNone(parse_command('heatmap world other-group'))


class ConsentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved=[]
        async def save(name,data):
            self.saved.append((name,copy.deepcopy(data)))
        self.bot=SimpleNamespace(load_json_data=lambda name,default:copy.deepcopy(default),
            save_json_data_async=save,regions_data={'10':{'location':'Sichuan','nickname':'private'}})
        self.maps=CommunityMaps(self.bot)
        self.ws=SimpleNamespace(send=AsyncMock(return_value=[{'user_id':i} for i in (10,11,12,13)]))
        self.send=AsyncMock()

    async def call(self,text,group=20,user=10,kind='group',addressed=True):
        return await self.maps.command(text,self.ws,kind,group,user,addressed,self.send)

    async def test_no_automatic_weather_enrollment_and_explicit_withdrawal(self):
        self.assertEqual(self.maps.records,{})
        await self.call('heatmap join')
        self.assertEqual(set(self.maps.records['20']['10']),{'country','province','consented_at'})
        self.assertNotIn('private',json.dumps(self.saved))
        await self.call('heatmap leave')
        self.assertNotIn('10',self.maps.records['20'])
        self.assertEqual(self.maps.versions['20'],2)

    async def test_self_and_group_isolation(self):
        await self.call('heatmap join France',group=21,user=11)
        await self.call('heatmap leave')
        self.assertIn('11',self.maps.records['21'])
        await self.call('heatmap join 1234567 Sichuan')
        self.assertNotIn('10',self.maps.records.get('20',{}))
        self.assertIsNone(await self.call('heatmap join France',addressed=False))
        self.assertNotIn('10',self.maps.records.get('20',{}))
        await self.call('heatmap join France',kind='private')
        self.assertNotIn('10',self.maps.records.get('20',{}))

    async def test_fail_closed_without_membership_response(self):
        self.ws.send.return_value=None
        with patch('community_maps.render_map') as renderer:
            answer=await self.call('heatmap world')
            renderer.assert_not_called()
        self.assertEqual(answer,self.maps.labels['failed'])
        self.assertEqual(len(self.ws.send.await_args_list),1)

    async def test_renderer_cannot_see_other_groups_members_or_private_fields(self):
        for user in (10,11,12):
            await self.call('heatmap join Sichuan',user=user)
        for user in (30,31,32):
            await self.call('heatmap join France',user=user,group=21)
        with patch('community_maps.render_map',return_value=b'png') as renderer:
            self.assertEqual(await self.call('heatmap china'),'')
        self.assertEqual(renderer.call_args.args[0],{'CN-SC':3})
        request=json.loads(self.ws.send.await_args_list[0].args[0])
        self.assertEqual(request,{'action':'get_group_member_list','params':{'group_id':20,'no_cache':True}})
        output=json.loads(self.ws.send.await_args_list[-1].args[0])
        self.assertEqual(output['params']['group_id'],20)
        self.assertEqual(output['params']['message'][0]['type'],'image')

    async def test_optout_during_render_prevents_stale_publication(self):
        async def changed(*args):
            self.maps.versions['20']+=1
            return b'png'
        with patch('community_maps.asyncio.to_thread',side_effect=changed):
            answer=await self.call('heatmap china')
        self.assertEqual(answer,self.maps.labels['changed'])
        self.assertEqual(len(self.ws.send.await_args_list),1)

    async def test_failed_save_does_not_acknowledge_or_mutate_consent(self):
        self.bot.save_json_data_async=AsyncMock(side_effect=OSError('offline'))
        with self.assertRaises(OSError):
            await self.call('heatmap join France')
        self.assertEqual(self.maps.records,{})

    async def test_model_action_can_only_draw_current_group_not_enroll_users(self):
        action=parse_action('[BOT_ACTION: {"tool":"community_map","args":{"scope":"world"}}]')
        self.assertEqual(action,{'tool':'community_map','args':{'scope':'world'}})
        for args in ({'scope':'join'},{'scope':'world','group_id':'99'},{'scope':'world','user_id':'99'}):
            self.assertIsNone(parse_action('[BOT_ACTION: '+json.dumps({'tool':'community_map','args':args})+']'))
        ext=SimpleNamespace(community=SimpleNamespace(command=AsyncMock(return_value='done')),send=AsyncMock())
        await dispatch(SimpleNamespace(extensions=ext),action,{'message_type':'group','group_id':20,'user_id':10},self.ws)
        ext.community.command.assert_awaited_once_with('heatmap world',self.ws,'group',20,10,True,ext.send)
