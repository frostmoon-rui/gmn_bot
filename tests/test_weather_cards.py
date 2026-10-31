import unittest
from io import BytesIO
from unittest.mock import patch
from PIL import Image, ImageDraw
from weather_report import format_weather
from weather_delivery import render_report
from weather_cards import series_rows, chart_specs
from weather_dashboard import axis_groups, connected_runs, daily_range, elapsed_seconds, temperature_sample


class WeatherCardsTests(unittest.TestCase):
    def test_missing_samples_stay_missing_and_combined_units_have_distinct_axes(self):
        rows = series_rows([{'temperature_2m': 0, 'precipitation': 0, 'precipitation_probability': 50},
                            {'precipitation_probability': 0}])
        self.assertEqual(rows[0]['temp'], 0)
        self.assertIsNone(rows[1].get('temp'))
        self.assertEqual(rows[0]['probability'], 50)
        self.assertEqual(series_rows([{'probability':0}])[0]['probability'],0)
        for chart in chart_specs(rows):
            groups=dict(axis_groups(chart))
            if 'probability' in chart and 'precipitation_mm' in chart:
                self.assertIn('probability',groups['%'])
                self.assertIn('precipitation_mm',groups['mm'])
                self.assertNotIn('probability',groups['mm'])

    def test_structured_report_renders_hourly_before_daily_without_sources(self):
        data = {'local_date': '2026-10-28', 'temp': 0, 'feels_like': -4,
                'wind_kmph': 20, 'wind_gust_kmph': 40, 'precipitation_mm': 0,
                'field_sources': {'temp': 'PRIVATE_PROVIDER'},
                'hours': [{'time': '2026-10-28T09:00', 'temperature_2m': 0},
                          {'time': '2026-10-28T10:00', 'temperature_2m': 2}],
                'days': [{'date': '2026-10-28', 'maxTemperature': {'degrees': 4},
                          'minTemperature': {'degrees': -2}, 'precipitation_sum': 0}]}
        texts = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', record):
            image = Image.open(BytesIO(render_report(format_weather(data))))
        self.assertEqual(image.width, 1800)
        self.assertLess(image.height, 8000)
        self.assertLess(texts.index('Hourly forecast'), texts.index('Daily forecast'))
        self.assertNotIn('PRIVATE_PROVIDER', '\n'.join(texts))
        self.assertIn('0°C', texts)

    def test_all_numeric_fields_survive_compact_grouping(self):
        from weather_report import METRICS
        fields=set(METRICS)-{'wind_direction','wind_force'}
        rows=[dict.fromkeys(fields,1)|{'probability':40}]
        specs=chart_specs(rows)
        self.assertEqual({key for panel in specs for key in panel},fields|{'probability'})
        self.assertLessEqual(len(specs),8)
        self.assertTrue(all(len(axis_groups(panel))<=2 for panel in specs))

    def test_real_gaps_and_missing_values_break_lines_but_zero_is_a_point(self):
        from datetime import datetime, timedelta
        start=datetime(2026,10,30)
        times=[start+timedelta(hours=hour) for hour in (0,1,2,3,6,7)]
        rows=[{'temp':v} for v in (0,1,None,2,3,4)]
        self.assertEqual(connected_runs(rows,'temp',times),[[0,1],[3],[4,5]])
        times[5]=None
        self.assertEqual(connected_runs(rows,'temp',times),[[0,1],[3],[4],[5]])

    def test_daily_ranges_keep_negative_and_single_sided_values(self):
        self.assertEqual(daily_range({'min_temp':-5,'max_temp':0}),(-5,0))
        self.assertEqual(daily_range({'max_temp':2}),(None,2))
        self.assertEqual(daily_range({'min_temp':0}),(0,None))
        self.assertEqual(daily_range({'min_temp':8,'max_temp':2}),(None,None))

    def test_repeated_dst_hour_keeps_two_real_positions(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone=ZoneInfo('America/New_York')
        a=datetime.fromisoformat('2026-11-01T01:00-04:00').astimezone(zone)
        b=datetime.fromisoformat('2026-11-01T01:00-05:00').astimezone(zone)
        self.assertEqual(elapsed_seconds(a,b),3600)
        self.assertEqual(connected_runs([{'temp':1},{'temp':2}],'temp',[a,b]),[[0,1]])

    def test_hourly_pairs_keep_their_metric_names_and_single_values_have_no_range(self):
        marks,span=temperature_sample({'temp':5,'feels_like':8},('temp','feels_like'))
        self.assertEqual(marks,[('temp',5),('feels_like',8)])
        self.assertEqual(span,(5,8))
        self.assertEqual(temperature_sample({'temp':0},('temp','feels_like')),([('temp',0)],None))
        marks,span=temperature_sample({'min_temp':-5,'max_temp':0,'feels_like':3},
                                      ('min_temp','max_temp','feels_like'))
        self.assertEqual(span,(-5,0))
        self.assertIn(('feels_like',3),marks)

    def test_hourly_and_daily_temperature_stems_are_vertical_and_dates_share_a_baseline(self):
        data={'local_date':'2026-10-28','temp':0,
              'hours':[{'time':'2026-10-28T09:00','temp':0,'feels_like':-4},
                       {'time':'2026-10-28T10:00','temp':2,'feels_like':-2}],
              'days':[{'date':'2026-10-28','minTemperature':{'degrees':-3},'maxTemperature':{'degrees':8}},
                      {'date':'2026-10-29','minTemperature':{'degrees':0},'maxTemperature':{'degrees':10}}]}
        stems=[];dates={}
        original_box=ImageDraw.ImageDraw.rounded_rectangle
        original_text=ImageDraw.ImageDraw.text
        def box(draw,xy,*args,**kwargs):
            if kwargs.get('fill') in {'#e4c1a8','#ddd4e7'}:stems.append((xy,kwargs['fill']))
            return original_box(draw,xy,*args,**kwargs)
        def text(draw,xy,value,*args,**kwargs):
            if str(value) in {'10-28','10-29'}:dates[str(value)]=xy
            return original_text(draw,xy,value,*args,**kwargs)
        with patch('weather_graphics.raster_scale',return_value=1),patch.object(ImageDraw.ImageDraw,'rounded_rectangle',box),patch.object(ImageDraw.ImageDraw,'text',text):
            render_report(format_weather(data))
        self.assertEqual(len(stems),4)
        for xy,_ in stems:
            self.assertLess(xy[2]-xy[0],50)
            self.assertGreater(xy[3]-xy[1],xy[2]-xy[0])
        self.assertEqual(dates['10-28'][1],dates['10-29'][1])
        self.assertLess(dates['10-28'][0],dates['10-29'][0])


class WeatherDensityTests(unittest.TestCase):
    def test_rain_and_wind_share_a_row_without_merging_axes_or_losing_data(self):
        from copy import deepcopy
        from weather_dashboard import Painter
        data={'local_date':'2026-10-30','hours':[
            {'time':'2026-10-30T09:00','temp':0,'precipitation_mm':0,'probability':0,
             'wind_kmph':0,'wind_gust_kmph':10,'wind_degrees':359},
            {'time':'2026-10-30T10:00','temp':3,'precipitation_mm':2,'probability':50,
             'wind_kmph':5,'wind_gust_kmph':15,'wind_degrees':1}]}
        before=deepcopy(data)
        panels=[]
        original=Painter.panel
        def capture(self,box,keys,*args,**kwargs):
            panels.append((box,keys))
            return original(self,box,keys,*args,**kwargs)
        with patch.object(Painter,'panel',capture):
            render_report(format_weather(data))
        temperature=next(box for box,keys in panels if 'temp' in keys)
        rain,rain_keys=next((box,keys) for box,keys in panels if 'precipitation_mm' in keys)
        wind,wind_keys=next((box,keys) for box,keys in panels if 'wind_kmph' in keys)
        self.assertGreater(temperature[2]-temperature[0],1200)
        self.assertEqual(rain[1],wind[1])
        self.assertLess(rain[2],wind[0])
        self.assertEqual({unit for unit,_ in axis_groups(rain_keys)},{'mm','%'})
        self.assertEqual({unit for unit,_ in axis_groups(wind_keys)},{'km/h'})
        self.assertEqual(data,before)

    def test_large_reading_shrinks_to_fit_instead_of_losing_its_digits(self):
        sizes=[]
        original=ImageDraw.ImageDraw.text
        def capture(draw,xy,text,*args,**kwargs):
            if str(text)=='499.9km/h':
                sizes.append(kwargs['font'].size)
            return original(draw,xy,text,*args,**kwargs)
        with patch('weather_graphics.raster_scale',return_value=1),patch.object(ImageDraw.ImageDraw,'text',capture):
            render_report(format_weather({'local_date':'2026-10-30','wind_kmph':499.9}))
        self.assertTrue(sizes)
        self.assertGreaterEqual(sizes[0],40)
        self.assertLess(sizes[0],82)
