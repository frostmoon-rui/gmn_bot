"""Help coverage without importing the live bot or issuing network requests."""
import unittest
from bot_features import Features, HELP


class WebsiteHelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_command_opens_the_website_topic(self):
        features = Features.__new__(Features)
        answer = await features.command('help website', 'group', 1, 2, True)
        self.assertEqual(answer, HELP['website'])

    async def test_general_help_exposes_the_website_commands(self):
        for command in ('website screenshot:home', 'recommended links', 'friend application', 'website'):
            self.assertIn(command, HELP['general'])

    async def test_parameters_defaults_and_boundaries_are_documented(self):
        topic = HELP['website']
        for value in ('https://frostmoonrui.com', 'zh', 'en', 'ru', 'ja', 'dark', 'light',
                      'home', 'about', 'friends', 'resources', 'food', 'projects', '30 seconds'):
            self.assertIn(value, topic)
        self.assertIn('Submit applications yourself', topic)
        self.assertIn('never enter the sticker library', topic)

    async def test_unaddressed_help_does_not_interrupt_the_group(self):
        features = Features.__new__(Features)
        self.assertIsNone(await features.command('help website', 'group', 1, 2, False))
