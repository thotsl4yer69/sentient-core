#!/usr/bin/env python3
"""Restore-tested backups for the Orin Nano (nanob).

The archive is age-encrypted and carries a sha256 manifest. ``verify`` decrypts
into a scratch directory and checks every restored file against that manifest.

Reads the live system only, except:

* the backup destination (archive, sidecar, and temporary files under it)
* ``redis-cli --rdb``, which asks local Redis for a BGSAVE snapshot and writes
  that copy under the destination

A missing named component fails the run. Paths are not dropped because they
were inconvenient to read. Dry-run performs the same checks and writes nothing.
Python 3.10+, stdlib only. ``age``, ``git``, and ``redis-cli`` are system tools.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tarfile
import urllib.parse

SCHEMA = 1
SPEC_SCHEMA = 1

NANOB_RUNTIME = Path('/mnt/orin-ai/orin')
NANOB_GIT_CHECKOUTS = (Path('/opt/sentient-core'),)
NANOB_SYSTEMD_ROOTS = (
    Path('/etc/systemd/system'),
    Path('/usr/lib/systemd/system'),
    Path('/lib/systemd/system'),
)
NANOB_UNITS = (
    'airi.service',
    'orin-voice.service',
    'ollama.service',
    'mosquitto.service',
    'redis-server.service',
    'sentient-core.target',
    'sentient-web-chat.service',
    'sentient-avatar.service',
    'sentient-voice.service',
    'sentient-conversation.service',
    'sentient-wake-word.service',
)
NANOB_ETC = (
    Path('/etc/mosquitto/mosquitto.conf'),
    Path('/etc/redis/redis.conf'),
    Path('/etc/nvpmodel.conf'),
    Path('/etc/nv_tegra_release'),
)
NANOB_DISCOVERY = (
    Path('/opt/sentient-core'),
    Path('/opt/sentient'),
    Path('/opt/sc'),
    Path('/opt/airi'),
    Path('/home/cortana/sentient-core'),
    Path('/home/cortana/cortana/backend'),
    Path('/home/cortana/airi'),
    Path('/home/nano/airi'),
    Path('/mnt/orin-ai'),
)

LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
UNIT_NAME = re.compile(r'^[A-Za-z0-9_.@-]+\.(service|target|socket|timer|path|mount)$')
AGE_RECIPIENT = re.compile(r'^age1[02-9ac-hj-np-z]{20,}$')
SQLITE_MAGIC = b'SQLite format 3\x00'
SECRET_NAMES = frozenset({
    '.env', 'secrets.env', 'sentient.env', 'credentials', 'credentials.json',
    'id_rsa', 'id_ed25519', 'id_ecdsa',
})
SPEC_KEYS = frozenset({
    'schema', 'sqlite', 'airi_path', 'runtime_path', 'git_checkouts',
    'systemd_roots', 'units', 'etc_paths', 'redis_host', 'redis_port',
    'redis_password_file', 'redis_cli', 'discover_roots', 'age_recipients',
    'age_bin',
})
REQUIRED_SPEC_KEYS = SPEC_KEYS - {'redis_cli', 'age_bin'}
GIT_PREFIX = [
    'git',
    '-c', 'safe.directory=*',
    '-c', 'core.hooksPath=/dev/null',
    '-c', 'core.fsmonitor=',
    '-c', 'filter.lfs.required=false',
]
HEADROOM_BYTES = 32 * 1024 * 1024


class BackupError(RuntimeError):
    def __init__(self, message: str, failures: list[str] | None = None):
        super().__init__(message)
        self.failures = list(failures) if failures else [message]


@dataclasses.dataclass(frozen=True)
class BackupRequest:
    sqlite_paths: tuple[Path, ...]
    airi_path: Path | None
    runtime_path: Path | None
    git_checkouts: tuple[Path, ...]
    systemd_roots: tuple[Path, ...]
    units: tuple[str, ...]
    etc_paths: tuple[Path, ...]
    redis_host: str
    redis_port: int
    redis_password_file: Path | None
    redis_cli: str
    discover_roots: tuple[Path, ...]
    age_recipients: tuple[str, ...]
    age_bin: str
    redis_timeout: float = 60.0


def nanob_request() -> BackupRequest:
    return BackupRequest(
        sqlite_paths=(),
        airi_path=None,
        runtime_path=NANOB_RUNTIME,
        git_checkouts=NANOB_GIT_CHECKOUTS,
        systemd_roots=NANOB_SYSTEMD_ROOTS,
        units=NANOB_UNITS,
        etc_paths=NANOB_ETC,
        redis_host='127.0.0.1',
        redis_port=6379,
        redis_password_file=None,
        redis_cli='redis-cli',
        discover_roots=NANOB_DISCOVERY,
        age_recipients=(),
        age_bin='age',
    )


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def payload_bytes(sqlite_info: list[dict], airi: dict | None, runtime: dict | None,
                  unit_plan: list[dict], etc_plan: list[dict]) -> int:
    """Lower bound on bytes that will be copied, excluding git bundles."""
    total = 0
    for item in sqlite_info:
        try:
            total += item['resolved'].stat().st_size
        except OSError:
            pass
    for info in (airi, runtime):
        if info is None:
            continue
        for record in info['records']:
            total += record_bytes(record)
    for unit in unit_plan:
        for copy in unit['copies']:
            unit_path = copy.get('unit_path')
            if unit_path is not None:
                try:
                    total += unit_path.stat().st_size
                except OSError:
                    pass
            for record in copy.get('dropin') or []:
                total += record_bytes(record)
    for item in etc_plan:
        if item['kind'] == 'file':
            try:
                total += item['resolved'].stat().st_size
            except OSError:
                pass
        else:
            for record in item['records']:
                total += record_bytes(record)
    return total


def record_bytes(record: dict) -> int:
    if record['type'] == 'symlink':
        return len(os.fsencode(record['linkname']))
    try:
        return record['source'].stat().st_size
    except OSError:
        return 0


def raise_failures(failures: list[str]) -> None:
    if failures:
        text = ('Backup refused. Missing or unreadable components are not skipped:\n'
                + '\n'.join(f'- {item}' for item in failures))
        raise BackupError(text, failures)


def run(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: float = 120) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            args, cwd=str(cwd) if cwd else None, env=env, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise BackupError(f'required command {args[0]} is not on PATH') from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f'command timed out: {args[0]}') from exc


def require(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
            timeout: float = 120) -> subprocess.CompletedProcess[bytes]:
    result = run(args, cwd=cwd, env=env, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.decode('utf-8', 'replace').strip()[:400]
        raise BackupError(f'{args[0]} failed ({result.returncode}): {detail}')
    return result


def command_available(name: str) -> bool:
    candidate = Path(name)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return True
    return shutil.which(name) is not None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_z(blob: bytes) -> list[str]:
    if not blob:
        return []
    parts = blob.split(b'\0')
    if parts and parts[-1] == b'':
        parts.pop()
    return [part.decode('utf-8') for part in parts]


def git_env() -> dict[str, str]:
    env = os.environ.copy()
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    env['GIT_CONFIG_GLOBAL'] = os.devnull
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def looks_like_secret(name: str) -> bool:
    lowered = name.lower()
    if lowered in SECRET_NAMES:
        return True
    if lowered.startswith('.env.') and not lowered.endswith(('.example', '.sample')):
        return True
    return lowered.endswith(('.pem', '.key'))


def sqlite_uri(path: Path) -> str:
    quoted = urllib.parse.quote(str(path))
    return f'file:{quoted}?mode=ro'


def inside(child: Path, parent: Path) -> bool:
    try:
        child_resolved = child.resolve()
        parent_resolved = parent.resolve()
    except OSError:
        return False
    if child_resolved == parent_resolved:
        return True
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError:
        return False
    return True


def covered_by(path: Path, named: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for item in named:
        try:
            if not item.exists():
                continue
            item_resolved = item.resolve()
        except OSError:
            continue
        if resolved == item_resolved:
            return True
        if item_resolved.is_dir():
            try:
                resolved.relative_to(item_resolved)
            except ValueError:
                continue
            return True
    return False


def readable_file(path: Path) -> str | None:
    try:
        with path.open('rb') as handle:
            handle.read(1)
    except OSError as exc:
        return f'cannot read {path}: {exc.strerror or exc}'
    return None


def reject_special(path: Path) -> str | None:
    try:
        info = path.lstat()
    except OSError as exc:
        return f'cannot stat {path}: {exc.strerror or exc}'
    special = stat.S_ISFIFO(info.st_mode) or stat.S_ISSOCK(info.st_mode)
    special = special or stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode)
    if special:
        return f'refusing special file {path}'
    return None


def archive_rel(prefix: str, relative: PurePosixPath) -> str:
    text = f'{prefix}/{relative.as_posix()}' if str(relative) not in ('', '.') else prefix
    if text.startswith('/') or '\\' in text or '\n' in text or '\r' in text or '\0' in text:
        raise BackupError(f'refusing archive path {text!r}')
    if any(part in {'', '.', '..'} for part in PurePosixPath(text).parts):
        raise BackupError(f'refusing archive path {text}')
    return text


def walk_records(root: Path, prefix: str) -> tuple[list[dict], list[str]]:
    """Describe a tree without following links inside it."""
    problems: list[str] = []
    records: list[dict] = []
    if not root.exists():
        return records, [f'{root} does not exist']
    if not root.is_dir():
        return records, [f'{root} is not a directory']
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        real_dirs: list[str] = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                link_problem = reject_special(path)
                if link_problem:
                    problems.append(link_problem)
                    continue
                try:
                    linkname = os.readlink(path)
                except OSError as exc:
                    problems.append(f'cannot read symlink {path}: {exc.strerror or exc}')
                    continue
                relative = path.relative_to(root)
                records.append({
                    'source': path,
                    'archive_path': archive_rel(prefix, PurePosixPath(relative.as_posix())),
                    'type': 'symlink',
                    'linkname': linkname,
                })
            else:
                real_dirs.append(name)
        dirnames[:] = real_dirs
        for name in filenames:
            path = current / name
            problem = reject_special(path)
            if problem:
                problems.append(problem)
                continue
            relative = path.relative_to(root)
            arc = archive_rel(prefix, PurePosixPath(relative.as_posix()))
            if path.is_symlink():
                try:
                    linkname = os.readlink(path)
                except OSError as exc:
                    problems.append(f'cannot read symlink {path}: {exc.strerror or exc}')
                    continue
                records.append({
                    'source': path, 'archive_path': arc, 'type': 'symlink', 'linkname': linkname,
                })
                continue
            if not path.is_file():
                problems.append(f'refusing special file {path}')
                continue
            problem = readable_file(path)
            if problem:
                problems.append(problem)
                continue
            records.append({
                'source': path, 'archive_path': arc, 'type': 'file', 'linkname': None,
            })
    if not problems and not records:
        problems.append(f'{root} contains no files')
    return records, problems


def discover_checkouts(roots: tuple[Path, ...], required: set[Path]) -> tuple[list[Path], list[dict]]:
    found: list[Path] = []
    found_resolved: set[Path] = set()
    notes: list[dict] = []
    for root in roots:
        if not root.exists():
            notes.append({'path': str(root), 'state': 'absent'})
            continue
        if root.is_symlink():
            notes.append({'path': str(root), 'state': 'symlink_not_followed'})
            continue
        if not root.is_dir():
            notes.append({'path': str(root), 'state': 'not_a_directory'})
            continue
        candidates = [root]
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise BackupError(f'cannot list discovery root {root}: {exc.strerror or exc}') from exc
        for child in children:
            if not child.is_dir() and not child.is_symlink():
                continue
            if child.is_symlink():
                notes.append({'path': str(child), 'state': 'symlink_not_followed'})
                continue
            candidates.append(child)
        root_is_repo = False
        for candidate in candidates:
            if not (candidate / '.git').exists():
                continue
            resolved = candidate.resolve()
            if candidate == root:
                root_is_repo = True
            if resolved in required or resolved in found_resolved:
                notes.append({'path': str(candidate), 'state': 'already_required'})
                continue
            found.append(candidate)
            found_resolved.add(resolved)
            notes.append({'path': str(candidate), 'state': 'included'})
        if not root_is_repo:
            notes.append({'path': str(root), 'state': 'not_a_git_checkout'})
    return found, notes


def inspect_git(path: Path) -> tuple[dict | None, list[str]]:
    problems: list[str] = []
    if not path.exists():
        return None, [f'git checkout does not exist: {path}']
    if not path.is_dir():
        return None, [f'git checkout is not a directory: {path}']
    env = git_env()
    top = run(GIT_PREFIX + ['-C', str(path), 'rev-parse', '--show-toplevel'], env=env)
    if top.returncode != 0:
        detail = top.stderr.decode('utf-8', 'replace').strip()[:240]
        return None, [f'not a git checkout: {path} ({detail})']
    toplevel = Path(top.stdout.decode().strip()).resolve()
    if toplevel != path.resolve():
        return None, [f'git checkout must be the repository top, not {path} (top is {toplevel})']
    head = run(GIT_PREFIX + ['-C', str(path), 'rev-parse', 'HEAD'], env=env)
    if head.returncode != 0:
        return None, [f'git checkout has no commits: {path}']
    branch = require(GIT_PREFIX + ['-C', str(path), 'rev-parse', '--abbrev-ref', 'HEAD'], env=env)
    stage = require(GIT_PREFIX + ['-C', str(path), 'ls-files', '--stage'], env=env)
    for line in stage.stdout.decode().splitlines():
        if line.startswith('160000 '):
            problems.append(
                f'git checkout has a submodule and must be passed as its own --git-checkout: {path}')
            break
    status = require(GIT_PREFIX + ['-C', str(path), 'status', '--porcelain=v1', '-uall', '-z'], env=env)
    others = require(GIT_PREFIX + ['-C', str(path), 'ls-files', '--others', '--exclude-standard', '-z'], env=env)
    ignored = require(GIT_PREFIX + ['-C', str(path), 'ls-files', '-o', '-i', '--exclude-standard', '-z'], env=env)
    info = {
        'source': path,
        'resolved': path.resolve(),
        'head': head.stdout.decode().strip(),
        'branch': branch.stdout.decode().strip(),
        'dirty': bool(status.stdout.strip(b'\0').strip()),
        'untracked': split_z(others.stdout),
        'ignored': split_z(ignored.stdout),
    }
    return info, problems


def redis_command(request: BackupRequest) -> list[str]:
    return [
        request.redis_cli, '-h', request.redis_host, '-p', str(request.redis_port),
        '--no-auth-warning',
    ]


def redis_environment(request: BackupRequest) -> dict[str, str]:
    env = os.environ.copy()
    env.pop('REDISCLI_AUTH', None)
    if request.redis_password_file is None:
        return env
    if not request.redis_password_file.is_file():
        raise BackupError(f'redis password file does not exist: {request.redis_password_file}')
    try:
        secret = request.redis_password_file.read_text(encoding='utf-8').rstrip('\r\n')
    except OSError as exc:
        raise BackupError(f'cannot read redis password file: {exc.strerror or exc}') from exc
    if secret == '':
        raise BackupError(f'redis password file is empty: {request.redis_password_file}')
    env['REDISCLI_AUTH'] = secret
    return env


def scan(request: BackupRequest, dest: Path) -> tuple[list[str], dict]:
    failures: list[str] = []
    if not request.sqlite_paths:
        failures.append('No SQLite database was named. Pass --sqlite for each database.')
    if request.airi_path is None:
        failures.append('AIRI path is required. Pass --airi-path. Gate A discovers the install path.')
    if request.runtime_path is None:
        failures.append('runtime path is required. The nanob default is /mnt/orin-ai/orin.')
    if not request.units:
        failures.append('No systemd units were named. Pass --unit for each unit file.')
    if not request.systemd_roots:
        failures.append('No systemd roots were named. Pass --systemd-root.')
    if not request.etc_paths:
        failures.append('No /etc paths were named. Pass --etc for each service config.')
    if not request.age_recipients:
        failures.append('An age public key is required. Pass --age-recipient. Refusing to write an unencrypted backup.')
    for recipient in request.age_recipients:
        if not AGE_RECIPIENT.fullmatch(recipient):
            failures.append(f'age recipient is not an age public key: {recipient}')
    if not command_available(request.age_bin):
        failures.append(f'age is required but was not found: {request.age_bin}')
    if not command_available('git'):
        failures.append('git is required but was not found on PATH')
    if not (1 <= request.redis_port <= 65535):
        failures.append(f'redis port is not valid: {request.redis_port}')
    redis_ping = False
    if request.redis_host not in LOOPBACK_HOSTS:
        failures.append(f'Redis backup only talks to loopback, refusing host {request.redis_host}')
    elif not command_available(request.redis_cli):
        failures.append(f'redis-cli is required but was not found: {request.redis_cli}')
    else:
        if request.redis_password_file is not None and not request.redis_password_file.is_file():
            failures.append(f'redis password file does not exist: {request.redis_password_file}')
        else:
            try:
                env = redis_environment(request)
            except BackupError as exc:
                failures.append(str(exc))
            else:
                ping = run(redis_command(request) + ['PING'], env=env, timeout=10)
                if ping.returncode != 0 or ping.stdout.strip() != b'PONG':
                    detail = ping.stderr.decode('utf-8', 'replace').strip()[:240]
                    failures.append(
                        f'Redis at {request.redis_host}:{request.redis_port} did not answer PONG ({detail})')
                else:
                    redis_ping = True

    sqlite_info = []
    for path in request.sqlite_paths:
        if not path.exists():
            failures.append(f'SQLite database does not exist: {path}')
            continue
        if path.is_symlink():
            resolved = path.resolve()
        else:
            resolved = path
        if not resolved.is_file():
            failures.append(f'SQLite database is not a regular file: {path}')
            continue
        try:
            with resolved.open('rb') as handle:
                magic = handle.read(16)
        except OSError as exc:
            failures.append(f'cannot read SQLite database {path}: {exc.strerror or exc}')
            continue
        if magic != SQLITE_MAGIC:
            failures.append(f'not a SQLite database: {path}')
            continue
        sqlite_info.append({'source': path, 'resolved': resolved})

    def tree_component(label: str, path: Path | None) -> dict | None:
        if path is None:
            return None
        if not path.exists():
            failures.append(f'{label} path does not exist: {path}')
            return None
        resolved = path.resolve()
        if not resolved.is_dir():
            failures.append(f'{label} path is not a directory: {path}')
            return None
        records, problems = walk_records(resolved, label)
        failures.extend(problems)
        return {
            'source': str(path),
            'resolved': str(resolved),
            'files': len(records),
            'records': records,
        }

    airi = tree_component('airi', request.airi_path)
    runtime = tree_component('runtime', request.runtime_path)

    systemd_existing: list[Path] = []
    seen_roots: set[Path] = set()
    for root in request.systemd_roots:
        if not root.exists():
            failures.append(f'systemd root does not exist: {root}')
            continue
        if not root.is_dir():
            failures.append(f'systemd root is not a directory: {root}')
            continue
        resolved = root.resolve()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        systemd_existing.append(root)

    unit_plan = []
    for unit in request.units:
        if not UNIT_NAME.fullmatch(unit):
            failures.append(f'refusing unusual unit name: {unit}')
            continue
        found_file = False
        copies = []
        for index, root in enumerate(systemd_existing):
            unit_path = root / unit
            dropin = root / f'{unit}.d'
            if not unit_path.exists() and not dropin.exists():
                continue
            entry = {'root': root, 'index': index, 'unit_path': None, 'dropin': None}
            if unit_path.exists():
                if unit_path.is_dir() and not unit_path.is_symlink():
                    failures.append(f'unit path is a directory: {unit_path}')
                else:
                    target = unit_path.resolve() if unit_path.is_symlink() else unit_path
                    if not target.is_file():
                        failures.append(f'unit file is not a regular file: {unit_path}')
                    else:
                        problem = readable_file(target)
                        if problem:
                            failures.append(problem)
                        else:
                            found_file = True
                            entry['unit_path'] = unit_path
            if dropin.exists():
                if dropin.is_symlink():
                    failures.append(f'refusing to follow unit drop-in symlink: {dropin}')
                elif not dropin.is_dir():
                    failures.append(f'unit drop-in is not a directory: {dropin}')
                else:
                    records, problems = walk_records(dropin, f'systemd/{index:03d}/{unit}.d')
                    failures.extend(problems)
                    entry['dropin'] = records
            if entry['unit_path'] is not None or entry['dropin']:
                copies.append(entry)
        if not found_file:
            looked = ', '.join(str(root) for root in request.systemd_roots) or '(none)'
            failures.append(f'unit file not found: {unit}; looked in {looked}')
        unit_plan.append({'unit': unit, 'copies': copies})

    etc_plan = []
    for path in request.etc_paths:
        try:
            present = path.exists()
        except PermissionError:
            # e.g. /etc/redis is 0750 redis:redis; keep checking the rest so the
            # operator sees every problem in one run.
            failures.append(f'/etc path cannot be inspected (permission denied; run with sudo): {path}')
            continue
        if not present:
            failures.append(f'/etc path does not exist: {path}')
            continue
        resolved = path.resolve()
        if resolved.is_dir():
            records, problems = walk_records(resolved, 'etc-dir')
            failures.extend(problems)
            etc_plan.append({'source': path, 'resolved': resolved, 'kind': 'dir', 'records': records})
        elif resolved.is_file():
            problem = readable_file(resolved)
            if problem:
                failures.append(problem)
            else:
                etc_plan.append({'source': path, 'resolved': resolved, 'kind': 'file', 'records': []})
        else:
            failures.append(f'/etc path is not a file or directory: {path}')

    required_resolved = {path.resolve() for path in request.git_checkouts if path.exists()}
    try:
        discovered, discovery_notes = discover_checkouts(request.discover_roots, required_resolved)
    except BackupError as exc:
        failures.append(str(exc))
        discovered, discovery_notes = [], []
    git_paths = list(request.git_checkouts) + discovered
    if not git_paths:
        failures.append('No git checkouts were named. Pass --git-checkout. /opt/sentient-core is required on nanob.')
    git_info = []
    named_cover = [path for path in request.etc_paths] + [path for path in request.sqlite_paths]
    for path in git_paths:
        info, problems = inspect_git(path)
        failures.extend(problems)
        if info is None:
            continue
        for relative in info['ignored']:
            if not looks_like_secret(Path(relative).name):
                continue
            candidate = info['resolved'] / relative
            if covered_by(candidate, named_cover):
                continue
            failures.append(
                'refusing to skip secret-looking ignored file '
                f'{candidate}. Pass it with --etc so it is encrypted into the archive.')
        for relative in info['untracked']:
            if relative.startswith('/') or '..' in PurePosixPath(relative).parts:
                failures.append(f'refusing untracked path {relative} in {path}')
        git_info.append(info)

    sources: list[Path] = []
    sources.extend(item['resolved'] for item in sqlite_info)
    if airi is not None:
        sources.append(Path(airi['resolved']))
    if runtime is not None:
        sources.append(Path(runtime['resolved']))
    sources.extend(systemd_existing)
    sources.extend(item['resolved'] for item in etc_plan)
    sources.extend(info['resolved'] for info in git_info)
    if dest.exists() and not dest.is_dir():
        failures.append(f'destination exists and is not a directory: {dest}')
    parent = dest if dest.is_dir() else dest.parent
    if not parent.exists():
        failures.append(f'destination parent does not exist: {parent}')
    else:
        for source in sources:
            if inside(dest, source):
                failures.append(f'destination is inside a source tree: {dest} is inside {source}')
                break
        try:
            free = shutil.disk_usage(parent).free
        except OSError as exc:
            failures.append(f'cannot check free space on {parent}: {exc.strerror or exc}')
        else:
            needed = HEADROOM_BYTES + payload_bytes(sqlite_info, airi, runtime, unit_plan, etc_plan)
            if free < needed:
                failures.append(
                    f'not enough free space on {parent}: {free} bytes free, need at least {needed}')

    plan = {
        'sqlite': [str(item['source']) for item in sqlite_info],
        'redis': {
            'host': request.redis_host,
            'port': request.redis_port,
            'snapshot': 'not_run',
            'ping': redis_ping,
        },
        'units': [item['unit'] for item in unit_plan],
        'etc': [str(item['source']) for item in etc_plan],
        'airi': None if airi is None else {
            'source': airi['source'], 'resolved': airi['resolved'], 'files': airi['files'],
        },
        'runtime': None if runtime is None else {
            'source': runtime['source'], 'resolved': runtime['resolved'], 'files': runtime['files'],
        },
        'git': [{
            'source': str(info['source']),
            'head': info['head'],
            'dirty': info['dirty'],
            'ignored_not_copied': len(info['ignored']),
        } for info in git_info],
        'discovery': discovery_notes,
    }
    details = {
        'sqlite': sqlite_info,
        'airi': airi,
        'runtime': runtime,
        'units': unit_plan,
        'etc': etc_plan,
        'git': git_info,
        'discovery': discovery_notes,
        'plan': plan,
    }
    return failures, details


def store_tree_record(payload: Path, record: dict) -> dict:
    relative = record['archive_path']
    destination = payload / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if record['type'] == 'symlink':
        os.symlink(record['linkname'], destination)
        return file_entry(destination, relative)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        with record['source'].open('rb') as source:
            shutil.copyfileobj(source, handle)
    os.chmod(destination, stat.S_IMODE(record['source'].stat().st_mode) & 0o777)
    return file_entry(destination, relative)


def file_entry(path: Path, relative: str) -> dict:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        linkname = os.readlink(path)
        data = os.fsencode(linkname)
        return {
            'path': relative,
            'sha256': sha256_bytes(data),
            'bytes': len(data),
            'mode': '0777',
            'type': 'symlink',
            'linkname': linkname,
        }
    if not stat.S_ISREG(info.st_mode):
        raise BackupError(f'refusing to archive special file {path}')
    return {
        'path': relative,
        'sha256': sha256_file(path),
        'bytes': info.st_size,
        'mode': f'{stat.S_IMODE(info.st_mode) & 0o777:04o}',
        'type': 'file',
        'linkname': None,
    }


def backup_sqlite(source: Path, destination: Path) -> None:
    uri = sqlite_uri(source.resolve())
    incoming = sqlite3.connect(uri, uri=True)
    try:
        incoming.execute('pragma query_only=ON')
        if destination.exists():
            destination.unlink()
        outgoing = sqlite3.connect(destination)
        try:
            incoming.backup(outgoing)
            # One file on disk. A WAL sidecar would be an unlisted archive member.
            outgoing.execute('pragma journal_mode=DELETE')
        finally:
            outgoing.close()
    except sqlite3.Error as exc:
        raise BackupError(f'SQLite online backup failed for {source}: {exc}') from exc
    finally:
        incoming.close()
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(destination) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    check = sqlite3.connect(sqlite_uri(destination), uri=True)
    try:
        row = check.execute('pragma integrity_check').fetchone()
    finally:
        check.close()
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(destination) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    if not row or row[0] != 'ok':
        raise BackupError(f'SQLite backup failed integrity_check: {source}')
    os.chmod(destination, 0o600)


def snapshot_redis(request: BackupRequest, destination: Path) -> None:
    env = redis_environment(request)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = run(redis_command(request) + ['--rdb', str(destination)],
                 env=env, timeout=request.redis_timeout)
    if result.returncode != 0 or not destination.is_file():
        detail = result.stderr.decode('utf-8', 'replace').strip()[:400]
        raise BackupError(f'Redis BGSAVE copy failed: {detail}')
    with destination.open('rb') as handle:
        header = handle.read(5)
    if header != b'REDIS':
        raise BackupError('Redis snapshot is not an RDB file; refusing to keep a bad copy')
    os.chmod(destination, 0o600)


def backup_git(info: dict, payload: Path, index: int) -> dict:
    root = payload / 'git' / f'{index:03d}'
    root.mkdir(parents=True, exist_ok=True)
    env = git_env()
    source = info['source']
    bundle = root / 'repo.bundle'
    require(GIT_PREFIX + ['-C', str(source), 'bundle', 'create', str(bundle), '--all'],
            env=env, timeout=300)
    diff = require(
        GIT_PREFIX + ['-C', str(source), 'diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD'],
        env=env, timeout=300)
    (root / 'dirty.diff').write_bytes(diff.stdout)
    (root / 'status.z').write_bytes(
        require(GIT_PREFIX + ['-C', str(source), 'status', '--porcelain=v1', '-uall', '-z'], env=env).stdout)
    (root / 'HEAD').write_text(info['head'] + '\n', encoding='utf-8')
    untracked_root = root / 'untracked'
    for relative in info['untracked']:
        origin = info['resolved'] / relative
        destination = untracked_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_symlink():
            os.symlink(os.readlink(origin), destination)
        elif origin.is_file():
            shutil.copyfile(origin, destination)
            os.chmod(destination, stat.S_IMODE(origin.stat().st_mode) & 0o777)
        else:
            raise BackupError(f'untracked path is not a file: {origin}')
    meta = {
        'source': str(source),
        'resolved': str(info['resolved']),
        'head': info['head'],
        'branch': info['branch'],
        'dirty': info['dirty'],
        'ignored_not_copied': len(info['ignored']),
        'untracked': info['untracked'],
        'bundle': f'git/{index:03d}/repo.bundle',
        'diff': f'git/{index:03d}/dirty.diff',
        'diff_empty': diff.stdout == b'',
    }
    (root / 'meta.json').write_text(json.dumps(meta, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            os.chmod(path, 0o600 if path.name != 'meta.json' else 0o600)
    return meta


def write_archive_tree(request: BackupRequest, details: dict, payload: Path) -> tuple[list[dict], dict]:
    entries: list[dict] = []
    components: dict = {
        'sqlite': [],
        'redis': {
            'source': f'redis://{request.redis_host}:{request.redis_port}',
            'archive_path': 'redis/dump.rdb',
            'method': 'redis-cli --rdb',
        },
        'systemd': [],
        'etc': [],
        'airi': None,
        'runtime': None,
        'git': [],
        'discovery': details['discovery'],
    }
    for index, item in enumerate(details['sqlite']):
        relative = f'sqlite/{index:03d}-{item["source"].name}'
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup_sqlite(item['resolved'], destination)
        entries.append(file_entry(destination, relative))
        components['sqlite'].append({
            'source': str(item['source']),
            'resolved': str(item['resolved']),
            'archive_path': relative,
        })
    redis_path = payload / 'redis' / 'dump.rdb'
    snapshot_redis(request, redis_path)
    entries.append(file_entry(redis_path, 'redis/dump.rdb'))
    for unit in details['units']:
        recorded_dropins = []
        archive_paths = []
        for copy in unit['copies']:
            if copy['unit_path'] is not None:
                relative = f'systemd/{copy["index"]:03d}/{unit["unit"]}'
                target = copy['unit_path']
                if target.is_symlink():
                    os.symlink(os.readlink(target), _mkdir_file(payload, relative))
                else:
                    _copy_file(target, payload / relative)
                entries.append(file_entry(payload / relative, relative))
                archive_paths.append(relative)
            for record in copy['dropin'] or []:
                entries.append(store_tree_record(payload, record))
                recorded_dropins.append(record['archive_path'])
        components['systemd'].append({
            'unit': unit['unit'],
            'archive_paths': archive_paths,
            'dropins': recorded_dropins,
        })
    for index, item in enumerate(details['etc']):
        if item['kind'] == 'file':
            relative = f'etc/{index:03d}-{item["source"].name}'
            _copy_file(item['resolved'], payload / relative)
            entries.append(file_entry(payload / relative, relative))
            components['etc'].append({'source': str(item['source']), 'archive_path': relative})
        else:
            prefix = f'etc/{index:03d}'
            for record in item['records']:
                rewritten = dict(record)
                suffix = record['archive_path'].split('/', 1)[1]
                rewritten['archive_path'] = f'{prefix}/{suffix}'
                entries.append(store_tree_record(payload, rewritten))
            components['etc'].append({'source': str(item['source']), 'archive_path': prefix})
    for label in ('airi', 'runtime'):
        info = details[label]
        if info is None:
            continue
        for record in info['records']:
            entries.append(store_tree_record(payload, record))
        components[label] = {
            'source': info['source'],
            'resolved': info['resolved'],
            'files': info['files'],
        }
    for index, info in enumerate(details['git']):
        meta = backup_git(info, payload, index)
        components['git'].append({
            'source': meta['source'],
            'resolved': meta['resolved'],
            'head': meta['head'],
            'branch': meta['branch'],
            'dirty': meta['dirty'],
            'ignored_not_copied': meta['ignored_not_copied'],
            'bundle': meta['bundle'],
            'diff': meta['diff'],
            'diff_empty': meta['diff_empty'],
        })
    git_entries = []
    git_root = payload / 'git'
    if git_root.exists():
        for path in sorted(git_root.rglob('*')):
            if path.is_symlink() or path.is_file():
                git_entries.append(file_entry(path, path.relative_to(payload).as_posix()))
    entries.extend(git_entries)
    entries.sort(key=lambda item: item['path'])
    return entries, components


def _mkdir_file(payload: Path, relative: str) -> Path:
    destination = payload / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, stat.S_IMODE(source.stat().st_mode) & 0o777 or 0o600)


def write_tar(payload: Path, destination: Path) -> None:
    with tarfile.open(destination, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        paths = [path for path in payload.rglob('*') if path.is_symlink() or path.is_file()]
        for path in sorted(paths, key=lambda item: item.relative_to(payload).as_posix()):
            relative = path.relative_to(payload).as_posix()
            info = tarfile.TarInfo(relative)
            info.mtime = int(path.lstat().st_mtime)
            if path.is_symlink():
                info.type = tarfile.SYMTYPE
                info.linkname = os.readlink(path)
                info.mode = 0o777
                archive.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.size = path.stat().st_size
                info.mode = stat.S_IMODE(path.stat().st_mode) & 0o777
                with path.open('rb') as handle:
                    archive.addfile(info, handle)


def age_encrypt(source: Path, destination: Path, recipients: tuple[str, ...], age_bin: str) -> None:
    command = [age_bin]
    for recipient in recipients:
        command.extend(['-r', recipient])
    command.extend(['-o', str(destination), str(source)])
    require(command, timeout=300)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise BackupError('age produced no ciphertext')
    with destination.open('rb') as handle:
        if not handle.read(len(b'age-encryption.org/v1\n')).startswith(b'age-encryption.org/v1\n'):
            raise BackupError('age output is not an age v1 file')
    os.chmod(destination, 0o600)


def result_shell(plan: dict, *, dry_run: bool, archive: str | None, digest: str | None,
                 size: int, manifest_files: int) -> dict:
    return {
        'ok': True,
        'dry_run': dry_run,
        'archive': archive,
        'sha256': digest,
        'bytes': size,
        'manifest_files': manifest_files,
        'components': plan,
    }


def backup(request: BackupRequest, dest_dir: Path, *, dry_run: bool = False) -> dict:
    failures, details = scan(request, dest_dir)
    raise_failures(failures)
    if dry_run:
        return result_shell(details['plan'], dry_run=True, archive=None, digest=None,
                            size=0, manifest_files=0)
    created_dest = False
    if not dest_dir.exists():
        dest_dir.mkdir(mode=0o700)
        created_dest = True
    os.chmod(dest_dir, 0o700)
    stamp = utc_stamp()
    partial = dest_dir / f'.orin-backup-{stamp}.partial'
    archive_path: Path | None = None
    try:
        partial.mkdir(mode=0o700)
        payload = partial / 'payload'
        payload.mkdir(mode=0o700)
        entries, components = write_archive_tree(request, details, payload)
        manifest = {
            'schema': SCHEMA,
            'created_utc': utc_iso(),
            'hostname': socket.gethostname(),
            'tool': 'orin_backup',
            'symlink_policy': ('named roots are resolved once; links inside a tree are stored '
                               'and not followed'),
            'ignored_policy': ('gitignored files are counted and not copied; secret-looking '
                               'ignored files fail the run unless named with --etc or --sqlite'),
            'components': components,
            'files': entries,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()
        manifest_path = payload / 'manifest.json'
        manifest_path.write_bytes(manifest_bytes)
        os.chmod(manifest_path, 0o600)
        sums_lines = [f'{item["sha256"]}  {item["path"]}' for item in entries]
        sums_lines.append(f'{sha256_bytes(manifest_bytes)}  manifest.json')
        sums_text = '\n'.join(sorted(sums_lines)) + '\n'
        sums_path = payload / 'SHA256SUMS'
        sums_path.write_text(sums_text, encoding='utf-8')
        os.chmod(sums_path, 0o600)
        tar_path = partial / 'backup.tar.gz'
        write_tar(payload, tar_path)
        final_name = f'orin-backup-{stamp}.tar.age'
        archive_path = dest_dir / final_name
        if archive_path.exists():
            raise BackupError(f'refusing to overwrite {archive_path}')
        age_encrypt(tar_path, archive_path, request.age_recipients, request.age_bin)
        digest = sha256_file(archive_path)
        sidecar = dest_dir / f'{final_name}.sha256'
        sidecar.write_text(f'{digest}  {final_name}\n', encoding='utf-8')
        os.chmod(sidecar, 0o600)
        components_public = details['plan']
        components_public['redis'] = dict(components_public['redis'], snapshot='redis-cli --rdb')
        return result_shell(components_public, dry_run=False, archive=str(archive_path),
                            digest=digest, size=archive_path.stat().st_size,
                            manifest_files=len(entries))
    except Exception:
        if archive_path is not None and archive_path.exists():
            archive_path.unlink()
        sidecar_path = dest_dir / f'{archive_path.name}.sha256' if archive_path is not None else None
        if sidecar_path is not None and sidecar_path.exists():
            sidecar_path.unlink()
        if created_dest:
            shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)


def safe_member_destination(dest: Path, name: str) -> Path:
    if name.startswith('/') or '\\' in name or '\n' in name or '\0' in name:
        raise BackupError(f'refusing archive member that escapes the scratch directory: {name}')
    parts = PurePosixPath(name).parts
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise BackupError(f'refusing archive member that escapes the scratch directory: {name}')
    current = dest
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise BackupError(f'refusing to follow a symlink while extracting {name}')
        if current.exists() and not current.is_dir():
            raise BackupError(f'refusing to extract through a non-directory: {name}')
        current.mkdir(mode=0o700, exist_ok=True)
    final = current / parts[-1]
    if final.is_symlink() or final.exists():
        raise BackupError(f'refusing to overwrite extracted path {name}')
    return final


def extract_archive(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            safe_member_destination(dest, member.name)
        for member in members:
            final = safe_member_destination(dest, member.name)
            if member.isdir():
                final.mkdir(mode=0o700)
                continue
            if member.issym():
                os.symlink(member.linkname, final)
                continue
            if member.islnk() or not member.isreg():
                raise BackupError(f'refusing archive member type for {member.name}')
            source = tar.extractfile(member)
            if source is None:
                raise BackupError(f'archive member has no content: {member.name}')
            fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                shutil.copyfileobj(source, handle)
            os.chmod(final, member.mode & 0o777)


def load_sums(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line:
            continue
        if '  ' not in line:
            raise BackupError(f'SHA256SUMS line is malformed: {line!r}')
        digest, name = line.split('  ', 1)
        if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise BackupError(f'SHA256SUMS digest is malformed for {name}')
        if name in found:
            raise BackupError(f'SHA256SUMS lists {name} more than once')
        found[name] = digest
    return found


def iter_restored(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                found.append(path)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            found.append(current / name)
    return found


def verify(archive: Path, scratch_dir: Path, identity: Path, *, age_bin: str = 'age') -> dict:
    if scratch_dir.exists():
        raise BackupError(f'scratch directory already exists: {scratch_dir}')
    if not archive.is_file():
        raise BackupError(f'archive does not exist: {archive}')
    if not identity.is_file():
        raise BackupError(f'age identity does not exist: {identity}')
    if not command_available(age_bin):
        raise BackupError(f'age is required but was not found: {age_bin}')
    sidecar = Path(str(archive) + '.sha256')
    ciphertext_checked = False
    if sidecar.exists():
        recorded = sidecar.read_text(encoding='utf-8').split()
        if not recorded or recorded[0] != sha256_file(archive):
            raise BackupError('archive sha256 sidecar does not match the archive bytes')
        ciphertext_checked = True
    scratch_dir.mkdir(mode=0o700)
    try:
        work = scratch_dir / 'work'
        work.mkdir(mode=0o700)
        plain = work / 'backup.tar.gz'
        require([age_bin, '-d', '-i', str(identity), '-o', str(plain), str(archive)], timeout=300)
        os.chmod(plain, 0o600)
        restored = scratch_dir / 'restored'
        restored.mkdir(mode=0o700)
        extract_archive(plain, restored)
        sums_path = restored / 'SHA256SUMS'
        manifest_path = restored / 'manifest.json'
        if not sums_path.is_file():
            raise BackupError('archive has no SHA256SUMS manifest')
        if not manifest_path.is_file():
            raise BackupError('archive has no manifest.json')
        expected = load_sums(sums_path)
        actual_paths = []
        for path in iter_restored(restored):
            relative = path.relative_to(restored).as_posix()
            if relative == 'SHA256SUMS':
                continue
            actual_paths.append(relative)
            if path.is_symlink():
                digest = sha256_bytes(os.fsencode(os.readlink(path)))
            elif path.is_file():
                digest = sha256_file(path)
            else:
                raise BackupError(f'restored path is not a file or symlink: {relative}')
            if relative not in expected:
                raise BackupError(f'restore does not match the sha256 manifest: unexpected {relative}')
            if expected[relative] != digest:
                raise BackupError(f'restore does not match the sha256 manifest: {relative}')
        missing = sorted(set(expected) - set(actual_paths))
        if missing:
            raise BackupError('restore does not match the sha256 manifest: ' + ', '.join(missing))
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('schema') != SCHEMA:
            raise BackupError('manifest schema is not supported')
        for item in manifest.get('files', []):
            if expected.get(item['path']) != item['sha256']:
                raise BackupError(f'restore does not match the sha256 manifest: {item["path"]}')
            restored_path = restored / item['path']
            if item['type'] == 'file':
                actual_mode = f'{stat.S_IMODE(restored_path.stat().st_mode) & 0o777:04o}'
                if actual_mode != item['mode']:
                    raise BackupError(
                        f'restore does not match the sha256 manifest: {item["path"]} mode {actual_mode}')
        sqlite_integrity = []
        for item in manifest['components']['sqlite']:
            database = restored / item['archive_path']
            connection = sqlite3.connect(sqlite_uri(database), uri=True)
            try:
                row = connection.execute('pragma integrity_check').fetchone()
            finally:
                connection.close()
            if not row or row[0] != 'ok':
                raise BackupError(f'restored SQLite database failed integrity_check: {item["archive_path"]}')
            sqlite_integrity.append('ok')
        redis_path = restored / manifest['components']['redis']['archive_path']
        with redis_path.open('rb') as handle:
            redis_ok = handle.read(5) == b'REDIS'
        if not redis_ok:
            raise BackupError('restored Redis snapshot is not an RDB file')
        git_reports = []
        template = work / 'git-template'
        template.mkdir()
        env = git_env()
        for index, item in enumerate(manifest['components']['git']):
            clone = work / f'git-{index}'
            require(['git', '-c', 'safe.directory=*', 'clone', '--template', str(template),
                     str(restored / item['bundle']), str(clone)], env=env, timeout=300)
            head = require(GIT_PREFIX + ['-C', str(clone), 'rev-parse', 'HEAD'], env=env).stdout.decode().strip()
            if head != item['head']:
                raise BackupError(f'restored git HEAD does not match the manifest for {item["source"]}')
            diff_path = restored / item['diff']
            diff_bytes = diff_path.read_bytes()
            if (diff_bytes == b'') != bool(item['diff_empty']):
                raise BackupError(f'git diff emptiness does not match the manifest for {item["source"]}')
            if diff_bytes:
                applied = run(GIT_PREFIX + ['-C', str(clone), 'apply', '--check', str(diff_path)], env=env)
                if applied.returncode != 0:
                    detail = applied.stderr.decode('utf-8', 'replace').strip()[:400]
                    raise BackupError(f'git diff does not apply for {item["source"]}: {detail}')
            git_reports.append({'source': item['source'], 'head': head, 'diff_applies': True})
        return {
            'ok': True,
            'archive': str(archive),
            'scratch': str(scratch_dir),
            'files_checked': len(expected),
            'mismatches': [],
            'sqlite_integrity': sqlite_integrity,
            'redis_rdb': True,
            'git': git_reports,
            'ciphertext_sha256_checked': ciphertext_checked,
        }
    except Exception:
        raise


def load_spec(path: Path) -> BackupRequest:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f'cannot read spec {path}: {exc}') from exc
    if not isinstance(data, dict):
        raise BackupError('spec must be a JSON object')
    unknown = sorted(set(data) - SPEC_KEYS)
    if unknown:
        raise BackupError('spec has unknown keys: ' + ', '.join(unknown))
    missing = sorted(key for key in REQUIRED_SPEC_KEYS if key not in data)
    if missing:
        raise BackupError('spec is missing keys: ' + ', '.join(missing))
    if data['schema'] != SPEC_SCHEMA:
        raise BackupError(f'spec schema {data["schema"]!r} is not supported')

    def as_paths(key: str) -> tuple[Path, ...]:
        value = data[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BackupError(f'spec key {key} must be a list of paths')
        return tuple(Path(item) for item in value)

    password = data['redis_password_file']
    if password is not None and not isinstance(password, str):
        raise BackupError('spec redis_password_file must be a path or null')
    if not isinstance(data['redis_host'], str) or not isinstance(data['redis_port'], int):
        raise BackupError('spec redis_host and redis_port are invalid')
    recipients = data['age_recipients']
    if not isinstance(recipients, list) or not all(isinstance(item, str) for item in recipients):
        raise BackupError('spec age_recipients must be a list of strings')
    return BackupRequest(
        sqlite_paths=as_paths('sqlite'),
        airi_path=Path(data['airi_path']),
        runtime_path=Path(data['runtime_path']),
        git_checkouts=as_paths('git_checkouts'),
        systemd_roots=as_paths('systemd_roots'),
        units=tuple(data['units']) if isinstance(data['units'], list) else (),
        etc_paths=as_paths('etc_paths'),
        redis_host=data['redis_host'],
        redis_port=data['redis_port'],
        redis_password_file=None if password is None else Path(password),
        redis_cli=data.get('redis_cli') or 'redis-cli',
        discover_roots=as_paths('discover_roots'),
        age_recipients=tuple(recipients),
        age_bin=data.get('age_bin') or 'age',
    )


def request_from_args(args: argparse.Namespace) -> BackupRequest:
    if args.spec and args.profile:
        raise BackupError('--spec and --profile cannot be used together')
    if args.discover_root and args.no_discover:
        raise BackupError('--discover-root and --no-discover cannot be used together')
    if args.spec:
        request = load_spec(Path(args.spec))
    elif args.profile == 'nanob':
        request = nanob_request()
    else:
        request = BackupRequest(
            sqlite_paths=(), airi_path=None, runtime_path=None, git_checkouts=(),
            systemd_roots=(), units=(), etc_paths=(), redis_host='127.0.0.1',
            redis_port=6379, redis_password_file=None, redis_cli='redis-cli',
            discover_roots=NANOB_DISCOVERY, age_recipients=(), age_bin='age',
        )
    changes: dict = {}
    if args.sqlite is not None:
        changes['sqlite_paths'] = tuple(Path(item) for item in args.sqlite)
    if args.airi_path is not None:
        changes['airi_path'] = Path(args.airi_path)
    if args.runtime_path is not None:
        changes['runtime_path'] = Path(args.runtime_path)
    if args.git_checkout is not None:
        changes['git_checkouts'] = tuple(Path(item) for item in args.git_checkout)
    if args.systemd_root is not None:
        changes['systemd_roots'] = tuple(Path(item) for item in args.systemd_root)
    if args.unit is not None:
        changes['units'] = tuple(args.unit)
    if args.etc is not None:
        changes['etc_paths'] = tuple(Path(item) for item in args.etc)
    if args.redis_host is not None:
        changes['redis_host'] = args.redis_host
    if args.redis_port is not None:
        changes['redis_port'] = args.redis_port
    if args.redis_password_file is not None:
        changes['redis_password_file'] = Path(args.redis_password_file)
    if args.redis_cli is not None:
        changes['redis_cli'] = args.redis_cli
    if args.no_discover:
        changes['discover_roots'] = ()
    elif args.discover_root is not None:
        changes['discover_roots'] = tuple(Path(item) for item in args.discover_root)
    if args.age_recipient is not None:
        changes['age_recipients'] = tuple(args.age_recipient)
    if args.age_bin is not None:
        changes['age_bin'] = args.age_bin
    return dataclasses.replace(request, **changes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='orin_backup',
        description='Create or verify an age-encrypted Orin backup with a sha256 manifest.',
    )
    commands = parser.add_subparsers(dest='command', required=True)
    backup_parser = commands.add_parser('backup', help='Write an encrypted archive under --dest')
    backup_parser.add_argument('--dest', required=True, help='Directory that receives the archive')
    backup_parser.add_argument('--profile', choices=('nanob',), help='Strict nanob path set')
    backup_parser.add_argument('--spec', help='JSON spec. Unknown keys are refused.')
    backup_parser.add_argument('--sqlite', action='append', help='SQLite database to back up online')
    backup_parser.add_argument('--airi-path', help='AIRI settings and assets directory from Gate A')
    backup_parser.add_argument('--runtime-path', help='Runtime tree, default /mnt/orin-ai/orin on nanob')
    backup_parser.add_argument('--git-checkout', action='append', help='Git checkout top, including /opt/sentient-core')
    backup_parser.add_argument('--systemd-root', action='append', help='Directory that holds unit files')
    backup_parser.add_argument('--unit', action='append', help='Required unit name')
    backup_parser.add_argument('--etc', action='append', help='File or directory under /etc, or a secret file')
    backup_parser.add_argument('--redis-host', help='Loopback Redis host')
    backup_parser.add_argument('--redis-port', type=int)
    backup_parser.add_argument('--redis-password-file', help='File whose contents are REDISCLI_AUTH')
    backup_parser.add_argument('--redis-cli', help='Path to redis-cli')
    backup_parser.add_argument('--discover-root', action='append', help='Also bundle git repos found here')
    backup_parser.add_argument('--no-discover', action='store_true', help='Do not scan discovery roots')
    backup_parser.add_argument('--age-recipient', action='append', help='age1... public key')
    backup_parser.add_argument('--age-bin', help='Path to the age binary')
    backup_parser.add_argument('--dry-run', action='store_true', help='Check components and write nothing')
    verify_parser = commands.add_parser('verify', help='Restore into a new scratch directory and compare hashes')
    verify_parser.add_argument('--archive', required=True)
    verify_parser.add_argument('--scratch', required=True, help='Must not already exist')
    verify_parser.add_argument('--identity', required=True, help='age identity file, kept off the archive')
    verify_parser.add_argument('--age-bin', default='age')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'backup':
            request = request_from_args(args)
            result = backup(request, Path(args.dest), dry_run=args.dry_run)
        else:
            result = verify(Path(args.archive), Path(args.scratch), Path(args.identity),
                            age_bin=args.age_bin)
    except BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        # Permission or disk errors outside the named checks (a root-only /etc
        # path when not run with sudo, a full disk). backup() has already removed
        # anything it wrote; say what failed instead of printing a traceback.
        where = f'{exc.filename}: ' if exc.filename else ''
        hint = ' (run with sudo)' if isinstance(exc, PermissionError) else ''
        print(f'{where}{exc.strerror or exc}{hint}', file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
