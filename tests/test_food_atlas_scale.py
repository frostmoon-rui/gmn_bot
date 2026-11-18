"""Regression coverage for the scale measured against native Amap labels."""
import unittest
from io import BytesIO

from PIL import Image

from food_atlas_image import map_panel, mercator


class AmapFixture:
    def request(self, route, params, **kwargs):
        self.zoom = params['zoom']
        buffer = BytesIO()
        Image.new('RGB', (750, 500), 'white').save(buffer, 'PNG')
        return buffer.getvalue()


class FoodAtlasScaleTests(unittest.TestCase):
    def test_zoom_five_matches_native_label_separation(self):
        client = AmapFixture()
        points = [mercator(116.397, 39.908), mercator(121.397, 39.908), mercator(116.397, 36.7)]
        _, pixels, fallback = map_panel(client, points)
        self.assertFalse(fallback)
        self.assertEqual(client.zoom, 5)
        self.assertAlmostEqual(pixels[1][0] - pixels[0][0], 227.5556, places=3)
        self.assertEqual(pixels[1][1], pixels[0][1])
        self.assertTrue(all(0 < x < 750 and 0 < y < 500 for x, y in pixels))
