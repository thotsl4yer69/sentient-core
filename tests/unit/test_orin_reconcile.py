"""Host-free tests. Never contacts systemd, Docker, network, or the Jetson."""
import hashlib
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
        self.source = '[Unit]\nDescription=Legacy UI\n[Install]\nWantedBy=multi-user.target\n'
    def show(self, unit):
        assert unit == self.name
        return {'LoadState':'loaded','ActiveState':self.active,
                'UnitFileState':self.enabled,
                'FragmentPath':f'/etc/systemd/system/{unit}', 'Names':unit,
                'PropagatesStopTo':'', 'BoundBy':'', 'ConsistsOf':'', 'TriggeredBy':''}
    def require(self, args, timeout=15):
        if args[:2]==['systemctl','cat']:
            return self.source
        self.commands.append(args)
        if args[:2] == ['systemctl', 'disable']:
            self.enabled='disabled'
            if '--now' in args:
                self.active='inactive'
                if self.fail_after_disable:
                    raise rc.SafetyError('Injected failure after systemd state change')
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

    def test_propagated_stops_are_forbidden(self):
        for relation in ('PropagatesStopTo', 'BoundBy', 'ConsistsOf'):
            state=FakeSystem().show('sentient-web-chat.service')
            state[relation]='other-app.service'
            with self.subTest(relation=relation), self.assertRaisesRegex(rc.SafetyError,'Stop propagation'):
                rc.check_unit_policy('sentient-web-chat.service',state)

    def test_activation_trigger_is_forbidden(self):
        state=FakeSystem().show('sentient-web-chat.service')
        state['TriggeredBy']='sentient-web-chat.socket'
        with self.assertRaisesRegex(rc.SafetyError,'activation detected'):
            rc.check_unit_policy('sentient-web-chat.service',state)

    def test_frontend_candidate_inventory_marks_names_not_permissions(self):
        units=[{'name':'sentient-web-chat.service','ActiveState':'active'},
               {'name':'ollama.service','ActiveState':'active'},
               {'name':'airi-stage.service','ActiveState':'active'}]
        containers={'stdout':(
            'open-webui|ghcr.io/open-webui/open-webui:stable|Up 4h|127.0.0.1:3100->8080/tcp\n'
            'redis|redis:7|Up 5h|6379/tcp\n')}
        found=rc.frontend_candidates(units,containers)
        self.assertEqual({x['name'] for x in found},
                         {'sentient-web-chat.service','airi-stage.service','open-webui'})
        self.assertTrue(all(x['removal_approved'] is False for x in found))
        self.assertEqual(next(x for x in found if x['name']=='airi-stage.service')['role_hint'],'AIRI')

    def test_voice_runtime_paths_include_observed_service_workdir(self):
        paths=rc.voice_runtime_paths([{'WorkingDirectory':'/opt/sentient-core'}])
        self.assertIn(Path('/opt/sentient-core/sentient/services/voice/wake_word.py'),paths)
        self.assertIn(Path('/mnt/orin-ai/orin/voice/handsfree.py'),paths)
        self.assertNotIn(Path('relative/voice/handsfree.py'),
                         rc.voice_runtime_paths([{'WorkingDirectory':'relative'}]))

    def test_runtime_fingerprints_do_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            regular=root/'voice.py'; regular.write_bytes(b'voice runtime\n')
            link=root/'link.py'; link.symlink_to(regular)
            missing=root/'missing.py'
            result={x['path']:x for x in rc.fingerprint_files([regular,link,missing])}
        self.assertEqual(result[str(regular)]['sha256'],
                         hashlib.sha256(b'voice runtime\n').hexdigest())
        self.assertTrue(result[str(link)]['symlink'])
        self.assertNotIn('sha256',result[str(link)])
        self.assertFalse(result[str(missing)]['exists'])

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


    def test_originally_disabled_inactive_is_not_started_on_rollback(self):
        self.fake.enabled='disabled'
        self.fake.active='inactive'
        path,plan=self.prepare()
        rc.apply_plan(path,plan['plan_id'],True)
        rc.rollback(path,plan['plan_id'])
        self.assertEqual((self.fake.active,self.fake.enabled),('inactive','disabled'))
        self.assertFalse(any(x[:2]==['systemctl','start'] for x in self.fake.commands))

    def test_rollback_from_interrupted_apply(self):
        path,plan=self.prepare()
        # Simulates power loss after systemctl disabled the unit but before
        # the post-change HTTP check or acceptance step could run.
        rc.record(path,plan,'applying','Simulated power-loss state')
        self.fake.active='inactive'; self.fake.enabled='disabled'
        result=rc.rollback(path,plan['plan_id'])
        self.assertEqual(result['phase'],'rolled_back')
        self.assertEqual((self.fake.active,self.fake.enabled),('active','enabled'))

    def test_auto_rollback_when_disable_partly_fails(self):
        path,plan=self.prepare()
        self.fake.fail_after_disable=True
        with self.assertRaisesRegex(rc.SafetyError,'automatic rollback succeeded'):
            rc.apply_plan(path,plan['plan_id'],True)
        self.assertEqual((self.fake.active,self.fake.enabled),('active','enabled'))
        self.assertEqual(rc.load_plan(path)['phase'],'rolled_back')

    def test_symlink_plan_rejected(self):
        path,plan=self.prepare()
        link=self.state_dir/'untrusted-symlink.json'
        link.symlink_to(path)
        with self.assertRaisesRegex(rc.SafetyError,'symlink'):
            rc.load_plan(link)

    def test_access_denied_blocks_before_mutations(self):
        with patch.object(rc,'root_required',side_effect=rc.SafetyError('Requires root')):
            with self.assertRaisesRegex(rc.SafetyError,'Requires root'):
                rc.prepare(self.fake.name,'http://127.0.0.1:3000/',self.state_dir)
        self.assertEqual(self.fake.commands,[])


    def test_also_and_alias_install_effects_blocked(self):
        for field in ('Also=some-other.service','Alias=another.service'):
            self.fake.source='[Unit]\nDescription=Demo\n[Install]\n'+field+'\n'
            with self.subTest(field=field),self.assertRaisesRegex(rc.SafetyError,'Also=/Alias='):
                rc.prepare(self.fake.name,'http://127.0.0.1:3000/',self.state_dir)
            self.assertEqual(self.fake.commands,[])

    def test_unit_file_drift_blocks_apply(self):
        path,plan=self.prepare()
        self.fake.source+='\n# Updated service definition after staging\n'
        with self.assertRaisesRegex(rc.SafetyError,'Unit-file contents changed'):
            rc.apply_plan(path,plan['plan_id'],True)
        self.assertEqual(self.fake.commands,[])

    def test_unit_file_drift_blocks_rollback(self):
        path,plan=self.prepare()
        rc.apply_plan(path,plan['plan_id'],True)
        self.fake.source+='\n# Changed after cutover\n'
        with self.assertRaisesRegex(rc.SafetyError,'Unit definition changed'):
            rc.rollback(path,plan['plan_id'])
        self.assertEqual((self.fake.active,self.fake.enabled),('inactive','disabled'))

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

