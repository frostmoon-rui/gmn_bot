import json
import shutil
import subprocess
import unittest
from remote_access_policy import apply_policy, script_policy, PROCESS_NAMES, DOMAINS


class RemoteAccessPolicyTests(unittest.TestCase):
    def test_global_mode_becomes_direct_exceptions_with_same_global_choice(self):
        original = {'mode':'global','rules':['MATCH,subscription'],
                    'dns':{'fake-ip-filter':['+.existing.test']},'secret':'fixture',
                    'tun':{'enable':True},'proxies':[{'name':'fixture-node'}]}
        result = apply_policy(original)
        self.assertEqual(original['mode'], 'global')
        self.assertEqual(result['mode'], 'rule')
        self.assertEqual(result['rules'][-1], 'MATCH,GLOBAL')
        self.assertEqual(result['secret'], original['secret'])
        self.assertEqual(result['proxies'], original['proxies'])
        self.assertTrue(result['tun']['enable'])
        for process in PROCESS_NAMES:
            self.assertIn(f'PROCESS-NAME,{process},DIRECT',result['rules'])
        for domain in DOMAINS:
            self.assertIn(f'DOMAIN-SUFFIX,{domain},DIRECT',result['rules'])
            self.assertIn('+.'+domain,result['dns']['fake-ip-filter'])

    def test_reapplication_is_idempotent(self):
        config=apply_policy({'mode':'global'})
        self.assertEqual(apply_policy(config),config)

    def test_unreviewed_dns_whitelist_is_rejected_without_modifying_input(self):
        original={'dns':{'fake-ip-filter-mode':'whitelist'}}
        with self.assertRaises(ValueError):apply_policy(original)
        self.assertEqual(original,{'dns':{'fake-ip-filter-mode':'whitelist'}})

    @unittest.skipUnless(shutil.which('node'),'Node.js not installed')
    def test_gui_script_matches_headless_policy(self):
        fixture={'mode':'global','rules':['MATCH,old'],
                 'dns':{'fake-ip-filter':['+.existing.test']},'proxies':[{'name':'fixture'}]}
        code=script_policy()+'\nconsole.log(JSON.stringify(main('+json.dumps(fixture)+')));'
        result=subprocess.check_output(['node','-e',code],text=True,timeout=10)
        self.assertEqual(json.loads(result),apply_policy(fixture))
