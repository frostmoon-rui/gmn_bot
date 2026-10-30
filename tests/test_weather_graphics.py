"""Offline rendering regressions for condition provenance and raster quality."""
from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image

from weather_graphics import condition_kind, condition_metadata, raster_scale, weather_icon
from weather_delivery import report_font, render_report
from weather_report import format_weather
from weather_providers import merge_weather


class WeatherGraphicsTests(unittest.TestCase):
    def test_supporting_labels_wrap_at_word_boundaries(self):
        from weather_dashboard import Painter
        painter=Painter(report_font,{},str)
        width=painter.draw.textlength('Precipitation probability',font=report_font(24))+4
        self.assertEqual(painter.wrap('Precipitation probability Not supplied',width,24),
                         ['Precipitation probability','Not supplied'])

    def test_condition_codes_and_legacy_adapter_do_not_infer_from_missing_rain(self):
        for row, expected in [({'weather_code':0},'clear'),({'weather_code':71},'snow'),
                              ({'weatherCondition':{'type':'HEAVY_RAIN'}},'rain'),
                              ({'desc':'多云'},'partly'),({'desc':'Overcast'},'cloud'),
                              ({'precipitation':0,'probability':0},'unknown'),
                              ({'weather_code':True},'unknown'),({'weather_code':float('nan')},'unknown')]:
            self.assertEqual(condition_kind(row),expected)
        self.assertEqual(condition_kind({'weather_code':0,'daytimeForecast':{
            'weatherCondition':{'type':'SNOW'}}},True),'snow')
        self.assertEqual(condition_metadata({'isDaytime':False}),{'is_day':0})
        self.assertEqual(condition_metadata({'is_day':'0','weather_type':'https://invalid.test'}),{})

    def test_hourly_symbols_preserve_condition_source_and_explicit_night(self):
        base={'local_date':'2026-10-30','timezone':'Asia/Shanghai'}
        first=dict(base,hours=[{'time':'2026-10-30T03:00','temp':0,'desc':'Overcast'}])
        second=dict(base,hours=[{'time':'2026-10-30T03:00','temp':3,'weather_code':0,'is_day':0}])
        row=merge_weather([('first',first),('second',second)])['hours'][0]
        self.assertEqual(condition_kind(row),'cloud')
        self.assertNotIn('weather_code',row)
        self.assertEqual(row['is_day'],0)
        row=merge_weather([('second',second)])['hours'][0]
        self.assertEqual(row['weather_code'],0)
        self.assertEqual(row['is_day'],0)

    def test_raster_budget_and_final_dimensions(self):
        for height in (900,5500,9000,30000):
            scale=raster_scale(1800,height)
            self.assertLessEqual(1800*height*scale*scale,54_000_000)
        self.assertEqual(raster_scale(1800,5500),2)
        data={'local_date':'2026-10-30','temp':0,'desc':'Clear','is_day':0}
        with patch('weather_graphics.raster_scale',return_value=1):
            direct=Image.open(BytesIO(render_report(format_weather(data))))
        smooth=Image.open(BytesIO(render_report(format_weather(data))))
        self.assertEqual(smooth.size,direct.size)
        self.assertEqual(smooth.width,1800)
        self.assertNotEqual(smooth.tobytes(),direct.tobytes())
        self.assertNotEqual(report_font(28,bold=True).getname(),report_font(28).getname())

    def test_symbols_are_local_vectors_and_day_night_differ(self):
        from weather_graphics import SmoothDraw
        images=[]
        for flag in (0,1):
            image=Image.new('RGB',(120,120),'white')
            weather_icon(SmoothDraw(image,1),24,24,70,{'weather_code':0,'is_day':flag})
            images.append(image.tobytes())
        self.assertNotEqual(*images)


if __name__=='__main__':
    unittest.main()
