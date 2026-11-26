import json
import unittest

from quoted_weather_location import quoted_map_place


class QuotedWeatherLocationTests(unittest.TestCase):
    def test_native_and_cq_segments(self):
        msg = {'message': [{'type': 'location', 'data': {
            'lat': '30.68', 'lon': '104.10', 'content': 'Example City, Example Country'}}]}
        self.assertEqual(quoted_map_place(msg), 'Example City, Example Country')
        self.assertEqual(quoted_map_place({'raw_message':
            '[CQ:location,lat=30.68,lon=104.10,title=Example&#44; Park]'}), 'Example, Park')

    def test_map_card_and_location_preview_are_regional(self):
        synthetic_address = '浙江省宁波市海曙区测试路'
        expected_area = '浙江省宁波市海曙区'
        payload = {'app': 'com.tencent.map', 'meta': {'Location.Search': {'address': synthetic_address}}}
        msg = {'message': [{'type': 'json', 'data': {'data': json.dumps(payload)}}]}
        self.assertEqual(quoted_map_place(msg), expected_area)
        payload['app'] = 'com.tencent.music'
        msg['message'][0]['data']['data'] = json.dumps(payload)
        self.assertIsNone(quoted_map_place(msg))
        self.assertEqual(quoted_map_place({'message': '[位置]' + synthetic_address}), expected_area)

    def test_unreadable_location_and_literal_cq_text(self):
        self.assertEqual(quoted_map_place({'message': [{'type': 'location', 'data': {}}]}), '')
        self.assertIsNone(quoted_map_place({'message': [{'type': 'text', 'data': {
            'text': '[CQ:location,title=Example Place]'}}]}))
