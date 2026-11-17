"""Host-free tests. Never contacts systemd, Docker, network, or the Jetson."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
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

    def test_backup_summary_is_shallow_and_does_not_expose_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            backup=root/'backups'; backup.mkdir()
            (backup/'secret-receipt-name.json').write_bytes(b'{}')
            (backup/'voice-usb-recovery-private-id').mkdir()
            (backup/'current').symlink_to(backup/'voice-usb-recovery-private-id')
            missing=root/'missing'
            result=rc.summarize_backup_locations((backup,missing))
        summary=result[0]
        self.assertEqual(summary['direct_children'],3)
        self.assertEqual(summary['direct_child_types'],
                         {'regular_files':1,'directories':1,'symlinks':1,'other':0})
        self.assertEqual(summary['direct_regular_file_bytes'],2)
        self.assertFalse(result[1]['exists'])
        serialized=str(result)
        self.assertNotIn('secret-receipt-name',serialized)
        self.assertNotIn('voice-usb-recovery-private-id',serialized)

    def test_identity_markers_are_bounded_and_host_keys_omit_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            model=root/'model'; model.write_bytes(b'NVIDIA Jetson Orin Nano\x00')
            public_bytes=b'bounded test public key bytes'
            public=root/'ssh_host_ed25519_key.pub'
            public.write_text('ssh-ed25519 '+base64.b64encode(public_bytes).decode()
                              +' private-host-comment\n')
            link=root/'linked.pub'; link.symlink_to(public)
            model_result=rc.read_device_model(model)
            keys=rc.ssh_host_key_fingerprints((public,link,root/'missing.pub'))
        self.assertEqual(model_result['value'],'NVIDIA Jetson Orin Nano')
        self.assertEqual(keys[0]['key_type'],'ssh-ed25519')
        expected='SHA256:'+base64.b64encode(hashlib.sha256(public_bytes).digest()).decode().rstrip('=')
        self.assertEqual(keys[0]['sha256'],expected)
        self.assertTrue(keys[1]['symlink'])
        self.assertNotIn('sha256',keys[1])
        self.assertFalse(keys[2]['exists'])
        self.assertNotIn('private-host-comment',str(keys))

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
        root = Path(self.tmp.name)
        self.state_dir = root / 'state'
        self.state_dir.mkdir()
        self.fake = FakeSystem()
        self.manifest_path = root / 'backups' / self.fake.name / 'manifest.json'
        _write_manifest(self.manifest_path, _manifest(self.fake.name))
        self.probe_ok={'http_ok':True,'status':200}
        patches=[patch.object(rc,'root_required'),patch.object(rc,'jetson_required'),
                 patch.object(rc,'machine_tag',return_value='test-jetson-01'),
                 patch.object(rc,'show_unit',side_effect=self.fake.show),
                 patch.object(rc.RUN,'require',side_effect=self.fake.require),
                 patch.object(rc,'probe_airi',return_value=self.probe_ok),
                 patch.object(rc,'build_consumer_map',
                              side_effect=lambda unit, state, **_kwargs: rc.empty_consumer_map(unit)),
                 patch.object(rc,'backup_manifest_path', return_value=self.manifest_path)]
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

def _manifest(unit, machine='test-jetson-01', **overrides):
    body = {
        'schema': rc.BACKUP_MANIFEST_SCHEMA,
        'tool': rc.BACKUP_MANIFEST_TOOL,
        'unit': unit,
        'machine_tag': machine,
        'created_utc': '2026-11-17T00:00:00+00:00',
        'restore_verified': True,
        'sha256': 'a' * 64,
    }
    body.update(overrides)
    return body


def _write_manifest(path, body, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding='utf-8')
    os.chmod(path, mode)


def _sub_chunk(client, topic, qos=0, identifier=0, options=0):
    fixed = (
        identifier.to_bytes(4, 'big')
        + len(client).to_bytes(2, 'big')
        + len(topic).to_bytes(2, 'big')
        + bytes((qos, options, 0, 0))
    )
    payload = fixed + client.encode() + topic.encode()
    return (5).to_bytes(4, 'big') + len(payload).to_bytes(4, 'big') + payload


def _mosquitto_db(*chunks, version=6):
    magic = bytes((0x00, 0xB5, 0x00)) + b'mosquitto db'
    return magic + b'\x11\x22\x33\x44' + version.to_bytes(4, 'big') + b''.join(chunks)


class ScriptedRunner:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def run(self, args, timeout=15):
        self.calls.append(list(args))
        stdout, code = self.mapping.get(tuple(args), ('', 1))
        return subprocess.CompletedProcess(args, code, stdout, '')


class TestReviewedAllowlist(unittest.TestCase):
    def test_builtin_entries_are_the_four_exact_legacy_units(self):
        allow = rc.load_reviewed_allowlist(None)
        self.assertEqual(set(allow), set(rc.LEGACY_UI_UNITS))
        for unit, entry in allow.items():
            self.assertEqual(entry['unit'], unit)
            self.assertNotIn('*', unit)
            self.assertNotIn('?', unit)
            self.assertTrue(entry['review_id'])
            self.assertTrue(entry['reason'])

    def test_patterns_and_protected_units_are_rejected(self):
        for unit in ('orin-*.service', 'sentient-?.service', 'sentient-web-chat.service ',
                     'mosquitto.service', '../sshd.service', 'sshd.service'):
            document = {'schema': 1, 'entries': [{
                'unit': unit, 'review_id': 'gate-e', 'reason': 'reviewed candidate'}]}
            with self.subTest(unit=unit), self.assertRaises(rc.SafetyError):
                rc.parse_allowlist_document(document)

    def test_new_candidate_requires_an_exact_entry(self):
        state = FakeSystem('avatar-bridge.service').show('avatar-bridge.service')
        with self.assertRaisesRegex(rc.SafetyError, 'exact reviewed allowlist entry'):
            rc.check_unit_policy('avatar-bridge.service', state)
        document = {'schema': 1, 'entries': [{
            'unit': 'avatar-bridge.service',
            'review_id': 'gate-e-avatar',
            'reason': 'AIRI avatar passed and consumers were mapped',
        }]}
        allow = rc.load_reviewed_allowlist(None)
        allow.update(rc.parse_allowlist_document(document))
        entry = rc.check_unit_policy('avatar-bridge.service', state, allow)
        self.assertEqual(entry['review_id'], 'gate-e-avatar')
        with self.assertRaises(rc.SafetyError):
            rc.check_unit_policy('avatar-bridge.service.extra', state, allow)
        with self.assertRaises(rc.SafetyError):
            rc.check_unit_policy('Sentient-web-chat.service', state)
        wanted = FakeSystem().show('sentient-web-chat.service')
        wanted['WantedBy'] = 'multi-user.target'
        self.assertEqual(
            rc.check_unit_policy('sentient-web-chat.service', wanted)['unit'],
            'sentient-web-chat.service')

    def test_allowlist_file_must_be_regular_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'allow.json'
            path.write_text(json.dumps({
                'schema': 1,
                'entries': [{
                    'unit': 'avatar-bridge.service',
                    'review_id': 'gate-e-avatar',
                    'reason': 'reviewed once',
                }],
            }), encoding='utf-8')
            merged = rc.load_reviewed_allowlist(path)
            self.assertIn('avatar-bridge.service', merged)
            self.assertIn('sentient-web-chat.service', merged)
            link = root / 'linked.json'
            link.symlink_to(path)
            with self.assertRaisesRegex(rc.SafetyError, 'symlink'):
                rc.load_reviewed_allowlist(link)
            repeat = root / 'repeat.json'
            repeat.write_text(json.dumps({
                'schema': 1,
                'entries': [{
                    'unit': 'sentient-web-chat.service',
                    'review_id': 'other',
                    'reason': 'duplicate of a built-in',
                }],
            }), encoding='utf-8')
            with self.assertRaisesRegex(rc.SafetyError, 'repeats'):
                rc.load_reviewed_allowlist(repeat)


class TestConsumerParsers(unittest.TestCase):
    def test_ss_listeners_keep_only_unit_pids_and_their_clients(self):
        listen = rc.parse_ss_table(
            'LISTEN 0 128 127.0.0.1:3001 0.0.0.0:* users:(("python",pid=1234,fd=5))\n'
            'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=99,fd=3))\n'
            'LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:(("python",pid=11,fd=9))\n'
            'LISTEN 0 128 [::]:8443 [::]:* users:(("python",pid=1234,fd=11))\n'
            'UNCONN 0 0 127.0.0.1:5353 0.0.0.0:* users:(("python",pid=1234,fd=7))\n',
            proto='tcp')
        # The helper tags whatever proto the caller passes; UDP is parsed separately.
        udp = rc.parse_ss_table(
            'UNCONN 0 0 127.0.0.1:5353 0.0.0.0:* users:(("python",pid=1234,fd=7))\n',
            proto='udp')
        tcp_listen = [row for row in listen if row['state'] == 'LISTEN']
        established = rc.parse_ss_table(
            'ESTAB 0 0 127.0.0.1:3001 127.0.0.1:40000 users:(("python",pid=1234,fd=6))\n'
            'ESTAB 0 0 127.0.0.1:40000 127.0.0.1:3001 users:(("firefox",pid=77,fd=12))\n'
            'ESTAB 0 0 192.168.1.5:8080 192.168.1.9:3333 users:(("python",pid=11,fd=10))\n'
            'ESTAB 0 0 [::1]:8443 [::1]:9999 users:(("python",pid=1234,fd=12))\n'
            'ESTAB 0 0 [::1]:9999 [::1]:8443 users:(("curl",pid=55,fd=4))\n'
            'ESTAB 0 0 127.0.0.1:51000 127.0.0.1:1883 users:(("python",pid=1234,fd=8))\n'
            'ESTAB 0 0 192.168.1.20:5000 127.0.0.1:22 users:(("sshd",pid=99,fd=4))\n',
            proto='tcp')
        mapped = rc.associate_listener_clients(tcp_listen + udp, established, {1234, 11})
        by_local = {item['local']: item for item in mapped}
        self.assertNotIn('0.0.0.0:22', by_local)
        web = by_local['127.0.0.1:3001']
        self.assertEqual(web['clients'], [{
            'peer': '127.0.0.1:40000', 'pid': 77, 'process': 'firefox', 'fd': 12,
        }])
        wildcard = by_local['0.0.0.0:8080']
        self.assertEqual(wildcard['clients'], [{
            'peer': '192.168.1.9:3333', 'pid': None, 'process': None, 'fd': None,
        }])
        v6 = by_local['[::]:8443']
        self.assertEqual(v6['clients'][0]['pid'], 55)
        self.assertEqual(v6['clients'][0]['process'], 'curl')
        self.assertEqual(by_local['127.0.0.1:5353']['proto'], 'udp')
        self.assertEqual(by_local['127.0.0.1:5353']['clients'], [])
        mqtt = rc.mqtt_connections_for_pids(established, {1234})
        self.assertEqual(mqtt, [{
            'proto': 'tcp', 'local': '127.0.0.1:51000', 'peer': '127.0.0.1:1883',
            'pid': 1234, 'process': 'python', 'fd': 8,
        }])

    def test_reverse_dependency_lists_and_closure_are_exact_names(self):
        state = {
            'RequiredBy': 'avatar-bridge.service tool-runner.service',
            'WantedBy': 'multi-user.target',
            'PartOf': 'sentient-core.target',
            'BoundBy': '',
        }
        lists = rc.dependency_lists(state)
        self.assertEqual(lists['reverse']['RequiredBy'],
                         ['avatar-bridge.service', 'tool-runner.service'])
        self.assertEqual(lists['reverse']['WantedBy'], ['multi-user.target'])
        self.assertEqual(lists['forward']['PartOf'], ['sentient-core.target'])
        self.assertEqual(lists['reverse']['BoundBy'], [])
        closure = rc.dependency_closure(
            'sentient-web-chat.service',
            'sentient-web-chat.service\nmulti-user.target\n'
            '● graphical.target\nLegend: the tree above\n')
        self.assertEqual(closure, ['graphical.target', 'multi-user.target'])

    def test_mosquitto_db_subscriptions_skip_message_payloads(self):
        secret = b'SUPER_SECRET_PAYLOAD'
        blob = _mosquitto_db(
            (2).to_bytes(4, 'big') + len(secret).to_bytes(4, 'big') + secret,
            _sub_chunk('airi-hearing', 'sentient/voice/#', qos=1, identifier=7),
            _sub_chunk('web-chat', 'sentient/chat', qos=0),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'mosquitto.db'
            path.write_bytes(blob)
            found = rc.read_mosquitto_subscriptions(path)
            link = root / 'linked.db'
            link.symlink_to(path)
            with self.assertRaisesRegex(rc.SafetyError, 'symlink'):
                rc.read_mosquitto_subscriptions(link)
            missing = rc.read_mosquitto_subscriptions(root / 'absent.db')
        self.assertIsNone(missing)
        self.assertEqual(found, [
            {'client_id': 'airi-hearing', 'topic': 'sentient/voice/#',
             'qos': 1, 'options': 0, 'identifier': 7},
            {'client_id': 'web-chat', 'topic': 'sentient/chat',
             'qos': 0, 'options': 0, 'identifier': 0},
        ])
        self.assertNotIn(secret.decode(), json.dumps(found))
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / 'bad.db'
            bad.write_bytes(b'not a mosquitto db')
            with self.assertRaises(rc.SafetyError):
                rc.read_mosquitto_subscriptions(bad)

    def test_cgroup_pids_do_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            procs = root / 'system.slice' / 'sentient-web-chat.service'
            procs.mkdir(parents=True)
            (procs / 'cgroup.procs').write_text('1234\n1235\n', encoding='ascii')
            linked = root / 'linked'
            linked.mkdir()
            (linked / 'cgroup.procs').symlink_to(procs / 'cgroup.procs')
            state = {'MainPID': '1234', 'ControlGroup': '/system.slice/sentient-web-chat.service'}
            pids, note = rc.unit_pids(state, root)
            self.assertEqual(pids, [1234, 1235])
            self.assertIsNone(note)
            escaped = {'MainPID': '9', 'ControlGroup': '/system.slice/../../etc'}
            pids, _note = rc.unit_pids(escaped, root)
            self.assertEqual(pids, [9])
            state['ControlGroup'] = '/linked'
            pids, _note = rc.unit_pids(state, root)
            self.assertEqual(pids, [1234])

    def test_mqtt_observer_reads_sys_and_ignores_other_topics(self):
        seen = {}
        ready = threading.Event()

        def read_packet(conn):
            header = conn.recv(1)
            self.assertEqual(len(header), 1)
            value = 0
            multiplier = 1
            while True:
                digit = conn.recv(1)[0]
                value += (digit & 0x7f) * multiplier
                if not digit & 0x80:
                    break
                multiplier *= 128
            body = b''
            while len(body) < value:
                body += conn.recv(value - len(body))
            return header[0], body

        def publish(conn, topic, payload):
            encoded = topic.encode()
            body = len(encoded).to_bytes(2, 'big') + encoded + payload.encode()
            conn.sendall(bytes((0x30, len(body))) + body)

        def serve():
            server = socket.socket()
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            seen['port'] = server.getsockname()[1]
            ready.set()
            server.settimeout(5)
            conn, _addr = server.accept()
            try:
                conn.settimeout(5)
                packet_type, body = read_packet(conn)
                seen['connect_type'] = packet_type
                seen['connect'] = body
                conn.sendall(bytes.fromhex('20020000'))
                packet_type, body = read_packet(conn)
                seen['subscribe_type'] = packet_type
                seen['subscribe'] = body
                conn.sendall(bytes.fromhex('9003000100'))
                publish(conn, '$SYS/broker/subscriptions/count', '2')
                publish(conn, '$SYS/broker/version', 'mosquitto 2.0.18')
                publish(conn, 'sentient/secret', 'should-not-capture')
                conn.shutdown(socket.SHUT_WR)
                try:
                    conn.recv(64)
                except OSError:
                    pass
            finally:
                conn.close()
                server.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(2))
        observed = rc.mqtt_observe_sys('127.0.0.1', seen['port'], 2.0)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed['$SYS/broker/subscriptions/count'], '2')
        self.assertEqual(observed['$SYS/broker/version'], 'mosquitto 2.0.18')
        self.assertNotIn('sentient/secret', observed)
        self.assertEqual(seen['connect_type'], 0x10)
        self.assertIn(b'\x04MQTT\x04\x02', seen['connect'])
        self.assertEqual(seen['subscribe_type'], 0x82)
        self.assertIn(b'$SYS/#', seen['subscribe'])
        self.assertNotIn(b'\x00\x01#', seen['subscribe'])
        with self.assertRaises(rc.SafetyError):
            rc.mqtt_observe_sys('192.168.1.159', 1883, 0.2)
        closed = socket.socket()
        closed.bind(('127.0.0.1', 0))
        closed_port = closed.getsockname()[1]
        closed.close()
        with self.assertRaises(rc.SafetyError):
            rc.mqtt_observe_sys('127.0.0.1', closed_port, 1.0)

    def test_build_consumer_map_joins_command_fixtures(self):
        unit = 'sentient-web-chat.service'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cgroup = root / 'system.slice' / unit
            cgroup.mkdir(parents=True)
            (cgroup / 'cgroup.procs').write_text('1234\n', encoding='ascii')
            db = root / 'mosquitto.db'
            db.write_bytes(_mosquitto_db(
                (2).to_bytes(4, 'big') + len(b'SUPER_SECRET_PAYLOAD').to_bytes(4, 'big')
                + b'SUPER_SECRET_PAYLOAD',
                _sub_chunk('web-chat', 'sentient/chat'),
            ))
            listen = 'LISTEN 0 128 127.0.0.1:3001 0.0.0.0:* users:(("python",pid=1234,fd=5))\n'
            established = (
                'ESTAB 0 0 127.0.0.1:3001 127.0.0.1:40000 users:(("python",pid=1234,fd=6))\n'
                'ESTAB 0 0 127.0.0.1:40000 127.0.0.1:3001 users:(("firefox",pid=77,fd=12))\n'
                'ESTAB 0 0 127.0.0.1:51000 127.0.0.1:1883 users:(("python",pid=1234,fd=8))\n'
            )
            mapping = {
                ('ss', '-H', '-lntp'): (listen, 0),
                ('ss', '-H', '-lnup'): ('', 0),
                ('ss', '-H', '-ntp'): (established, 0),
                ('ss', '-H', '-nup'): ('', 0),
                ('systemctl', 'list-dependencies', '--reverse', '--plain',
                 '--no-pager', '--all', unit):
                    (unit + '\nmulti-user.target\n', 0),
            }
            runner = ScriptedRunner(mapping)
            state = {
                'MainPID': '1234',
                'ControlGroup': f'/system.slice/{unit}',
                'RequiredBy': 'avatar-bridge.service',
                'WantedBy': 'multi-user.target',
                'PartOf': 'sentient-core.target',
            }
            consumers = rc.build_consumer_map(
                unit, state, strict=True, runner=runner, cgroup_root=root,
                persistence_path=db, mqtt_timeout=0.2, mqtt_port=1883,
                mqtt_observe=lambda host, port, timeout: {
                    '$SYS/broker/subscriptions/count': '1',
                    '$SYS/broker/version': 'mosquitto 2.0.18',
                })
        self.assertEqual(consumers['unit'], unit)
        self.assertEqual(consumers['unit_pids'], [1234])
        self.assertEqual(consumers['reverse_dependencies']['RequiredBy'],
                         ['avatar-bridge.service'])
        self.assertEqual(consumers['reverse_dependency_closure'], ['multi-user.target'])
        self.assertEqual(consumers['listeners'][0]['clients'][0]['process'], 'firefox')
        self.assertEqual(consumers['mqtt']['subscriptions'][0]['topic'], 'sentient/chat')
        self.assertEqual(consumers['mqtt']['sys']['$SYS/broker/subscriptions/count'], '1')
        self.assertEqual(consumers['mqtt']['unit_connections'][0]['peer'], '127.0.0.1:1883')
        self.assertTrue(any('MQTT' in line for line in consumers['attention']))
        self.assertNotIn('SUPER_SECRET_PAYLOAD', json.dumps(consumers))
        self.assertFalse(any(call[:2] == ['systemctl', 'disable'] for call in runner.calls))
        with self.assertRaises(rc.SafetyError):
            rc.build_consumer_map(
                unit, state, strict=True, runner=ScriptedRunner({}),
                cgroup_root=root, persistence_path=db,
                mqtt_observe=lambda host, port, timeout: {})


class TestBackupManifestGate(unittest.TestCase):
    def test_manifest_contract_rejects_incomplete_or_unverified_backups(self):
        unit = 'sentient-web-chat.service'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = rc.backup_manifest_path(unit, root)
            self.assertEqual(path, root / unit / 'manifest.json')
            body = _manifest(unit)
            _write_manifest(path, body)
            loaded = rc.load_backup_manifest(path, unit=unit, machine='test-jetson-01')
            self.assertEqual(loaded['sha256'], 'a' * 64)
            self.assertNotIn('paths', loaded)
            cases = [
                {'unit': 'cortana-web.service'},
                {'machine_tag': 'other-machine'},
                {'restore_verified': False},
                {'restore_verified': 'true'},
                {'sha256': 'A' * 64},
                {'sha256': 'abcd'},
                {'created_utc': '2026-11-17T00:00:00'},
                {'schema': 2},
                {'tool': 'other'},
            ]
            for override in cases:
                broken = _manifest(unit)
                broken.update(override)
                _write_manifest(path, broken)
                with self.subTest(override=override), self.assertRaisesRegex(rc.SafetyError, 'WS1 backup manifest'):
                    rc.load_backup_manifest(path, unit=unit, machine='test-jetson-01')
            _write_manifest(path, body, mode=0o666)
            with self.assertRaisesRegex(rc.SafetyError, 'world-writable'):
                rc.load_backup_manifest(path, unit=unit, machine='test-jetson-01')
            link = root / 'link.json'
            link.symlink_to(path)
            with self.assertRaisesRegex(rc.SafetyError, 'symlink'):
                rc.load_backup_manifest(link, unit=unit, machine='test-jetson-01')
            with self.assertRaisesRegex(rc.SafetyError, 'WS1 backup manifest'):
                rc.load_backup_manifest(root / 'missing.json', unit=unit, machine='test-jetson-01')
        with self.assertRaises(rc.SafetyError):
            rc.backup_manifest_path('../sshd.service', root)


class TestConsumerIntegration(unittest.TestCase):
    def test_prepare_and_apply_one_new_allowlisted_unit(self):
        unit = 'avatar-bridge.service'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allow = root / 'allow.json'
            allow.write_text(json.dumps({
                'schema': 1,
                'entries': [{
                    'unit': unit,
                    'review_id': 'gate-e-avatar',
                    'reason': 'AIRI avatar passed and consumers were mapped',
                }],
            }), encoding='utf-8')
            manifest = rc.backup_manifest_path(unit, root / 'backups')
            _write_manifest(manifest, _manifest(unit))
            fake = FakeSystem(unit)
            patches = [
                patch.object(rc, 'root_required'),
                patch.object(rc, 'jetson_required'),
                patch.object(rc, 'machine_tag', return_value='test-jetson-01'),
                patch.object(rc, 'show_unit', side_effect=fake.show),
                patch.object(rc.RUN, 'require', side_effect=fake.require),
                patch.object(rc, 'probe_airi', return_value={'http_ok': True, 'status': 200}),
                patch.object(rc, 'build_consumer_map',
                             side_effect=lambda name, state, **_kwargs: rc.empty_consumer_map(name)),
            ]
            for item in patches:
                item.start()
            try:
                plan_path = rc.prepare(unit, 'http://127.0.0.1:3000/', root / 'state',
                                       allowlist_path=allow)
                with self.assertRaisesRegex(rc.SafetyError, 'non-allowlisted'):
                    rc.load_plan(plan_path)
                plan = rc.load_plan(plan_path, allowlist_path=allow)
                self.assertEqual(plan['allowlist_entry']['review_id'], 'gate-e-avatar')
                self.assertEqual(plan['consumers']['unit'], unit)
                self.assertTrue(plan['backup_manifest_path'].endswith(f'{unit}/manifest.json'))
                self.assertEqual(fake.commands, [])
                missing = root / 'missing-manifest.json'
                with self.assertRaisesRegex(rc.SafetyError, 'WS1 backup manifest'):
                    rc.apply_plan(plan_path, plan['plan_id'], True,
                                  backup_manifest=missing, allowlist_path=allow)
                self.assertEqual(fake.commands, [])
                self.assertEqual(rc.load_plan(plan_path, allowlist_path=allow)['phase'], 'prepared')
                applied = rc.apply_plan(plan_path, plan['plan_id'], True,
                                        backup_manifest=manifest, allowlist_path=allow)
                self.assertEqual(applied['phase'], 'pending_acceptance')
                self.assertEqual(applied['backup_manifest']['sha256'], 'a' * 64)
                self.assertEqual((fake.active, fake.enabled), ('inactive', 'disabled'))
                self.assertTrue(all(call[-1] == unit or unit in call for call in fake.commands))
                restored = rc.rollback(plan_path, plan['plan_id'], allowlist_path=allow)
                self.assertEqual(restored['phase'], 'rolled_back')
                self.assertEqual((fake.active, fake.enabled), ('active', 'enabled'))
            finally:
                for item in reversed(patches):
                    item.stop()

    def test_inventory_records_consumers_and_does_not_mutate(self):
        calls = []

        def run(args, timeout=15):
            calls.append(list(args))
            if args[:2] == ['systemctl', 'list-unit-files']:
                stdout = 'sentient-web-chat.service enabled\nssh.service enabled\n'
            elif args[:3] == ['systemctl', 'list-dependencies', '--reverse']:
                stdout = 'sentient-web-chat.service\nmulti-user.target\n'
            else:
                stdout = ''
            return subprocess.CompletedProcess(args, 0, stdout, '')

        def require(args, timeout=15):
            calls.append(list(args))
            return ('LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n'
                    'FragmentPath=/etc/systemd/system/sentient-web-chat.service\n'
                    'Names=sentient-web-chat.service\nMainPID=0\n'
                    'RequiredBy=\nWantedBy=multi-user.target\n')

        with patch.object(rc.RUN, 'run', side_effect=run), \
             patch.object(rc.RUN, 'require', side_effect=require), \
             patch.object(rc, 'mqtt_observe_sys', return_value={'$SYS/broker/version': 'test'}), \
             patch.object(rc, 'read_mosquitto_subscriptions', return_value=[{
                 'client_id': 'airi', 'topic': 'sentient/#', 'qos': 0,
                 'options': 0, 'identifier': 0}]):
            result = rc.inventory()
        chat = next(item for item in result['units'] if item['name'] == 'sentient-web-chat.service')
        self.assertEqual(chat['consumers']['reverse_dependencies']['WantedBy'],
                         ['multi-user.target'])
        self.assertEqual(chat['consumers']['mqtt']['subscriptions'][0]['topic'], 'sentient/#')
        self.assertIn('not permission', result['notice'])
        self.assertFalse(any(call[:2] == ['systemctl', 'disable'] for call in calls))
        self.assertFalse(any(call[:2] == ['systemctl', 'stop'] for call in calls))


if __name__ == '__main__':
    unittest.main()

