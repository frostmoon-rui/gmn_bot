import unittest
from io import BytesIO
from unittest.mock import patch
from PIL import Image, ImageDraw
from weather_report import format_weather
from weather_delivery import render_report
from weather_cards import series_rows, chart_specs


class WeatherCardsTests(unittest.TestCase):
    def test_missing_samples_stay_missing_and_units_have_separate_charts(self):
        rows = series_rows([{'temperature_2m': 0, 'precipitation': 0, 'precipitation_probability': 50},
                            {'precipitation_probability': 0}])
        self.assertEqual(rows[0]['temp'], 0)
        self.assertIsNone(rows[1].get('temp'))
        self.assertEqual(rows[0]['probability'], 50)
        for chart in chart_specs(rows):
            self.assertFalse('probability' in chart and 'precipitation_mm' in chart)

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
        self.assertEqual(image.width, 1440)
        self.assertLess(image.height, 8000)
        self.assertLess(texts.index('Hourly forecast'), texts.index('Daily forecast'))
        self.assertNotIn('PRIVATE_PROVIDER', '\n'.join(texts))
        self.assertIn('0°C', texts)
