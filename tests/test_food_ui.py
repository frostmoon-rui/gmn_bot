"""Offline checks for the English food-map interface."""
import shutil
import subprocess
import unittest
from pathlib import Path


class FoodUITests(unittest.TestCase):
    def test_english_interface_and_external_feed_compatibility(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is optional; needed only for frontend checks')
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run([node, str(root / 'tools/check_food_ui.js')],
                                   cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('PASS', completed.stdout)
