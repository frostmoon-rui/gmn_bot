"""Regression coverage for complete passive replies and bubble boundaries."""
from types import SimpleNamespace
import unittest
from chat_style import prepare_passive_reply, generation_hit_limit


class ChatStyleTests(unittest.TestCase):
    def test_ordinary_reply_beyond_thirty_characters_is_preserved(self):
        text='Hmph, this cat is only collecting the good stickers![SPLIT]You keep sending them and I keep finding treasures.'
        self.assertGreater(len(text),30)
        self.assertEqual(prepare_passive_reply(text),text)

    def test_long_reply_ends_at_the_last_complete_sentence_within_the_soft_limit(self):
        first='This sentence is complete.'
        text=first+'[SPLIT]'+'unfinished '*40
        self.assertEqual(prepare_passive_reply(text,60),first)

    def test_first_sentence_may_finish_beyond_soft_limit(self):
        text='A complete sentence that needs a little more space.'
        self.assertEqual(prepare_passive_reply(text,30),text)

    def test_model_tags_and_reasoning_are_removed_without_running_updates(self):
        text='<think>Private reasoning.</think>Hi![REGION_UPDATE: 123456789 | Wrong city][SPLIT]A complete second bubble.'
        self.assertEqual(prepare_passive_reply(text),'Hi![SPLIT]A complete second bubble.')

    def test_extreme_output_has_an_explicit_ellipsis(self):
        result=prepare_passive_reply('x'*500,60)
        self.assertEqual(result,'x'*60+'…')

    def test_provider_limit_is_distinguished_from_local_formatting(self):
        self.assertTrue(generation_hit_limit(SimpleNamespace(candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')])))
        self.assertFalse(generation_hit_limit(SimpleNamespace(candidates=[SimpleNamespace(finish_reason='STOP')])))
