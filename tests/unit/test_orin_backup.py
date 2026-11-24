"""Host-free tests for the Orin backup tool. No Jetson, systemd, or live Redis."""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import io
import os
import sys
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout

SCRIPT = Path(__file__).resolve().parents[2] / 'tools' / 'orin_backup.py'
spec = importlib.util.spec_from_file_location('orin_backup', SCRIPT)
ob = importlib.util.module_from_spec(spec)
sys.modules['orin_backup'] = ob
spec.loader.exec_module(ob)

GIT_ENV = {
    **os.environ,
    'GIT_AUTHOR_NAME': 'Orin Backup Test',
    'GIT_AUTHOR_EMAIL': 'orin-backup@example.com',
    'GIT_COMMITTER_NAME': 'Orin Backup Test',
    'GIT_COMMITTER_EMAIL': 'orin-backup@example.com',
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_CONFIG_GLOBAL': os.devnull,
}
REDIS_PASSWORD = 'fixture-redis-pass'
OUTSIDE_TOKEN = 'SECRET-TOKEN-not-followed-9f3a2c'
ENV_TOKEN = 'TOKEN-ENV-sentient-backup'


def run_git(args: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(['git', *args], cwd=cwd, env=GIT_ENV, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def write_redis_cli(path: Path) -> None:
    path.write_text(textwrap.dedent('''\
        #!/usr/bin/env python3
        import os
        import sys
        from pathlib import Path

        root = Path(os.environ['ORIN_BACKUP_REDIS_FIXTURE'])
        log = root / 'commands.log'
        with log.open('a', encoding='utf-8') as handle:
            handle.write('ARGV ' + ' '.join(sys.argv[1:]) + '\\n')
            handle.write('AUTH ' + os.environ.get('REDISCLI_AUTH', '') + '\\n')
        expected = os.environ.get('ORIN_BACKUP_REDIS_PASSWORD', '')
        got = os.environ.get('REDISCLI_AUTH', '')
        if expected and got != expected:
            print('NOAUTH Authentication required.', file=sys.stderr)
            raise SystemExit(1)
        args = sys.argv[1:]
        rest = []
        index = 0
        rdb_path = None
        while index < len(args):
            item = args[index]
            if item in {'-h', '-p'} and index + 1 < len(args):
                index += 2
                continue
            if item == '--rdb' and index + 1 < len(args):
                rdb_path = args[index + 1]
                index += 2
                continue
            if item == '--no-auth-warning':
                index += 1
                continue
            rest.append(item)
            index += 1
        if os.environ.get('ORIN_BACKUP_REDIS_FAIL_PING') == '1' and rest[:1] == ['PING']:
            print('connection refused', file=sys.stderr)
            raise SystemExit(1)
        if rdb_path is not None:
            payload = b'NOTARDB'
            if os.environ.get('ORIN_BACKUP_REDIS_BAD') != '1':
                payload = b'REDIS0009-fixture-snapshot'
            Path(rdb_path).write_bytes(payload)
            print('Transferring fixture RDB')
            raise SystemExit(0)
        command = rest[0].upper() if rest else ''
        if command == 'PING':
            print('PONG')
            raise SystemExit(0)
        print('ERR unexpected command', file=sys.stderr)
        raise SystemExit(1)
    '''))
    path.chmod(0o755)


def age_identity(directory: Path) -> tuple[Path, str]:
    key = directory / 'identity.txt'
    subprocess.run(['age-keygen', '-o', str(key)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    public = ''
    for line in key.read_text(encoding='utf-8').splitlines():
        if line.startswith('# public key:'):
            public = line.split(':', 1)[1].strip()
    if not public.startswith('age1'):
        raise AssertionError('age-keygen did not report a public key')
    return key, public


def commit_file(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    run_git(['add', name], cwd=repo)
    run_git(['commit', '-m', f'add {name}'], cwd=repo)


class BackupFixture:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True)
        self.redis_dir = root / 'redis-fixture'
        self.redis_dir.mkdir()
        self.redis_cli = root / 'redis-cli'
        write_redis_cli(self.redis_cli)
        self.identity, self.recipient = age_identity(root)
        self.sqlite_path = root / 'databases' / 'memory.db'
        self.sqlite_path.parent.mkdir()
        connection = sqlite3.connect(self.sqlite_path)
        connection.execute('create table notes(id integer primary key, body text)')
        connection.execute('insert into notes(body) values (?)', ('kept',))
        connection.commit()
        connection.execute('pragma journal_mode=wal')
        connection.close()
        self.airi_real = root / 'airi-real'
        (self.airi_real / 'assets').mkdir(parents=True)
        (self.airi_real / 'settings.json').write_text('{"model":"installed"}\\n', encoding='utf-8')
        (self.airi_real / 'assets' / 'avatar.bin').write_bytes(b'avatar-bytes')
        self.airi_link = root / 'airi-link'
        self.airi_link.symlink_to(self.airi_real, target_is_directory=True)
        self.runtime = root / 'orin'
        (self.runtime / 'voice').mkdir(parents=True)
        (self.runtime / 'voice' / 'handsfree.py').write_text('print("handsfree")\\n', encoding='utf-8')
        outside = root / 'outside-secret.txt'
        outside.write_text(OUTSIDE_TOKEN, encoding='utf-8')
        outside_dir = root / 'outside-dir'
        outside_dir.mkdir()
        (outside_dir / 'hidden.txt').write_text(OUTSIDE_TOKEN, encoding='utf-8')
        (self.runtime / 'linked').symlink_to(outside)
        (self.runtime / 'linked-dir').symlink_to(outside_dir, target_is_directory=True)
        self.etc_dir = root / 'etc'
        self.etc_dir.mkdir()
        self.mosquitto = self.etc_dir / 'mosquitto.conf'
        self.mosquitto.write_text('listener 1883\\nbind_address 127.0.0.1\\n', encoding='utf-8')
        self.systemd_etc = root / 'systemd' / 'etc'
        self.systemd_lib = root / 'systemd' / 'lib'
        dropin = self.systemd_etc / 'sentient-voice.service.d'
        dropin.mkdir(parents=True)
        (self.systemd_etc / 'sentient-voice.service').write_text(
            '[Service]\\nExecStart=/opt/sentient-core/voice\\n', encoding='utf-8')
        (dropin / 'limit.conf').write_text('[Service]\\nMemoryMax=200M\\n', encoding='utf-8')
        self.systemd_lib.mkdir(parents=True)
        (self.systemd_lib / 'redis-server.service').write_text(
            '[Service]\\nExecStart=/usr/bin/redis-server\\n', encoding='utf-8')
        self.dirty_repo = root / 'opt-sentient-core'
        self.clean_repo = root / 'opt-other'
        self._init_repo(self.dirty_repo, dirty=True)
        self._init_repo(self.clean_repo, dirty=False)
        self.env_file = self.dirty_repo / '.env'
        self.env_file.write_text(f'REDIS_PASSWORD={ENV_TOKEN}\\n', encoding='utf-8')
        (self.dirty_repo / 'cache.bin').write_bytes(b'ignored-cache')

    def _init_repo(self, path: Path, *, dirty: bool) -> None:
        path.mkdir()
        run_git(['init', '-b', 'main'], cwd=path)
        commit_file(path, 'README.md', f'readme {path.name}\\n')
        (path / '.gitignore').write_text('.env\ncache.bin\n', encoding='utf-8')
        run_git(['add', '.gitignore'], cwd=path)
        run_git(['commit', '-m', 'ignore local secrets and cache'], cwd=path)
        if dirty:
            (path / 'README.md').write_text('readme dirty\\n', encoding='utf-8')
            (path / 'notes.txt').write_text('untracked note\\n', encoding='utf-8')

    def activate_redis(self) -> None:
        os.environ['ORIN_BACKUP_REDIS_FIXTURE'] = str(self.redis_dir)
        os.environ['ORIN_BACKUP_REDIS_PASSWORD'] = REDIS_PASSWORD

    def request(self) -> ob.BackupRequest:
        return ob.BackupRequest(
            sqlite_paths=(self.sqlite_path,),
            airi_path=self.airi_link,
            runtime_path=self.runtime,
            git_checkouts=(self.dirty_repo, self.clean_repo),
            systemd_roots=(self.systemd_etc, self.systemd_lib),
            units=('sentient-voice.service', 'redis-server.service'),
            etc_paths=(self.mosquitto, self.env_file),
            redis_host='127.0.0.1',
            redis_port=6379,
            redis_password_file=self._password_file(),
            redis_cli=str(self.redis_cli),
            discover_roots=(),
            age_recipients=(self.recipient,),
            age_bin='age',
        )

    def _password_file(self) -> Path:
        path = self.root / 'redis.pass'
        path.write_text(REDIS_PASSWORD + '\n', encoding='utf-8')
        path.chmod(0o600)
        return path


class OrinBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fx = BackupFixture(self.tmp / 'fixture')
        self.fx.activate_redis()
        self.req = self.fx.request()

    def tearDown(self) -> None:
        for key in ('ORIN_BACKUP_REDIS_FIXTURE', 'ORIN_BACKUP_REDIS_PASSWORD',
                    'ORIN_BACKUP_REDIS_BAD', 'ORIN_BACKUP_REDIS_FAIL_PING'):
            os.environ.pop(key, None)
        self._tmp.cleanup()

    def test_round_trip_matches_manifest_and_restores_components(self) -> None:
        dest = self.tmp / 'dest'
        result = ob.backup(self.req, dest)
        self.assertTrue(result['ok'])
        self.assertFalse(result['dry_run'])
        archive = Path(result['archive'])
        self.assertTrue(archive.name.endswith('.tar.age'))
        blob = archive.read_bytes()
        self.assertTrue(blob.startswith(b'age-encryption.org/v1\n'))
        self.assertNotIn(OUTSIDE_TOKEN.encode(), blob)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        sidecar = Path(str(archive) + '.sha256')
        self.assertEqual(sidecar.read_text(encoding='utf-8').split()[0], result['sha256'])
        self.assertEqual(hashlib.sha256(blob).hexdigest(), result['sha256'])
        self.assertEqual(set(dest.iterdir()), {archive, sidecar})
        scratch = self.tmp / 'scratch'
        verified = ob.verify(archive, scratch, self.fx.identity)
        self.assertTrue(verified['ok'])
        self.assertEqual(verified['mismatches'], [])
        self.assertEqual(verified['sqlite_integrity'], ['ok'])
        self.assertTrue(verified['redis_rdb'])
        self.assertTrue(verified['ciphertext_sha256_checked'])
        restored = scratch / 'restored'
        rows = sqlite3.connect(next(restored.glob('sqlite/*.db'))).execute(
            'select body from notes').fetchall()
        self.assertEqual(rows, [('kept',)])
        self.assertTrue((restored / 'redis' / 'dump.rdb').read_bytes().startswith(b'REDIS'))
        self.assertEqual((restored / 'airi' / 'settings.json').read_text(encoding='utf-8'),
                         '{"model":"installed"}\\n')
        self.assertEqual((restored / 'airi' / 'assets' / 'avatar.bin').read_bytes(), b'avatar-bytes')
        self.assertEqual((restored / 'runtime' / 'voice' / 'handsfree.py').read_text(encoding='utf-8'),
                         'print("handsfree")\\n')
        self.assertTrue((restored / 'runtime' / 'linked').is_symlink())
        self.assertEqual(os.readlink(restored / 'runtime' / 'linked'),
                         str(self.tmp / 'fixture' / 'outside-secret.txt'))
        linked_dir = restored / 'runtime' / 'linked-dir'
        self.assertTrue(linked_dir.is_symlink())
        self.assertTrue(stat.S_ISLNK(linked_dir.lstat().st_mode))
        etc_text = '\\n'.join(path.read_text(encoding='utf-8')
                             for path in (restored / 'etc').rglob('*') if path.is_file())
        self.assertIn('listener 1883', etc_text)
        self.assertIn(ENV_TOKEN, etc_text)
        limits = list(restored.rglob('limit.conf'))
        self.assertEqual(len(limits), 1)
        self.assertIn('MemoryMax=200M', limits[0].read_text(encoding='utf-8'))
        manifest = restored.joinpath('manifest.json').read_text(encoding='utf-8')
        self.assertIn('"schema": 1', manifest)
        self.assertIn(str(self.fx.airi_real), manifest)
        bundles = list(restored.glob('git/*/repo.bundle'))
        self.assertEqual(len(bundles), 2)
        diffs = {path.parent.name: path.read_bytes() for path in restored.glob('git/*/dirty.diff')}
        self.assertTrue(any(diff == b'' for diff in diffs.values()))
        self.assertTrue(any(b'readme dirty' in diff for diff in diffs.values()))
        notes = list(restored.glob('git/*/untracked/notes.txt'))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].read_text(encoding='utf-8'), 'untracked note\\n')
        self.assertFalse(list(restored.rglob('cache.bin')))
        untracked_env = list(restored.glob('git/*/untracked/**/.env')) + list(restored.glob('git/*/untracked/.env'))
        self.assertFalse(untracked_env)
        self.assertEqual(len(verified['git']), 2)
        self.assertTrue(all(item['diff_applies'] for item in verified['git']))

    def test_sqlite_online_backup_excludes_uncommitted_rows(self) -> None:
        writer = sqlite3.connect(self.fx.sqlite_path)
        writer.execute('begin')
        writer.execute('insert into notes(body) values (?)', ('hidden',))
        dest = self.tmp / 'dest'
        ob.backup(self.req, dest)
        writer.rollback()
        writer.close()
        scratch = self.tmp / 'scratch'
        archive = next(dest.glob('*.tar.age'))
        ob.verify(archive, scratch, self.fx.identity)
        rows = sqlite3.connect(next((scratch / 'restored').glob('sqlite/*.db'))).execute(
            'select body from notes order by id').fetchall()
        self.assertEqual(rows, [('kept',)])

    def test_dry_run_writes_nothing_and_does_not_snapshot_redis(self) -> None:
        dest = self.tmp / 'dest'
        before = self._source_fingerprint()
        result = ob.backup(self.req, dest, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertIsNone(result['archive'])
        self.assertFalse(dest.exists())
        self.assertEqual(self._source_fingerprint(), before)
        log = (self.fx.redis_dir / 'commands.log').read_text(encoding='utf-8')
        self.assertIn('PING', log)
        self.assertNotIn('--rdb', log)
        self.assertEqual(result['components']['redis']['snapshot'], 'not_run')
        self.assertTrue(result['components']['redis']['ping'])
        dirty = [item['dirty'] for item in result['components']['git']]
        self.assertCountEqual(dirty, [True, False])

    def test_sources_are_unchanged_after_backup(self) -> None:
        before = self._source_fingerprint()
        ob.backup(self.req, self.tmp / 'dest')
        self.assertEqual(self._source_fingerprint(), before)

    def test_redis_password_is_not_placed_on_the_command_line(self) -> None:
        ob.backup(self.req, self.tmp / 'dest')
        log = (self.fx.redis_dir / 'commands.log').read_text(encoding='utf-8')
        argv_lines = [line for line in log.splitlines() if line.startswith('ARGV ')]
        self.assertTrue(argv_lines)
        for line in argv_lines:
            self.assertNotIn(REDIS_PASSWORD, line)
        self.assertIn(f'AUTH {REDIS_PASSWORD}', log)

    def test_ignored_secret_fails_until_named_with_etc(self) -> None:
        req = dataclasses.replace(self.req, etc_paths=(self.fx.mosquitto,))
        dest = self.tmp / 'dest'
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(req, dest)
        self.assertIn('.env', str(caught.exception))
        self.assertFalse(dest.exists())
        ob.backup(self.req, self.tmp / 'dest-ok')

    def test_named_missing_component_fails_without_archive(self) -> None:
        missing = self.tmp / 'missing'
        cases = {
            'sqlite': dataclasses.replace(self.req, sqlite_paths=(missing / 'memory.db',)),
            'airi': dataclasses.replace(self.req, airi_path=missing / 'airi'),
            'runtime': dataclasses.replace(self.req, runtime_path=missing / 'orin'),
            'etc': dataclasses.replace(self.req, etc_paths=(missing / 'mosquitto.conf',)),
            'git': dataclasses.replace(self.req, git_checkouts=(missing / 'repo',)),
            'unit': dataclasses.replace(self.req, units=('missing.service',)),
            'systemd': dataclasses.replace(self.req, systemd_roots=(missing / 'systemd',)),
        }
        needles = {
            'sqlite': 'memory.db',
            'airi': 'airi',
            'runtime': 'orin',
            'etc': 'mosquitto.conf',
            'git': 'repo',
            'unit': 'missing.service',
            'systemd': 'systemd',
        }
        for name, req in cases.items():
            with self.subTest(component=name):
                dest = self.tmp / f'dest-{name}'
                with self.assertRaises(ob.BackupError) as caught:
                    ob.backup(req, dest)
                self.assertIn(needles[name], str(caught.exception))
                self.assertFalse(dest.exists())

    def test_not_a_sqlite_database_fails(self) -> None:
        bogus = self.tmp / 'notes.txt'
        bogus.write_text('not a database', encoding='utf-8')
        req = dataclasses.replace(self.req, sqlite_paths=(bogus,))
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(req, self.tmp / 'dest')
        self.assertIn('notes.txt', str(caught.exception))

    def test_redis_non_loopback_fails_without_connecting(self) -> None:
        req = dataclasses.replace(self.req, redis_host='192.168.1.159')
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(req, self.tmp / 'dest')
        self.assertIn('loopback', str(caught.exception).lower())
        self.assertFalse((self.fx.redis_dir / 'commands.log').exists())

    def test_bad_rdb_header_fails_closed(self) -> None:
        os.environ['ORIN_BACKUP_REDIS_BAD'] = '1'
        dest = self.tmp / 'dest'
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(self.req, dest)
        self.assertIn('RDB', str(caught.exception))
        self.assertFalse(dest.exists())

    @unittest.skipIf(os.geteuid() == 0, 'root can read a mode-0 file; CI also runs this as a normal user')
    def test_unreadable_file_fails(self) -> None:
        target = self.fx.runtime / 'voice' / 'handsfree.py'
        target.chmod(0)
        try:
            with self.assertRaises(ob.BackupError) as caught:
                ob.backup(self.req, self.tmp / 'dest')
            self.assertIn('handsfree.py', str(caught.exception))
            self.assertFalse((self.tmp / 'dest').exists())
        finally:
            target.chmod(0o644)

    def test_dest_inside_source_fails(self) -> None:
        dest = self.fx.runtime / 'backups'
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(self.req, dest)
        self.assertIn('destination', str(caught.exception).lower())
        self.assertFalse(dest.exists())

    def test_discovery_includes_extra_checkout_and_records_absent_roots(self) -> None:
        extra = self.tmp / 'extra-repo'
        extra.mkdir()
        run_git(['init', '-b', 'main'], cwd=extra)
        commit_file(extra, 'extra.txt', 'extra\\n')
        (extra / 'extra.txt').write_text('extra dirty\\n', encoding='utf-8')
        absent = self.tmp / 'no-such-discover-root'
        req = dataclasses.replace(self.req, discover_roots=(self.tmp, absent))
        dest = self.tmp / 'dest'
        ob.backup(req, dest)
        scratch = self.tmp / 'scratch'
        ob.verify(next(dest.glob('*.tar.age')), scratch, self.fx.identity)
        restored = scratch / 'restored'
        self.assertEqual(len(list(restored.glob('git/*/repo.bundle'))), 3)
        manifest = (restored / 'manifest.json').read_text(encoding='utf-8')
        self.assertIn(str(absent), manifest)
        self.assertIn('absent', manifest)

    def test_submodule_checkout_fails(self) -> None:
        child = self.tmp / 'child'
        child.mkdir()
        run_git(['init', '-b', 'main'], cwd=child)
        commit_file(child, 'lib.txt', 'lib\\n')
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=child, env=GIT_ENV, text=True).strip()
        run_git(['update-index', '--add', '--cacheinfo', f'160000,{head},vendor/child'],
                cwd=self.fx.clean_repo)
        run_git(['commit', '-m', 'add submodule'], cwd=self.fx.clean_repo)
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(self.req, self.tmp / 'dest')
        self.assertIn('submodule', str(caught.exception).lower())

    def test_verify_detects_tampered_payload(self) -> None:
        dest = self.tmp / 'dest'
        result = ob.backup(self.req, dest)
        archive = Path(result['archive'])
        original = self.tmp / 'scratch-ok'
        self.assertTrue(ob.verify(archive, original, self.fx.identity)['ok'])
        plain = self.tmp / 'plain.tar.gz'
        subprocess.run(['age', '-d', '-i', str(self.fx.identity), '-o', str(plain), str(archive)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        extracted = self.tmp / 'extracted'
        with tarfile.open(plain, 'r:gz') as tar:
            for member in tar.getmembers():
                if not member.isreg():
                    continue
                target = extracted / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    continue
                target.write_bytes(source.read())
        settings = extracted / 'airi' / 'settings.json'
        settings.write_text('{"model":"tampered"}\\n', encoding='utf-8')
        rebuilt = self.tmp / 'rebuilt.tar.gz'
        with tarfile.open(rebuilt, 'w:gz') as tar:
            for path in sorted(extracted.rglob('*')):
                if path.is_file() and not path.is_symlink():
                    tar.add(path, arcname=path.relative_to(extracted).as_posix())
        tampered = self.tmp / 'tampered.tar.age'
        subprocess.run(['age', '-r', self.fx.recipient, '-o', str(tampered), str(rebuilt)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self.assertRaises(ob.BackupError) as caught:
            ob.verify(tampered, self.tmp / 'scratch-bad', self.fx.identity)
        self.assertIn('airi/settings.json', str(caught.exception))

    def test_verify_rejects_path_traversal(self) -> None:
        plain = self.tmp / 'evil.tar.gz'
        with tarfile.open(plain, 'w:gz') as tar:
            payload = io.BytesIO(b'pwn')
            info = tarfile.TarInfo('../escape')
            info.size = len(payload.getvalue())
            tar.addfile(info, payload)
        evil = self.tmp / 'evil.tar.age'
        subprocess.run(['age', '-r', self.fx.recipient, '-o', str(evil), str(plain)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self.assertRaises(ob.BackupError) as caught:
            ob.verify(evil, self.tmp / 'scratch', self.fx.identity)
        self.assertIn('escape', str(caught.exception))
        self.assertFalse((self.tmp / 'escape').exists())

    def test_verify_refuses_existing_scratch(self) -> None:
        dest = self.tmp / 'dest'
        result = ob.backup(self.req, dest)
        scratch = self.tmp / 'scratch'
        scratch.mkdir()
        (scratch / 'keep.txt').write_text('keep', encoding='utf-8')
        with self.assertRaises(ob.BackupError):
            ob.verify(Path(result['archive']), scratch, self.fx.identity)
        self.assertEqual((scratch / 'keep.txt').read_text(encoding='utf-8'), 'keep')

    def test_profile_nanob_fails_closed_off_device(self) -> None:
        dest = self.tmp / 'nanob-dest'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ob.main([
                'backup', '--profile', 'nanob', '--dest', str(dest),
                '--age-recipient', self.fx.recipient,
                '--no-discover',
            ])
        self.assertEqual(code, 1)
        message = stderr.getvalue()
        self.assertIn(str(ob.NANOB_RUNTIME), message)
        self.assertIn(str(ob.NANOB_GIT_CHECKOUTS[0]), message)
        self.assertIn('sqlite', message.lower())
        self.assertIn('airi', message.lower())
        self.assertFalse(dest.exists())
        self.assertEqual(stdout.getvalue(), '')

    def test_spec_unknown_key_fails_and_known_spec_restores(self) -> None:
        spec_path = self.tmp / 'backup-spec.json'
        body = {
            'schema': 1,
            'sqlite': [str(self.fx.sqlite_path)],
            'airi_path': str(self.fx.airi_link),
            'runtime_path': str(self.fx.runtime),
            'git_checkouts': [str(self.fx.dirty_repo), str(self.fx.clean_repo)],
            'systemd_roots': [str(self.fx.systemd_etc), str(self.fx.systemd_lib)],
            'units': ['sentient-voice.service', 'redis-server.service'],
            'etc_paths': [str(self.fx.mosquitto), str(self.fx.env_file)],
            'redis_host': '127.0.0.1',
            'redis_port': 6379,
            'redis_password_file': str(self.fx._password_file()),
            'redis_cli': str(self.fx.redis_cli),
            'discover_roots': [],
            'age_recipients': [self.fx.recipient],
        }
        spec_path.write_text(__import__('json').dumps(body), encoding='utf-8')
        bad = self.tmp / 'bad-spec.json'
        bad.write_text(__import__('json').dumps({**body, 'password': 'nope'}), encoding='utf-8')
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ob.main(['backup', '--spec', str(bad), '--dest', str(self.tmp / 'nope'), '--no-discover'])
        self.assertEqual(code, 1)
        self.assertIn('password', stderr.getvalue())
        self.assertFalse((self.tmp / 'nope').exists())
        dest = self.tmp / 'from-spec'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ob.main(['backup', '--spec', str(spec_path), '--dest', str(dest), '--no-discover'])
        self.assertEqual(code, 0, stderr.getvalue())
        archive = next(dest.glob('*.tar.age'))
        scratch = self.tmp / 'spec-scratch'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ob.main(['verify', '--archive', str(archive), '--scratch', str(scratch),
                            '--identity', str(self.fx.identity)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn('"ok": true', stdout.getvalue())

    def test_missing_age_binary_fails(self) -> None:
        req = dataclasses.replace(self.req, age_bin=str(self.tmp / 'missing-age'))
        with self.assertRaises(ob.BackupError) as caught:
            ob.backup(req, self.tmp / 'dest')
        self.assertIn('age', str(caught.exception).lower())

    def _source_fingerprint(self) -> list[tuple[str, int, int]]:
        paths = [
            self.fx.sqlite_path,
            self.fx.airi_real / 'settings.json',
            self.fx.airi_real / 'assets' / 'avatar.bin',
            self.fx.runtime / 'voice' / 'handsfree.py',
            self.fx.mosquitto,
            self.fx.env_file,
            self.fx.dirty_repo / 'README.md',
            self.fx.dirty_repo / 'notes.txt',
            self.fx.clean_repo / 'README.md',
            self.fx.systemd_etc / 'sentient-voice.service',
            self.fx.systemd_etc / 'sentient-voice.service.d' / 'limit.conf',
            self.fx.systemd_lib / 'redis-server.service',
        ]
        found = []
        for path in paths:
            info = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else ''
            found.append((str(path), info.st_mtime_ns, info.st_size, digest))
        return found


if __name__ == '__main__':
    unittest.main()


class OsErrorReportingTests(unittest.TestCase):
    def test_permission_error_is_a_clean_message_not_a_traceback(self) -> None:
        import contextlib
        import io

        def denied(*_args, **_kwargs):
            raise PermissionError(13, 'Permission denied', '/etc/redis/redis.conf')

        err = io.StringIO()
        original = ob.backup
        ob.backup = denied
        try:
            with contextlib.redirect_stderr(err):
                code = ob.main(['backup', '--dest', '/nonexistent-dest', '--no-discover'])
        finally:
            ob.backup = original
        self.assertEqual(code, 1)
        self.assertIn('/etc/redis/redis.conf: Permission denied (run with sudo)', err.getvalue())
