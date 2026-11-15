"""Host-free tests. Never contacts systemd, Docker, network, or the Jetson."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'tools' / 'orin_reconcile.py'
spec = importlib.util.spec_from_file_location('orin_reconcile', SCRIPT)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

class FakeSystem:
    def __init__(self, name='sentient-web-chat.service'):
        self.name = name
        self.active = 'active'
        self.enabled = 'enabled'
        self.fail_after_disable = False
        self.commands = []
    def show(self, unit):
        assert unit == self.name
        return {'LoadState':'loaded','ActiveState':self.active,
                'UnitFileState':self.enabled,
                'FragmentPath':f'/etc/systemd/system/{unit}', 'Names':unit}
    def require(self, args, timeout=15):
        self.commands.append(args)
        if args[:2] == ['systemctl', 'disable']:
            self.enabled='disabled'
            if '--now' in args: self.active='inactive'
        elif args[:2] == ['systemctl', 'enable']:
            self.enabled='enabled'
        elif args[:2] == ['systemctl', 'stop']:
            self.active='inactive'
        elif args[:2] == ['systemctl', 'start']:
            self.active='active'
        return ''

class TestSafety(unittest.TestCase):
    def test_private_url_validation(self):
        for s in ['http://127.0.0.1:3000','http://192.168.1.168:5173/',
                  'https://airi.local:8443/','http://localhost:1234']:
            self.assertTrue(rc.validate_private_url(s).endswith('/'))
        for s in ['https://airi.moeru.ai:443/','http://1.1.1.1:5173/',
                  'http://user:pass@127.0.0.1:3333',
                  'http://127.0.0.1:3000/admin','http://127.0.0.1/',
                  'file:///etc/passwd','http://example.com:8000',
                  'http://127.0.0.1:3000/?secret=yes']:
            with self.subTest(s=s), self.assertRaises(rc.SafetyError):
                rc.validate_private_url(s)

    def test_unsupported_unit_and_unsafe_state_blocked(self):
        state=FakeSystem().show('sentient-web-chat.service')
        for unit in ('sshd.service','airi.service','ollama.service','sentient-conversation.service'):
            with self.subTest(unit=unit), self.assertRaises(rc.SafetyError):
                rc.check_unit_policy(unit,state)
        state['UnitFileState']='masked'
        with self.assertRaises(rc.SafetyError):
            rc.check_unit_policy('sentient-web-chat.service',state)

    def test_read_only_inventory_filters_without_secrets(self):
        class FakeRunner:
            def run(self,args,timeout=15):
                if args[1]=='list-unit-files':
                    return subprocess.CompletedProcess(args,0,
                        'sentient-web-chat.service enabled\nollama.service enabled\nssh.service enabled\n')
                return subprocess.CompletedProcess(args,0,'')
            def require(self,args,timeout=15):
                return ('LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'
                        f'FragmentPath=/etc/systemd/system/{args[2]}\nNames={args[2]}\n')
        with patch.object(rc,'RUN',FakeRunner()):
            result=rc.units_inventory()
        self.assertEqual([x['name'] for x in result],['ollama.service','sentient-web-chat.service'])
        self.assertFalse(result[0]['candidate_legacy_ui'])
        self.assertTrue(result[1]['candidate_legacy_ui'])

class TestTransaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir=Path(self.tmp.name)
        self.fake = FakeSystem()
        self.probe_ok={'http_ok':True,'status':200}
        patches=[patch.object(rc,'root_required'),patch.object(rc,'jetson_required'),
                 patch.object(rc,'machine_tag',return_value='test-jetson-01'),
                 patch.object(rc,'show_unit',side_effect=self.fake.show),
                 patch.object(rc.RUN,'require',side_effect=self.fake.require),
                 patch.object(rc,'probe_airi',return_value=self.probe_ok)]
        for p in patches: p.start();self.addCleanup(p.stop)

    def prepare(self):
        path=rc.prepare(self.fake.name,'http://127.0.0.1:3000/',self.state_dir)
        return path,rc.load_plan(path)

    def test_prepare_no_mutations(self):
        path,plan=self.prepare()
        self.assertEqual(self.fake.commands,[])
        self.assertEqual(plan['phase'],'prepared')
        self.assertTrue(plan['plan_id'])
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.fake.active,'active')

    def test_apply_then_rollback_preserves_original_state(self):
        path,plan=self.prepare()
        with self.assertRaisesRegex(rc.SafetyError,'Require exact'):
            rc.apply_plan(path,'bad',True)
        with self.assertRaisesRegex(rc.SafetyError,'Require exact'):
            rc.apply_plan(path,plan['plan_id'],False)
        self.assertEqual(self.fake.commands,[])
        outcome=rc.apply_plan(path,plan['plan_id'],True)
        self.assertEqual(outcome['phase'],'pending_acceptance')
        with self.assertRaises(rc.SafetyError):
            rc.accept_plan(path,plan['plan_id'],False)
        accepted=rc.accept_plan(path,plan['plan_id'],True)
        self.assertEqual(accepted['phase'],'accepted')
        self.assertEqual((self.fake.active,self.fake.enabled),('inactive','disabled'))
        restored=rc.rollback(path,plan['plan_id'])
        self.assertEqual(restored['phase'],'rolled_back')
        self.assertEqual((self.fake.active,self.fake.enabled),('active','enabled'))
        with self.assertRaisesRegex(rc.SafetyError,'Cannot rollback'):
            rc.rollback(path,plan['plan_id'])

    def test_originally_disabled_but_active_restores_precisely(self):
        self.fake.enabled='disabled'
        path,plan=self.prepare()
        rc.apply_plan(path,plan['plan_id'],True)
        rc.rollback(path,plan['plan_id'])
        self.assertEqual((self.fake.active,self.fake.enabled),('active','disabled'))

    def test_refuses_drift_before_changes(self):
        path,plan=self.prepare()
        self.fake.active='inactive'
        with self.assertRaisesRegex(rc.SafetyError,'state drift'):
            rc.apply_plan(path,plan['plan_id'],True)
        self.assertEqual(self.fake.commands,[])

    def test_auto_rollback_when_http_breaks(self):
        path,plan=self.prepare()
        hits=iter([self.probe_ok,{'http_ok':False,'error':'down'}])
        with patch.object(rc,'probe_airi',side_effect=lambda url:next(hits)):
            with self.assertRaisesRegex(rc.SafetyError,'automatic rollback succeeded'):
                rc.apply_plan(path,plan['plan_id'],True)
        self.assertEqual((self.fake.active,self.fake.enabled),('active','enabled'))
        self.assertEqual(rc.load_plan(path)['phase'],'rolled_back')

    def test_tampered_plan_rejected(self):
        path,plan=self.prepare()
        plan['unit']='ollama.service'
        rc.write_json(path,plan,allow_existing=True)
        with self.assertRaisesRegex(rc.SafetyError,'non-allowlisted'):
            rc.load_plan(path)

    def test_machine_identity_mismatch(self):
        path,plan=self.prepare()
        with patch.object(rc,'machine_tag',return_value='other-machine'):
            with self.assertRaisesRegex(rc.SafetyError,'different machine'):
                rc.load_plan(path)

    def test_missing_airi_blocks_prepare(self):
        with patch.object(rc,'probe_airi',return_value={'http_ok':False}):
            with self.assertRaisesRegex(rc.SafetyError,'unavailable'):
                rc.prepare(self.fake.name,'http://127.0.0.1:3000/',self.state_dir)
        self.assertEqual(list(self.state_dir.iterdir()),[])

if __name__ == '__main__':
    unittest.main()
