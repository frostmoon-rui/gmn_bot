"""Regression coverage for the scale measured against native Amap labels."""
import unittest
from io import BytesIO

from PIL import Image

from food_atlas_image import map_panel, mercator, render_atlas


class AmapFixture:
    def request(self, route, params, **kwargs):
        self.zoom = params['zoom']
        self.density = params['scale']
        width, height = map(int, params['size'].split('*'))
        buffer = BytesIO()
        Image.new('RGB', (width*self.density, height*self.density), 'white').save(buffer, 'PNG')
        return buffer.getvalue()


class FoodAtlasScaleTests(unittest.TestCase):
    def test_hd_zoom_uses_measured_amap_scale(self):
        client = AmapFixture()
        points = [mercator(116.397, 39.908), mercator(121.397, 39.908), mercator(116.397, 36.7)]
        panel, pixels, fallback = map_panel(client, points)
        self.assertFalse(fallback)
        self.assertEqual(client.zoom, 6)
        self.assertEqual(client.density, 2)
        self.assertEqual(panel.size, (2048, 1364))
        self.assertAlmostEqual(pixels[1][0] - pixels[0][0], 910.2222, places=3)
        self.assertEqual(pixels[1][1], pixels[0][1])
        self.assertTrue(all(0 < x < panel.width and 0 < y < panel.height for x, y in pixels))

    def test_long_image_keeps_native_map_width(self):
        rows = [dict(city='Beijing', name='Fixture venue', location='116.397,39.908')]
        output = render_atlas(AmapFixture(), rows)
        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.width, 3840)
            self.assertGreater(image.height, 3000)
