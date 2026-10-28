import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from model_catalogs import eligible, fetch_catalog, discover, model_rank, record_verified, prefer_verified, account_blocked


class ModelCatalogTests(unittest.TestCase):
    def test_flagship_order_and_standard_mimo_pro_preference(self):
        names=['gpt-6.1-terra','gpt-6.1-sol','gpt-6-astra']
        self.assertEqual(sorted(names,key=lambda n:model_rank('openai',n),reverse=True),names[::-1])
        self.assertGreater(model_rank('mimo','mimo-v2.6-pro'),model_rank('mimo','mimo-v2.6-pro-ultraspeed'))

    def test_credit_exhaustion_is_distinct_from_temporary_rate_limit(self):
        self.assertTrue(account_blocked(429, 'credit_balance_exhausted'))
        self.assertFalse(account_blocked(429, 'rate_limit_exceeded'))
        self.assertTrue(account_blocked(401, 'invalid key'))

    def test_specialized_models_are_not_chat_candidates(self):
        for provider, name in [('mimo','mimo-v2.5-asr'), ('gemini','gemini-nano-banana-2.1'),
                               ('gemini','gemini-3.1-pro-preview-customtools'), ('openai','gpt-image-2'),
                               ('openai','gpt-5-search-api'), ('openai','gpt-5.3-codex')]:
            item = {'name': 'models/'+name} if provider == 'gemini' else {'id': name}
            self.assertIsNone(eligible(provider, item))
        self.assertEqual(eligible('gemini', {'name':'models/gemini-4-argon'}), 'gemini-4-argon')
        self.assertEqual(eligible('mimo', {'id':'mimo-v2.6-pro-ultraspeed'}), 'mimo-v2.6-pro-ultraspeed')

    def test_retired_official_openai_models_do_not_override_compatible_endpoints(self):
        self.assertIsNone(eligible('openai', {'id':'gpt-3.5-turbo'}))
        self.assertEqual(eligible('openai', {'id':'gpt-3.5-turbo'}, False), 'gpt-3.5-turbo')
        self.assertEqual(eligible('openai', {'id':'gpt-6.1-sol'}), 'gpt-6.1-sol')
        self.assertIsNone(eligible('claude', {'id':'claude-opus-4-8','lifecycle':'retired'}))

    def test_gemini_pagination_and_supported_methods(self):
        calls = []
        def reader(url):
            calls.append(url)
            return {'models':[{'name':'models/gemini-3.8-flash'}], 'nextPageToken':'page2'} if len(calls)==1 else \
                   {'models':[{'name':'models/gemini-4-argon'},
                              {'name':'models/gemini-9-flash','supportedGenerationMethods':['embedContent']}]}
        names = fetch_catalog('gemini','secret','https://example.org', reader=reader)
        self.assertIn('gemini-4-argon', names)
        self.assertNotIn('gemini-9-flash', names)
        self.assertIn('pageToken=page2', calls[1])

    def test_claude_pagination_has_more(self):
        responses = iter([{'data':[{'id':'claude-sonnet-5-5'}], 'has_more':True,'last_id':'last'},
                          {'data':[{'id':'claude-opus-5-5'}], 'has_more':False}])
        calls=[]
        def reader(url):
            calls.append(url)
            return next(responses)
        names = fetch_catalog('claude','secret','https://example.org',reader=reader)
        self.assertEqual(names[0], 'claude-opus-5-5')
        self.assertIn('after_id=last', calls[1])

    def test_failure_uses_cache_and_does_not_invent_generation_success(self):
        with TemporaryDirectory() as root:
            with patch('model_catalogs.fetch_catalog', return_value=['mimo-v2.6-pro']):
                self.assertEqual(discover('mimo','secret','https://example.org',[],cache_root=root), ['mimo-v2.6-pro'])
            with patch('model_catalogs.fetch_catalog',side_effect=TimeoutError()):
                self.assertEqual(discover('mimo','secret','https://example.org',['old'],cache_root=root), ['mimo-v2.6-pro'])
            record=json.loads((Path(root)/'mimo.json').read_text())
            self.assertFalse(record['generation_tested'])
            self.assertNotIn('secret', json.dumps(record))

    def test_verified_priority_keeps_other_models_and_is_key_specific(self):
        with TemporaryDirectory() as root:
            record_verified('gemini','secret','https://example.org',['gemini-3.6-flash'],root)
            names=['gemini-3.8-flash','gemini-3.6-flash']
            self.assertEqual(prefer_verified('gemini','secret','https://example.org',names,root),names[::-1])
            self.assertEqual(prefer_verified('gemini','new-key','https://example.org',names,root),names)
