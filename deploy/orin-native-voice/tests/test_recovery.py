import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
import audio_devices
import install


def load_listener():
    modules = {}
    for name in ('orin', 'orin.core', 'orin.voice'):
        modules[name] = types.ModuleType(name)
    for name, fields in {
        'orin.core.progress_watchdog': {'watchdog': Mock()},
        'orin.voice.capture_diagnostics': {'record': Mock(), 'stamp': Mock(), 'health': Mock()},
        'orin.voice.telemetry': {'Telemetry': Mock},
        'orin.voice.audio_devices': vars(audio_devices),
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(fields)
        modules[name] = module
    spec = importlib.util.spec_from_file_location('native_handsfree', PACKAGE / 'handsfree.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


native = load_listener()


class DeviceRecoveryTests(unittest.TestCase):
    def listener(self):
        listener = native.Listener.__new__(native.Listener)
        listener._pulse_default = Mock(return_value=None)
        return listener

    def test_usb_replug_replaces_still_available_fallback(self):
        listener = self.listener()
        inventory = {'sources': ['analog'], 'sinks': ['hdmi']}
        listener._pulse_devices = lambda kind: inventory[kind]
        with patch.dict(os.environ, {}, clear=True), patch.object(native, 'SOURCE', native.USB_SOURCE), patch.object(native, 'SINK', native.USB_SINK):
            self.assertTrue(listener.devices_ready())
            self.assertEqual((native.SOURCE, native.SINK), ('analog', 'hdmi'))
            inventory['sources'].append(native.USB_SOURCE)
            inventory['sinks'].append(native.USB_SINK)
            self.assertTrue(listener.devices_ready())
            self.assertEqual((native.SOURCE, native.SINK), (native.USB_SOURCE, native.USB_SINK))

    def test_explicit_missing_source_is_not_silently_substituted(self):
        result = audio_devices.select_devices(['analog', 'usb'], ['hdmi'], 'analog', 'hdmi', 'usb', 'hdmi', source_override='missing')
        self.assertEqual(result, ('missing', 'hdmi', False))

    def test_explicit_source_and_sink_survive_usb_replug(self):
        result = audio_devices.select_devices(['analog', 'usb'], ['hdmi', 'usb-speaker'], 'analog', 'hdmi', 'usb', 'usb-speaker', source_override='analog', sink_override='hdmi')
        self.assertEqual(result, ('analog', 'hdmi', True))

    def test_monitor_is_not_automatic_microphone(self):
        result = audio_devices.select_devices(['hdmi.monitor'], ['hdmi'], 'hdmi.monitor', 'hdmi', 'usb', 'usb-speaker', default_source='hdmi.monitor')
        self.assertFalse(result[2])

    def test_explicit_monitor_remains_available_for_loopback_canary(self):
        result = audio_devices.select_devices(['hdmi.monitor'], ['hdmi'], 'hdmi.monitor', 'hdmi', 'usb', 'usb-speaker', source_override='hdmi.monitor')
        self.assertTrue(result[2])

    def test_input_change_resets_capture_before_any_new_audio_is_read(self):
        listener = self.listener()
        listener.last_device_check = 0
        listener.capture_source = 'analog'
        listener._pulse_devices = lambda kind: [native.USB_SOURCE] if kind == 'sources' else [native.USB_SINK]
        with patch.dict(os.environ, {}, clear=True), patch.object(native, 'SOURCE', 'analog'), patch.object(native, 'SINK', native.USB_SINK), patch.object(native.time, 'monotonic', return_value=10), patch.object(native.os, 'read') as read:
            with self.assertRaises(native.AudioSourceChanged):
                listener.frame()
            read.assert_not_called()

    def test_partial_disconnection_does_not_report_ready(self):
        result = audio_devices.select_devices(['usb'], [], 'usb', 'old', 'usb', 'speaker')
        self.assertFalse(result[2])


class SignalTests(unittest.TestCase):
    def test_exact_eight_seconds_of_zero_frames_is_unhealthy(self):
        health = audio_devices.InputSignalHealth()
        for _ in range(99):
            health.observe(bytes(2560))
        self.assertEqual(health.state, 'checking')
        health.observe(bytes(2560))
        self.assertEqual(health.status(), {'state': 'digital_silence', 'has_signal': False, 'observed_seconds': 8.0, 'digital_zero_seconds': 8.0})

    def test_nonzero_sample_immediately_clears_silence(self):
        health = audio_devices.InputSignalHealth()
        health.observe(bytes(256000))
        self.assertTrue(health.observe(b'\x01\x00'))
        self.assertEqual(health.state, 'signal_present')
        self.assertEqual(health.zero_samples, 0)

    def test_ordinary_quiet_noise_is_not_digital_silence(self):
        health = audio_devices.InputSignalHealth()
        for _ in range(200):
            health.observe(b'\x01\x00' + bytes(2558))
        self.assertEqual(health.state, 'signal_present')

    def test_reset_and_empty_frames_do_not_invent_signal(self):
        health = audio_devices.InputSignalHealth()
        health.observe(b'\x01\x00')
        health.reset()
        self.assertFalse(health.observe(b''))
        self.assertEqual(health.state, 'unmeasured')
        self.assertEqual(health.samples, 0)

    def test_pcm16_alignment_is_required(self):
        with self.assertRaises(ValueError):
            audio_devices.InputSignalHealth().observe(b'\x01')

    def test_listener_publishes_silent_error_and_recovers_without_restart(self):
        listener = native.Listener.__new__(native.Listener)
        listener.last_device_check = 10
        listener.capture_source = 'analog'
        listener.input_signal = audio_devices.InputSignalHealth()
        listener.error = None
        listener.capture = types.SimpleNamespace(stdout=types.SimpleNamespace(fileno=lambda: 1))
        listener.check = Mock()
        listener.trace = Mock()
        listener.status = Mock()
        with patch.object(native, 'SOURCE', 'analog'), patch.object(native.time, 'monotonic', return_value=10), patch.object(native.select, 'select', return_value=([1], [], [])), patch.object(native.os, 'read', return_value=bytes(2560)) as read:
            for _ in range(100):
                listener.frame()
            self.assertIn('digital silence', listener.error)
            read.return_value = b'\x01\x00' + bytes(2558)
            listener.frame()
            self.assertIsNone(listener.error)
            self.assertEqual(listener.input_signal.state, 'signal_present')


class FakeService:
    def __init__(self, fail=False):
        self.operations = []
        self.fail = fail

    def stop(self):
        self.operations.append('stop')

    def start(self):
        self.operations.append('start')

    def wait_ready(self, root, started):
        self.operations.append('health')
        if self.fail:
            raise RuntimeError('Injected failed health gate')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'orin'
        self.package = self.base / 'package'
        self.package.mkdir()
        self.entries = []
        self.originals = {}
        for name, target in install.TARGETS.items():
            path = self.root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            original = None if name == 'audio_devices.py' else ('original ' + name + '\r\n').encode()
            self.originals[target] = original
            if original is not None:
                path.write_bytes(original)
            content = ('replacement ' + name + '\n').encode()
            (self.package / name).write_bytes(content)
            self.entries.append({'file': name, 'target': target, 'sha256': install.sha(content), 'baseline_sha256': install.canonical_sha(original) if original is not None else None})
        self.write_manifest()

    def write_manifest(self):
        (self.package / 'manifest.json').write_text(json.dumps({'schema': 1, 'files': self.entries}))

    def apply(self, service=None):
        return install.apply(self.root, self.base / 'backups', self.package, service or FakeService())

    def assert_originals(self):
        for target, original in self.originals.items():
            path = self.root / target
            self.assertEqual(path.read_bytes() if path.exists() else None, original)

    def test_apply_is_idempotent_and_rollback_restores_exact_bytes(self):
        service = FakeService()
        result = self.apply(service)
        self.assertEqual(result['state'], 'installed')
        self.assertEqual(service.operations, ['stop', 'start', 'health'])
        self.assertEqual(self.apply(), {'state': 'already_installed'})
        install.rollback(result['receipt'], FakeService())
        self.assert_originals()

    def test_health_failure_automatically_restores_originals(self):
        service = FakeService(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'health gate'):
            self.apply(service)
        self.assert_originals()
        self.assertEqual(service.operations, ['stop', 'start', 'health', 'stop', 'start'])
        receipt = next((self.base / 'backups').glob('*/receipt.json'))
        self.assertEqual(json.loads(receipt.read_text())['state'], 'automatically_rolled_back')

    def test_newer_runtime_is_refused_before_service_stop(self):
        (self.root / 'voice/handsfree.py').write_text('newer work')
        service = FakeService()
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.apply(service)
        self.assertEqual(service.operations, [])
        self.assertEqual((self.root / 'voice/handsfree.py').read_text(), 'newer work')

    def test_corrupted_package_is_refused(self):
        (self.package / 'audio_devices.py').write_text('damaged')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.apply()
        self.assert_originals()

    def test_rollback_preserves_subsequent_runtime_edits(self):
        result = self.apply()
        (self.root / 'voice/handsfree.py').write_text('subsequent work')
        service = FakeService()
        with self.assertRaisesRegex(ValueError, 'changed after'):
            install.rollback(result['receipt'], service)
        self.assertEqual(service.operations, [])
        self.assertEqual((self.root / 'voice/handsfree.py').read_text(), 'subsequent work')

    def test_rollback_validates_all_backups_before_any_restoration(self):
        result = self.apply()
        receipt_path = Path(result['receipt'])
        receipt = json.loads(receipt_path.read_text())
        item = next(i for i in receipt['files'] if i['target'].endswith('.js'))
        (receipt_path.parent / item['backup']).write_text('damaged backup')
        with self.assertRaisesRegex(ValueError, 'Backup checksum'):
            install.rollback(receipt_path, FakeService())
        for entry in self.entries:
            self.assertEqual((self.root / entry['target']).read_bytes(), (self.package / entry['file']).read_bytes())

    def test_manifest_cannot_redirect_target(self):
        self.entries[0]['target'] = '../elsewhere.py'
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.apply()
        self.assertFalse((self.base / 'elsewhere.py').exists())


if __name__ == '__main__':
    unittest.main()
