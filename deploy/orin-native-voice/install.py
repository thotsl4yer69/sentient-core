"""Apply only to the recorded native runtime, with backup and guarded rollback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

PACKAGE = Path(__file__).resolve().parent
TARGETS = {'handsfree.py': 'voice/handsfree.py',
           'audio_devices.py': 'voice/audio_devices.py',
           'handsfree-controls.js': 'dashboard/static/js/handsfree-controls.js'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_sha(data):
    return sha(data.replace(b'\r\n', b'\n'))


def checked_target(root, relative):
    path = root / relative
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError('Refusing a symlink in target path')
    if not path.parent.is_dir():
        raise ValueError('Runtime directory is missing: ' + relative)
    return path


def atomic_write(path, data, mode=0o600):
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def receipt_write(path, data):
    atomic_write(path, (json.dumps(data, indent=2) + '\n').encode())


def preflight(root, package=PACKAGE):
    root, package = Path(root).resolve(), Path(package).resolve()
    manifest = json.loads((package / 'manifest.json').read_text())
    entries = manifest['files']
    if manifest.get('schema') != 1 or {e['file']: e['target'] for e in entries} != TARGETS or len(entries) != len(TARGETS):
        raise ValueError('Unexpected package manifest')
    states = []
    for entry in entries:
        candidate = package / entry['file']
        content = candidate.read_bytes()
        if sha(content) != entry['sha256']:
            raise ValueError('Package checksum mismatch: ' + entry['file'])
        target = checked_target(root, entry['target'])
        current = canonical_sha(target.read_bytes()) if target.exists() else None
        states.append('installed' if current == canonical_sha(content) else
                      'baseline' if current == entry['baseline_sha256'] else 'unknown')
    if len(set(states)) != 1 or states[0] == 'unknown':
        raise ValueError('Runtime differs from the recorded baseline; no files changed')
    return root, entries, states[0]


class Service:
    def call(self, action):
        if sys.platform != 'linux':
            raise RuntimeError('Live service control requires Linux')
        prefix = [] if os.geteuid() == 0 else ['sudo', '-n']
        result = subprocess.run(prefix + ['systemctl', action, 'orin-voice'],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError('orin-voice service operation failed: ' + action)

    def stop(self):
        self.call('stop')

    def start(self):
        self.call('start')

    def wait_ready(self, root, started):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                self.call('is-active')
                status = json.loads((root.parent / 'run/handsfree/status.json').read_text())
                if (status.get('updated_at', 0) >= started and status.get('phase') in
                        ('wake_listening', 'muted', 'waiting_for_audio', 'listening',
                         'transcribing', 'thinking', 'speaking', 'cooldown')):
                    return
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(.25)
        raise RuntimeError('Fresh voice status was not observed after restart')


def restore_files(root, receipt, directory):
    # Validate every backup before restoring any file.
    for item in receipt['files']:
        if item['before_sha256'] is not None:
            content = (directory / item['backup']).read_bytes()
            if sha(content) != item['before_sha256']:
                raise ValueError('Backup checksum mismatch')
    for item in receipt['files']:
        target = checked_target(root, item['target'])
        if item['before_sha256'] is None:
            if target.exists():
                if sha(target.read_bytes()) != item['after_sha256']:
                    raise ValueError('Refusing to remove a changed file')
                target.unlink()
        else:
            atomic_write(target, (directory / item['backup']).read_bytes(), item['mode'])


def apply(root, backup_root=None, package=PACKAGE, service=None):
    root, entries, state = preflight(root, package)
    if state == 'installed':
        return {'state': 'already_installed'}
    service = service or Service()
    backup_root = Path(backup_root or root.parent / 'backups')
    directory = backup_root / ('voice-usb-recovery-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    receipt_path = directory / 'receipt.json'
    receipt = {'schema': 1, 'root': str(root), 'files': [], 'state': 'preparing'}
    service.stop()
    try:
        preflight(root, package)
        for index, entry in enumerate(entries):
            target = checked_target(root, entry['target'])
            original = target.read_bytes() if target.exists() else None
            backup = str(index) + '.original'
            mode = target.stat().st_mode & 0o777 if original is not None else 0o644
            if original is not None:
                atomic_write(directory / backup, original)
            receipt['files'].append({'target': entry['target'], 'backup': backup,
                                     'mode': mode,
                                     'before_sha256': sha(original) if original is not None else None,
                                     'after_sha256': entry['sha256']})
        receipt['state'] = 'prepared'
        receipt_write(receipt_path, receipt)
        for entry in entries:
            item = next(i for i in receipt['files'] if i['target'] == entry['target'])
            atomic_write(checked_target(root, entry['target']),
                         (Path(package) / entry['file']).read_bytes(), item['mode'])
        started = time.time()
        service.start()
        service.wait_ready(root, started)
        receipt['state'] = 'installed'
        receipt_write(receipt_path, receipt)
        return {'state': 'installed', 'receipt': str(receipt_path)}
    except BaseException:
        service.stop()
        restore_files(root, receipt, directory)
        service.start()
        receipt['state'] = 'automatically_rolled_back'
        receipt_write(receipt_path, receipt)
        raise


def rollback(receipt_path, service=None):
    receipt_path = Path(receipt_path).resolve()
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('schema') != 1 or receipt.get('state') != 'installed':
        raise ValueError('Receipt does not describe an installed update')
    root = Path(receipt['root']).resolve()
    if {i['target'] for i in receipt['files']} != set(TARGETS.values()) or len(receipt['files']) != len(TARGETS):
        raise ValueError('Unexpected rollback targets')
    def verify_current():
        for item in receipt['files']:
            if not re.fullmatch(r'[0-9]+\.original', item['backup']):
                raise ValueError('Unexpected backup name')
            target = checked_target(root, item['target'])
            if not target.is_file() or sha(target.read_bytes()) != item['after_sha256']:
                raise ValueError('Runtime changed after installation; refusing rollback')
    verify_current()
    service = service or Service()
    service.stop()
    try:
        verify_current()
        restore_files(root, receipt, receipt_path.parent)
        started = time.time()
        service.start()
        service.wait_ready(root, started)
        receipt['state'] = 'rolled_back'
        receipt_write(receipt_path, receipt)
    except BaseException:
        service.start()
        raise
    return {'state': 'rolled_back'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'apply', 'rollback'])
    parser.add_argument('--root', type=Path, default=Path('/mnt/orin-ai/orin'))
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.action == 'preflight':
        root, _, state = preflight(args.root)
        result = {'state': state, 'root': str(root), 'changed': False}
    elif args.action == 'apply':
        result = apply(args.root)
    else:
        if args.receipt is None:
            parser.error('--receipt is required for rollback')
        result = rollback(args.receipt)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
