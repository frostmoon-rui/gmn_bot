import unittest

from model_proxy import select_model_proxy


class ModelProxyTests(unittest.TestCase):
    def test_explicit_port_overrides_stale_desktop_settings(self):
        self.assertEqual(select_model_proxy('http://127.0.0.1:7897',
                                          {'https': 'http://127.0.0.1:7890'}),
                         'http://127.0.0.1:7897')

    def test_boot_environment_proxy_is_preserved(self):
        self.assertEqual(select_model_proxy(None, {'https': 'http://127.0.0.1:7897'}),
                         'http://127.0.0.1:7897')

    def test_existing_desktop_and_legacy_defaults_are_preserved(self):
        self.assertEqual(select_model_proxy('', {'http': 'http://localhost:8888'}),
                         'http://localhost:8888')
        self.assertEqual(select_model_proxy(None, {}), 'http://127.0.0.1:7890')

    def test_invalid_setting_does_not_disclose_its_value(self):
        with self.assertRaisesRegex(ValueError, '^MODEL_PROXY must be a URL string$'):
            select_model_proxy({'secret': 'not-a-url'})


if __name__ == '__main__':
    unittest.main()
