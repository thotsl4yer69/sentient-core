#!/usr/bin/env python3
"""AIRI-first Orin service cutover: evidence, one unit at a time, rollback.

No installation, deletion, model download, config rewrite, or automatic discovery
is interpreted as permission to mutate the host. Python 3.10+, stdlib only.

WS1 backup manifest contract
----------------------------
``tools/orin_backup.py`` (WS1, written separately) must leave one manifest per
unit before ``apply`` will stop that unit. This module does not import WS1.
Stable names WS1 may import or mirror::

    BACKUP_MANIFEST_SCHEMA   integer 1
    BACKUP_MANIFEST_TOOL     "orin_backup"
    DEFAULT_BACKUP_ROOT      /var/lib/orin-airi-recovery/backups
    backup_manifest_path(unit, root=None)
    load_backup_manifest(path, unit=..., machine=...)

Default path: ``/var/lib/orin-airi-recovery/backups/<exact-unit>/manifest.json``.
``apply --backup-manifest PATH`` overrides it. The file must be a regular
non-symlink, not world-writable, and at most 262144 bytes. JSON object with
at least these keys (any other keys are ignored):

    schema            1
    tool              "orin_backup"
    unit              exact systemd unit, equal to the plan unit
    machine_tag       orin_reconcile.machine_tag() for this host
    created_utc       timezone-aware ISO-8601 timestamp
    restore_verified  true only after a scratch restore matched hashes
    sha256            64 lowercase hex characters of the backup payload

``prepare`` records the path and does not require the file, so the backup can
be taken after the consumer map is reviewed. ``apply`` reads it and blocks
before any ``systemctl`` change when the contract is not met.

Reviewed allowlist
------------------
The four legacy UI units are the built-in exact entries. Any other candidate
needs a reviewed JSON file passed to prepare, apply, accept, rollback and
status via ``--allowlist``::

    {"schema": 1,
     "entries": [{"unit": "name.service", "review_id": "gate-e-id", "reason": "..."}]}

Unit names are exact. Globs and protected units (ssh, docker, mosquitto, the
model server, and the other names in ``PROTECTED_UNITS``) are rejected. The
entry is copied into the plan and must still match the live allowlist when the
plan is loaded.

Consumer map
------------
``prepare`` records, for that one unit only:

* systemd reverse dependencies (``systemctl show`` properties and
  ``list-dependencies --reverse``)
* listening TCP/UDP sockets owned by the unit cgroup or ``MainPID``, and the
  clients connected to those sockets
* an anonymous MQTT 3.1.1 subscription to ``$SYS/#`` on ``127.0.0.1:1883``
* subscription rows parsed from the mosquitto v5/v6 persistence file
  (message payloads are skipped)
* TCP/UDP sessions from the unit to ports 1883 and 8883

The map is evidence for the operator. It does not itself stop a unit. Existing
stop-propagation, bind, consists-of and trigger blocks are unchanged, as are
one-unit apply, automatic rollback and manual accept.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = 3
DEFAULT_STATE_DIR = Path('/var/lib/orin-airi-recovery')
BACKUP_LOCATIONS = (
    DEFAULT_STATE_DIR,
    Path('/mnt/orin-ai/backups'),
)
SSH_HOST_PUBLIC_KEYS = (
    Path('/etc/ssh/ssh_host_ed25519_key.pub'),
    Path('/etc/ssh/ssh_host_ecdsa_key.pub'),
    Path('/etc/ssh/ssh_host_rsa_key.pub'),
)
LEGACY_UI_UNITS = frozenset({
    'sentient-web-chat.service',
    'cortana-web.service',
    'cortana-dashboard.service',
    'open-webui.service',
})
PROTECTED_UNITS = frozenset({
    'ssh.service', 'sshd.service', 'NetworkManager.service',
    'systemd-networkd.service', 'docker.service', 'containerd.service',
    'ollama.service', 'mosquitto.service', 'redis-server.service',
})
SUSPECT_TERMS = ('airi', 'sentient', 'cortana', 'avatar', 'open-webui',
                 'ollama', 'whisper', 'piper', 'starnet', 'llama', 'voice')
BACKUP_MANIFEST_SCHEMA = 1
BACKUP_MANIFEST_TOOL = 'orin_backup'
BACKUP_MANIFEST_MAX_BYTES = 262144
DEFAULT_BACKUP_ROOT = DEFAULT_STATE_DIR / 'backups'
ALLOWLIST_SCHEMA = 1
ALLOWLIST_MAX_BYTES = 65536
DEFAULT_MOSQUITTO_DB = Path('/var/lib/mosquitto/mosquitto.db')
MOSQUITTO_DB_MAX_BYTES = 32 * 1024 * 1024
MOSQUITTO_DB_MAGIC = bytes((0x00, 0xB5, 0x00)) + b'mosquitto db'
MOSQUITTO_SUB_CHUNK = 5
MQTT_OBSERVE_HOST = '127.0.0.1'
MQTT_OBSERVE_PORT = 1883
MQTT_OBSERVE_TIMEOUT = 12.0
MQTT_PORTS = frozenset({1883, 8883})
UNIT_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9:_.@-]{0,200}\.service\Z')
REVIEW_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}\Z')
CLOSURE_UNIT_RE = re.compile(
    r'^[A-Za-z0-9][A-Za-z0-9:@._\\-]*\.(service|socket|target|path|timer|mount|slice|scope)\Z')
SS_PROCESS_RE = re.compile(r'"([^"]*)",pid=(\d+),fd=(\d+)')
REVERSE_DEPENDENCY_KEYS = (
    'RequiredBy', 'WantedBy', 'RequisiteOf', 'BoundBy', 'UpheldBy',
    'TriggeredBy', 'StopPropagatedFrom', 'ReloadPropagatedFrom',
)
FORWARD_DEPENDENCY_KEYS = (
    'Requires', 'Requisite', 'Wants', 'BindsTo', 'PartOf', 'Upholds',
    'ConsistsOf', 'PropagatesStopTo', 'PropagatesReloadTo',
)
SS_COMMANDS = (
    ('tcp', 'listen', ['ss', '-H', '-lntp']),
    ('udp', 'listen', ['ss', '-H', '-lnup']),
    ('tcp', 'established', ['ss', '-H', '-ntp']),
    ('udp', 'established', ['ss', '-H', '-nup']),
)
SHOW_UNIT_PROPERTIES = (
    'LoadState', 'ActiveState', 'UnitFileState', 'FragmentPath', 'Names',
    'MainPID', 'ControlGroup', 'User', 'WorkingDirectory',
) + REVERSE_DEPENDENCY_KEYS + FORWARD_DEPENDENCY_KEYS
BUILTIN_REVIEWED_ALLOWLIST = {
    'sentient-web-chat.service': {
        'unit': 'sentient-web-chat.service',
        'review_id': 'runbook-section-4',
        'reason': 'Legacy operator web chat; AIRI is the only operator-facing AI interface',
    },
    'cortana-web.service': {
        'unit': 'cortana-web.service',
        'review_id': 'runbook-section-4',
        'reason': 'Legacy Cortana web UI superseded by AIRI',
    },
    'cortana-dashboard.service': {
        'unit': 'cortana-dashboard.service',
        'review_id': 'runbook-section-4',
        'reason': 'Legacy Cortana dashboard superseded by AIRI',
    },
    'open-webui.service': {
        'unit': 'open-webui.service',
        'review_id': 'runbook-section-4',
        'reason': 'Legacy Open WebUI systemd unit; a Docker container still needs its own reviewed change',
    },
}

class SafetyError(RuntimeError):
    pass

class Runner:
    def run(self, args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(args, check=False, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SafetyError(f'{args[0]} failed or timed out: {exc}') from exc

    def require(self, args: list[str], timeout: int = 15) -> str:
        res = self.run(args, timeout)
        if res.returncode != 0:
            raise SafetyError(f'{" ".join(args[:3])}: exit {res.returncode}: '
                              f'{res.stderr.strip()[:240]}')
        return res.stdout.strip()

RUN = Runner()

def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def root_required() -> None:
    if os.geteuid() != 0:
        raise SafetyError('This action requires root; inventory does not.')

def machine_tag() -> str:
    try:
        machine_id = Path('/etc/machine-id').read_text().strip()
    except OSError as exc:
        raise SafetyError('Cannot read machine identity; refusing mutation') from exc
    if not re.fullmatch(r'[a-f0-9]{32}', machine_id):
        raise SafetyError('Unexpected machine-id; refusing mutation')
    return hashlib.sha256(machine_id.encode()).hexdigest()[:20]

def jetson_required() -> None:
    p = Path('/etc/nv_tegra_release')
    if not p.is_file() or platform.machine() not in ('aarch64', 'arm64'):
        raise SafetyError('Target does not look like an ARM64 NVIDIA Jetson; refusing mutation')

def validate_private_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise SafetyError('AIRI URL must be an http(s) URL without credentials')
    if p.path not in ('', '/') or p.query or p.fragment:
        raise SafetyError('Supply only the AIRI site origin, e.g. http://127.0.0.1:3000/')
    host = p.hostname
    if host.lower() not in ('localhost',) and not host.lower().endswith('.local'):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError as exc:
            raise SafetyError('AIRI URL must use a loopback/private IP or .local hostname') from exc
        if not (ip.is_private or ip.is_loopback):
            raise SafetyError('Public IP is not accepted for AIRI health check')
    if not p.port or not (1 <= p.port <= 65535):
        raise SafetyError('Specify an explicit AIRI port for reliable health checks')
    normalized_host = f'[{host}]' if ':' in host else host
    return f'{p.scheme}://{normalized_host}:{p.port}/'

def probe_airi(url: str, *, timeout: float = 3) -> dict:
    """HTTP-only reachability. This is explicitly NOT a real chat acceptance test."""
    result = {'url': url, 'checked_utc': utc_now(), 'http_ok': False}
    try:
        # Do not follow login/external redirects. A 302 page is not a healthy AIRI stage.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(url, headers={'User-Agent':'orin-airi-reconcile/1'})
        with opener.open(req, timeout=timeout) as res:
            result['status'] = res.status
            result['http_ok'] = 200 <= res.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        result['error'] = str(exc)[:200]
    return result

def show_unit(unit: str) -> dict:
    args = ['systemctl', 'show', unit, '--no-pager']
    for prop in SHOW_UNIT_PROPERTIES:
        args.extend(('-p', prop))
    raw = RUN.require(args)
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)

def units_inventory() -> list[dict]:
    listed = RUN.run(['systemctl', 'list-unit-files', '--type=service',
                      '--all', '--no-legend', '--no-pager', '--plain'])
    if listed.returncode != 0:
        return []
    names = []
    for line in listed.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].endswith('.service') and any(t in parts[0].lower() for t in SUSPECT_TERMS):
            names.append(parts[0])
    out = []
    for name in sorted(set(names)):
        try:
            item = {'name': name, **show_unit(name)}
            item['candidate_legacy_ui'] = name in LEGACY_UI_UNITS
            out.append(item)
        except SafetyError as exc:
            out.append({'name':name,'error':str(exc)})
    return out

def unit_definition_fingerprint(unit: str) -> str:
    """Reject multi-unit install effects; record hash, never plaintext config."""
    source = RUN.require(['systemctl','cat',unit],timeout=15)
    if not source.strip():
        raise SafetyError('Cannot inspect systemd unit definition; refusing cutover')
    in_install = False
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(('#',';')):
            continue
        if re.fullmatch(r'\[[^]]+\]',line):
            in_install = line.lower() == '[install]'
            continue
        if in_install and re.match(r'^(Also|Alias)\s*=\s*\S+',line,re.IGNORECASE):
            raise SafetyError('Legacy unit declares Also=/Alias=; disabling could affect other units')
    return hashlib.sha256(source.encode()).hexdigest()

def read_command(args: list[str], timeout: int = 15) -> dict:
    try:
        r = RUN.run(args, timeout)
        return {'exit_code':r.returncode,'stdout':r.stdout[:30000],
                'stderr':r.stderr[:500]}
    except SafetyError as exc:
        return {'error':str(exc)}

def frontend_candidates(units: list[dict], containers: dict) -> list[dict]:
    """Name-based leads only. Never infer that a candidate is safe to stop."""
    found = []
    for item in units:
        name = item.get('name','')
        low = name.lower()
        if ('airi' in low or name in LEGACY_UI_UNITS
                or any(k in low for k in ('web-chat','dashboard','open-webui'))):
            found.append({'source':'systemd','name':name,
                          'role_hint':'AIRI' if 'airi' in low else 'legacy_or_other_ui',
                          'active_state':item.get('ActiveState','unknown'),
                          'removal_approved':False})
    for raw in containers.get('stdout','').splitlines():
        parts=raw.split('|',3)
        if len(parts)!=4:
            continue
        name,image,status,ports=(x.strip() for x in parts)
        low=(name+' '+image).lower()
        if any(k in low for k in ('airi','open-webui','cortana','sentient-web','dashboard','portainer','grafana')):
            found.append({'source':'docker','name':name,'image':image,
                          'status':status,'ports':ports,
                          'role_hint':'AIRI' if 'airi' in low else 'legacy_or_other_ui',
                          'removal_approved':False})
    return found

def voice_runtime_paths(units: list[dict]) -> list[Path]:
    """Known voice entrypoints plus candidates beneath observed service workdirs."""
    paths = {
        Path('/mnt/orin-ai/orin/voice/handsfree.py'),
        Path('/mnt/orin-ai/orin/voice/audio_devices.py'),
        Path('/mnt/orin-ai/orin/dashboard/static/js/handsfree-controls.js'),
    }
    relative = (
        Path('sentient/services/voice/wake_word.py'),
        Path('sentient/services/voice/pipeline.py'),
        Path('voice/handsfree.py'),
        Path('voice/audio_devices.py'),
    )
    for unit in units:
        working = unit.get('WorkingDirectory', '')
        if working.startswith('/') and len(working) < 4096:
            base = Path(working)
            paths.update(base / item for item in relative)
    return sorted(paths, key=str)

def fingerprint_files(paths: list[Path], max_bytes: int = 5 * 1024 * 1024) -> list[dict]:
    """Hash small regular files without reading symlink targets or file contents out."""
    observed = []
    for path in paths:
        item = {'path': str(path), 'exists': False}
        try:
            metadata = path.lstat()
            item.update(exists=True, symlink=path.is_symlink(), size=metadata.st_size,
                        modified_ns=metadata.st_mtime_ns)
            if not item['symlink'] and path.is_file() and metadata.st_size <= max_bytes:
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                item['sha256'] = digest.hexdigest()
            elif not item['symlink'] and path.is_file():
                item['hash_skipped'] = f'larger_than_{max_bytes}_bytes'
        except FileNotFoundError:
            pass
        except OSError as exc:
            item['error'] = f'{type(exc).__name__}: {exc}'[:240]
        observed.append(item)
    return observed

def summarize_backup_locations(paths: tuple[Path, ...] = BACKUP_LOCATIONS) -> list[dict]:
    """Report shallow backup metadata without exposing filenames or following symlinks."""
    observed = []
    for path in paths:
        item = {'path': str(path), 'exists': False}
        try:
            metadata = path.lstat()
            item.update(exists=True, symlink=stat.S_ISLNK(metadata.st_mode),
                        modified_ns=metadata.st_mtime_ns)
            if item['symlink']:
                observed.append(item)
                continue
            if not stat.S_ISDIR(metadata.st_mode):
                item['directory'] = False
                observed.append(item)
                continue
            counts = {'regular_files': 0, 'directories': 0, 'symlinks': 0,
                      'other': 0}
            direct_file_bytes = 0
            newest_modified_ns = metadata.st_mtime_ns
            for child in path.iterdir():
                child_metadata = child.lstat()
                newest_modified_ns = max(newest_modified_ns,
                                         child_metadata.st_mtime_ns)
                mode = child_metadata.st_mode
                if stat.S_ISLNK(mode):
                    counts['symlinks'] += 1
                elif stat.S_ISREG(mode):
                    counts['regular_files'] += 1
                    direct_file_bytes += child_metadata.st_size
                elif stat.S_ISDIR(mode):
                    counts['directories'] += 1
                else:
                    counts['other'] += 1
            item.update(directory=True, direct_children=sum(counts.values()),
                        direct_child_types=counts,
                        direct_regular_file_bytes=direct_file_bytes,
                        newest_modified_ns=newest_modified_ns)
        except FileNotFoundError:
            pass
        except OSError as exc:
            item['error'] = f'{type(exc).__name__}: {exc}'[:240]
        observed.append(item)
    return observed

def read_device_model(path: Path = Path('/proc/device-tree/model')) -> dict:
    """Read the bounded device-tree model marker without following a symlink."""
    item = {'path': str(path), 'exists': False}
    try:
        metadata = path.lstat()
        item.update(exists=True, symlink=stat.S_ISLNK(metadata.st_mode))
        if item['symlink']:
            return item
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            item['error'] = 'model marker is not a small regular file'
            return item
        raw = path.read_bytes()
        if len(raw) > 4096:
            item['error'] = 'model marker exceeds 4096 bytes'
            return item
        item['value'] = raw.rstrip(b'\x00\r\n').decode('utf-8')
    except FileNotFoundError:
        pass
    except (OSError, UnicodeDecodeError) as exc:
        item['error'] = f'{type(exc).__name__}: {exc}'[:240]
    return item

def ssh_host_key_fingerprints(paths: tuple[Path, ...] = SSH_HOST_PUBLIC_KEYS) -> list[dict]:
    """Fingerprint fixed public host-key files without comments or private-key access."""
    observed = []
    for path in paths:
        item = {'path': str(path), 'exists': False}
        try:
            metadata = path.lstat()
            item.update(exists=True, symlink=stat.S_ISLNK(metadata.st_mode))
            if item['symlink']:
                observed.append(item)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16384:
                item['error'] = 'public key is not a small regular file'
                observed.append(item)
                continue
            fields = path.read_text(encoding='ascii').split()
            if len(fields) < 2:
                raise ValueError('malformed OpenSSH public key')
            public_key = base64.b64decode(fields[1], validate=True)
            digest = hashlib.sha256(public_key).digest()
            item.update(key_type=fields[0],
                        sha256='SHA256:' + base64.b64encode(digest).decode().rstrip('='))
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            item['error'] = f'{type(exc).__name__}: {exc}'[:240]
        observed.append(item)
    return observed

def _attach_consumer_maps(units: list[dict]) -> None:
    """Best-effort per-unit maps. Inventory stays read-only when observation fails."""
    try:
        runtime = observe_runtime(strict=False)
    except SafetyError as exc:
        runtime = {'listen': [], 'established': [], 'mqtt': {
            'endpoint': f'{MQTT_OBSERVE_HOST}:{MQTT_OBSERVE_PORT}',
            'reachable': False, 'sys': {}, 'subscriptions': [],
            'persistence': 'error', 'persistence_path': str(DEFAULT_MOSQUITTO_DB),
            'unit_connections': [], 'error': str(exc),
        }, 'errors': [str(exc)]}
    for item in units:
        if 'error' in item or not item.get('name'):
            continue
        try:
            item['consumers'] = map_unit_consumers(item['name'], item, runtime, strict=False)
        except SafetyError as exc:
            item['consumers'] = {'unit': item['name'], 'error': str(exc)}


def inventory() -> dict:
    """Metadata only: no environment, docker inspect, process arguments or journals."""
    units=units_inventory()
    _attach_consumer_maps(units)
    containers=read_command(['docker','ps','-a','--format',
          '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'])
    return {
        'schema':SCHEMA, 'observed_utc':utc_now(), 'hostname':socket.gethostname(),
        'platform':platform.platform(), 'architecture':platform.machine(),
        'device_model':read_device_model(),
        'jetson_marker':Path('/etc/nv_tegra_release').read_text().strip()
           if Path('/etc/nv_tegra_release').is_file() else None,
        'ssh_host_public_keys':ssh_host_key_fingerprints(),
        'units':units,
        'ports':read_command(['ss','-ltnp']),
        'containers':containers,
        'frontend_candidates':frontend_candidates(units,containers),
        'voice_runtime_files':fingerprint_files(voice_runtime_paths(units)),
        'backup_locations':summarize_backup_locations(),
        'models':read_command(['ollama','list']),
        'loaded_models':read_command(['ollama','ps']),
        'audio_inputs':read_command(['arecord','-l']),
        'audio_outputs':read_command(['aplay','-l']),
        'memory':read_command(['free','-h']),
        'storage':read_command(['df','-h','/','/mnt/orin-ai']),
        'notice':('Read-only metadata. Port/container names may be sensitive. '
                  'HTTP health alone is not proof of working AIRI chat. '
                  'Ollama and docker may return permission errors. '
                  'Consumer maps list reverse dependencies, listener clients and '
                  'MQTT subscriptions; they are evidence, not permission to retire a unit.'),
    }

def write_json(path: Path, obj: dict, *, allow_existing: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and not allow_existing:
        raise SafetyError(f'Will not overwrite existing evidence: {path}')
    fd, tmp = tempfile.mkstemp(prefix='.staging-', dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj,f,indent=2,sort_keys=True)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def load_plan(path: Path, allowlist_path: Path | None = None) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SafetyError('Plan must be a regular file, not a symlink')
    data = json.loads(path.read_text())
    if data.get('schema') != SCHEMA or data.get('type') != 'airi_legacy_ui_retirement':
        raise SafetyError('Invalid plan schema')
    allowlist = load_reviewed_allowlist(allowlist_path)
    unit = data.get('unit')
    if unit in PROTECTED_UNITS or unit not in allowlist:
        raise SafetyError('Plan references a non-allowlisted or protected service')
    if data.get('machine_tag') != machine_tag():
        raise SafetyError('Plan belongs to a different machine')
    if data.get('phase') not in ('prepared','applying','pending_acceptance','accepted','rolling_back','rolled_back'):
        raise SafetyError('Invalid plan phase')
    try:
        expected_id = compute_id(data)
    except KeyError as exc:
        raise SafetyError(f'Incomplete plan fields: {exc}') from exc
    if expected_id != data.get('plan_id'):
        raise SafetyError('Plan identity mismatch; refusing mutation')
    if data.get('allowlist_entry') != allowlist[unit]:
        raise SafetyError('Reviewed allowlist entry does not match this plan')
    return data

def compute_id(plan: dict) -> str:
    fields = {k:plan[k] for k in ('schema','machine_tag','unit','original','unit_sha256','airi_url','created_utc')}
    canonical = json.dumps(fields,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

def parse_allowlist_document(data: dict) -> dict:
    """Return exact unit -> review entry. Patterns and protected units fail."""
    if not isinstance(data, dict) or data.get('schema') != ALLOWLIST_SCHEMA or type(data.get('schema')) is not int:
        raise SafetyError('Allowlist schema must be 1')
    entries = data.get('entries')
    if not isinstance(entries, list) or not entries:
        raise SafetyError('Allowlist entries must be a non-empty list of exact units')
    parsed = {}
    for item in entries:
        if not isinstance(item, dict):
            raise SafetyError('Allowlist entry must be an object')
        unit = item.get('unit')
        review_id = item.get('review_id')
        reason = item.get('reason')
        if not isinstance(unit, str) or UNIT_NAME_RE.fullmatch(unit) is None:
            raise SafetyError(f'Allowlist unit must be an exact service name, not a pattern: {unit!r}')
        if unit in PROTECTED_UNITS:
            raise SafetyError(f'Protected unit cannot be allowlisted: {unit}')
        if not isinstance(review_id, str) or REVIEW_ID_RE.fullmatch(review_id) is None:
            raise SafetyError('Allowlist review_id must be an exact review token')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or '\n' in reason:
            raise SafetyError('Allowlist reason must be a single-line explanation')
        if unit in parsed:
            raise SafetyError(f'Duplicate allowlist entry: {unit}')
        parsed[unit] = {'unit': unit, 'review_id': review_id, 'reason': reason.strip()}
    return parsed


def _read_json_object(path: Path, max_bytes: int, label: str) -> dict:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SafetyError(f'{label} missing or not a regular file: {path}') from exc
    except OSError as exc:
        raise SafetyError(f'{label} unreadable: {exc.__class__.__name__}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(f'{label} must be a regular file, not a symlink: {path}')
    if metadata.st_size > max_bytes:
        raise SafetyError(f'{label} exceeds {max_bytes} bytes: {path}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError(f'{label} is not UTF-8 JSON: {exc.__class__.__name__}') from exc
    if not isinstance(data, dict):
        raise SafetyError(f'{label} must be a JSON object')
    return data


def load_reviewed_allowlist(path: Path | None = None) -> dict:
    """Built-in legacy UI entries plus an optional reviewed file of exact units."""
    if set(BUILTIN_REVIEWED_ALLOWLIST) != set(LEGACY_UI_UNITS):
        raise SafetyError('Built-in allowlist does not match the legacy UI set')
    merged = {}
    for entry in BUILTIN_REVIEWED_ALLOWLIST.values():
        merged.update(parse_allowlist_document({'schema': ALLOWLIST_SCHEMA, 'entries': [entry]}))
    if path is None:
        return merged
    extra = parse_allowlist_document(_read_json_object(path, ALLOWLIST_MAX_BYTES, 'Allowlist'))
    overlap = sorted(set(extra) & set(merged))
    if overlap:
        raise SafetyError('Allowlist file repeats a built-in unit: ' + ', '.join(overlap))
    merged.update(extra)
    return merged


def backup_manifest_path(unit: str, root: Path | None = None) -> Path:
    """Path WS1 writes and WS2 reads for one exact unit."""
    if not isinstance(unit, str) or UNIT_NAME_RE.fullmatch(unit) is None:
        raise SafetyError('Refusing backup manifest path for a non-exact unit name')
    base = DEFAULT_BACKUP_ROOT if root is None else root
    return base / unit / 'manifest.json'


def load_backup_manifest(path: Path, *, unit: str, machine: str) -> dict:
    """Validate a WS1 manifest. Extra keys are ignored and not copied into the plan."""
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SafetyError(f'WS1 backup manifest missing or not a regular file: {path}') from exc
    except OSError as exc:
        raise SafetyError(f'WS1 backup manifest unreadable: {exc.__class__.__name__}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(f'WS1 backup manifest must be a regular file, not a symlink: {path}')
    if metadata.st_mode & stat.S_IWOTH:
        raise SafetyError('WS1 backup manifest is world-writable')
    if metadata.st_size > BACKUP_MANIFEST_MAX_BYTES:
        raise SafetyError('WS1 backup manifest is unexpectedly large')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError(f'WS1 backup manifest is not UTF-8 JSON: {exc.__class__.__name__}') from exc
    if not isinstance(data, dict):
        raise SafetyError('WS1 backup manifest must be a JSON object')
    required = ('schema', 'tool', 'unit', 'machine_tag', 'created_utc', 'restore_verified', 'sha256')
    missing = [key for key in required if key not in data]
    if missing:
        raise SafetyError('WS1 backup manifest missing fields: ' + ', '.join(missing))
    if data['schema'] != BACKUP_MANIFEST_SCHEMA or type(data['schema']) is not int or data['tool'] != BACKUP_MANIFEST_TOOL:
        raise SafetyError('WS1 backup manifest schema or tool does not match the WS2 contract')
    if data['unit'] != unit:
        raise SafetyError('WS1 backup manifest is for a different unit')
    if data['machine_tag'] != machine:
        raise SafetyError('WS1 backup manifest is for a different machine')
    if data['restore_verified'] is not True:
        raise SafetyError('WS1 backup manifest does not record a verified restore')
    if not isinstance(data['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', data['sha256']) is None:
        raise SafetyError('WS1 backup manifest sha256 must be 64 lowercase hex characters')
    if not isinstance(data['created_utc'], str):
        raise SafetyError('WS1 backup manifest created_utc is not ISO-8601')
    try:
        created = dt.datetime.fromisoformat(data['created_utc'])
    except ValueError as exc:
        raise SafetyError('WS1 backup manifest created_utc is not ISO-8601') from exc
    if created.tzinfo is None:
        raise SafetyError('WS1 backup manifest created_utc must include a timezone')
    return {
        'path': str(path),
        'schema': BACKUP_MANIFEST_SCHEMA,
        'tool': BACKUP_MANIFEST_TOOL,
        'unit': unit,
        'machine_tag': machine,
        'created_utc': data['created_utc'],
        'restore_verified': True,
        'sha256': data['sha256'],
    }


def systemd_property_list(value) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return text.split()


def dependency_lists(state: dict) -> dict:
    return {
        'reverse': {key: systemd_property_list(state.get(key)) for key in REVERSE_DEPENDENCY_KEYS},
        'forward': {key: systemd_property_list(state.get(key)) for key in FORWARD_DEPENDENCY_KEYS},
    }


def dependency_closure(unit: str, stdout: str) -> list[str]:
    names = []
    for raw in stdout.splitlines():
        line = raw.strip().lstrip('●').lstrip('*').strip()
        if not line or line.lower().startswith('legend:'):
            continue
        token = line.split()[0]
        if token == unit or CLOSURE_UNIT_RE.fullmatch(token) is None:
            continue
        names.append(token)
    return sorted(set(names))


def parse_ss_table(stdout: str, proto: str) -> list[dict]:
    rows = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith('State') or line.startswith('Netid'):
            continue
        proc_raw = ''
        if ' users:' in line:
            line, proc_raw = line.split(' users:', 1)
            proc_raw = 'users:' + proc_raw
        parts = line.split()
        if len(parts) < 5:
            raise SafetyError(f'Unrecognised ss output: {raw[:120]}')
        processes = [
            {'process': name, 'pid': int(pid), 'fd': int(fd)}
            for name, pid, fd in SS_PROCESS_RE.findall(proc_raw)
        ]
        rows.append({
            'proto': proto,
            'state': parts[0],
            'local': parts[3],
            'peer': parts[4],
            'processes': processes,
        })
    return rows


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    if endpoint.startswith('['):
        host, sep, port = endpoint[1:].partition(']:')
        if not sep or not port:
            raise SafetyError(f'Unrecognised socket endpoint: {endpoint}')
        return host, port
    host, sep, port = endpoint.rpartition(':')
    if not sep or not port:
        raise SafetyError(f'Unrecognised socket endpoint: {endpoint}')
    return host, port


def _same_endpoint(candidate: str, listener: str) -> bool:
    candidate_host, candidate_port = _split_endpoint(candidate)
    listener_host, listener_port = _split_endpoint(listener)
    if candidate_port == '*' or listener_port == '*' or candidate_port != listener_port:
        return False
    if listener_host in ('0.0.0.0', '*'):
        return ':' not in candidate_host
    if listener_host == '::':
        return ':' in candidate_host
    return candidate_host == listener_host


def associate_listener_clients(listen_rows: list[dict], established_rows: list[dict],
                               pids: set[int]) -> list[dict]:
    """Listeners owned by ``pids`` plus the other ends of their TCP connections."""
    owned = []
    for row in listen_rows:
        if row.get('state') not in ('LISTEN', 'UNCONN'):
            continue
        if {proc['pid'] for proc in row.get('processes') or []} & set(pids):
            owned.append(row)
    mapped = []
    for listener in owned:
        owner = next(proc for proc in listener['processes'] if proc['pid'] in set(pids))
        clients = {}
        if listener.get('state') == 'LISTEN':
            for established in established_rows:
                if established.get('proto') != listener.get('proto') or established.get('state') != 'ESTAB':
                    continue
                established_pids = {proc['pid'] for proc in established.get('processes') or []}
                if established_pids & set(pids) and _same_endpoint(established['local'], listener['local']):
                    clients.setdefault(established['peer'], {
                        'peer': established['peer'], 'pid': None, 'process': None, 'fd': None,
                    })
                elif not (established_pids & set(pids)) and _same_endpoint(established['peer'], listener['local']):
                    info = clients.setdefault(established['local'], {
                        'peer': established['local'], 'pid': None, 'process': None, 'fd': None,
                    })
                    if established.get('processes'):
                        proc = established['processes'][0]
                        info.update(pid=proc['pid'], process=proc['process'], fd=proc['fd'])
        cleaned = [item for item in clients.values() if item['pid'] not in set(pids)]
        mapped.append({
            'proto': listener.get('proto') or ('tcp' if listener.get('state') == 'LISTEN' else 'udp'),
            'local': listener['local'],
            'pid': owner['pid'],
            'process': owner['process'],
            'fd': owner['fd'],
            'clients': sorted(cleaned, key=lambda item: (item['peer'], item['pid'] or -1)),
        })
    return sorted(mapped, key=lambda item: (item['proto'], item['local']))


def mqtt_connections_for_pids(established_rows: list[dict], pids: set[int]) -> list[dict]:
    found = []
    seen = set()
    for row in established_rows:
        if row.get('state') != 'ESTAB':
            continue
        procs = [proc for proc in row.get('processes') or [] if proc['pid'] in set(pids)]
        if not procs:
            continue
        try:
            _local_host, local_port = _split_endpoint(row['local'])
            _peer_host, peer_port = _split_endpoint(row['peer'])
        except SafetyError:
            continue
        if local_port not in {str(port) for port in MQTT_PORTS} and peer_port not in {str(port) for port in MQTT_PORTS}:
            continue
        proc = procs[0]
        item = {
            'proto': row.get('proto') or 'tcp',
            'local': row['local'],
            'peer': row['peer'],
            'pid': proc['pid'],
            'process': proc['process'],
            'fd': proc['fd'],
        }
        key = (item['proto'], item['local'], item['peer'], item['pid'], item['fd'])
        if key in seen:
            continue
        seen.add(key)
        found.append(item)
    return sorted(found, key=lambda item: (item['local'], item['peer'], item['fd']))


def unit_pids(state: dict, cgroup_root: Path = Path('/sys/fs/cgroup')) -> tuple[list[int], str | None]:
    """MainPID plus cgroup.procs. Symlinks and ``..`` control groups are ignored."""
    pids = []
    note = None
    main = str(state.get('MainPID') or '0').strip()
    if main.isdigit() and int(main) > 0:
        pids.append(int(main))
    control = str(state.get('ControlGroup') or '').strip()
    if control.startswith('/') and '..' not in Path(control).parts:
        relative = control.lstrip('/')
        for candidate in (cgroup_root / relative / 'cgroup.procs',
                          cgroup_root / 'systemd' / relative / 'cgroup.procs'):
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                note = f'cgroup procs unreadable: {exc.__class__.__name__}'
                break
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                continue
            if metadata.st_size > 1024 * 1024:
                note = 'cgroup procs too large'
                break
            try:
                text = candidate.read_text(encoding='ascii')
            except (OSError, UnicodeError) as exc:
                note = f'cgroup procs unreadable: {exc.__class__.__name__}'
                break
            for line in text.splitlines():
                if line.strip().isdigit():
                    pids.append(int(line.strip()))
            note = None
            break
    return sorted(set(pids)), note


def _mqtt_encode_length(length: int) -> bytes:
    if length < 0 or length > 268435455:
        raise SafetyError('MQTT packet is too large')
    out = bytearray()
    while True:
        digit = length % 128
        length //= 128
        if length:
            digit |= 0x80
        out.append(digit)
        if not length:
            break
    return bytes(out)


def _mqtt_packet(packet_type: int, payload: bytes) -> bytes:
    return bytes((packet_type,)) + _mqtt_encode_length(len(payload)) + payload


def _mqtt_utf8(text: str) -> bytes:
    raw = text.encode('utf-8')
    if len(raw) > 65535:
        raise SafetyError('MQTT string is too long')
    return len(raw).to_bytes(2, 'big') + raw


class _MqttClosed(Exception):
    pass


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except socket.timeout as exc:
            raise TimeoutError('MQTT read timed out') from exc
        if not chunk:
            raise _MqttClosed()
        buf += chunk
    return bytes(buf)


def _read_mqtt_packet(sock: socket.socket) -> tuple[int, bytes]:
    packet_type = _recv_exact(sock, 1)[0]
    value = 0
    multiplier = 1
    for _ in range(4):
        digit = _recv_exact(sock, 1)[0]
        value += (digit & 0x7F) * multiplier
        if not digit & 0x80:
            break
        multiplier *= 128
    else:
        raise SafetyError('MQTT remaining length is invalid')
    if value > 65536:
        raise SafetyError('MQTT observation packet is too large')
    body = _recv_exact(sock, value) if value else b''
    return packet_type, body


def _parse_publish(body: bytes, qos: int) -> tuple[str, str]:
    if len(body) < 2:
        raise SafetyError('MQTT publish is truncated')
    topic_len = int.from_bytes(body[:2], 'big')
    if len(body) < 2 + topic_len:
        raise SafetyError('MQTT publish topic is truncated')
    topic = body[2:2 + topic_len].decode('utf-8', 'replace')
    rest = body[2 + topic_len:]
    if qos:
        if len(rest) < 2:
            raise SafetyError('MQTT publish packet id is truncated')
        rest = rest[2:]
    return topic, rest.decode('utf-8', 'replace')


def mqtt_observe_sys(host: str, port: int, timeout: float, client_id: str = 'orin-reconcile') -> dict:
    """Subscribe to ``$SYS/#`` on the loopback broker and return the topics seen.

    The session is clean, so the temporary subscription is not retained. Payloads
    that are not ``$SYS/`` topics are discarded.
    """
    if host != MQTT_OBSERVE_HOST:
        raise SafetyError('MQTT observation is limited to the loopback broker')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SafetyError('MQTT observation port is invalid')
    if timeout <= 0:
        raise SafetyError('MQTT observation timeout must be positive')
    if not client_id or len(client_id.encode('utf-8')) > 128:
        raise SafetyError('MQTT observer client id is invalid')
    deadline = time.monotonic() + float(timeout)
    try:
        sock = socket.create_connection((host, port), timeout=min(float(timeout), 5.0))
    except OSError as exc:
        raise SafetyError(f'MQTT broker observation failed: {exc.__class__.__name__}') from exc
    observed = {}
    try:
        remaining = max(0.1, deadline - time.monotonic())
        sock.settimeout(remaining)
        connect = _mqtt_utf8('MQTT') + bytes((4, 0x02)) + (30).to_bytes(2, 'big') + _mqtt_utf8(client_id)
        sock.sendall(_mqtt_packet(0x10, connect))
        packet_type, body = _read_mqtt_packet(sock)
        if packet_type != 0x20 or len(body) < 2 or body[1] != 0:
            code = body[1] if len(body) > 1 else 'missing'
            raise SafetyError(f'MQTT broker refused the observer connection: {code}')
        subscribe = (1).to_bytes(2, 'big') + _mqtt_utf8('$SYS/#') + bytes((0,))
        sock.sendall(_mqtt_packet(0x82, subscribe))
        packet_type, body = _read_mqtt_packet(sock)
        if packet_type != 0x90 or not body or body[-1] >= 0x80:
            raise SafetyError('MQTT broker refused the $SYS/# subscription')
        while len(observed) < 200 and time.monotonic() < deadline:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                packet_type, body = _read_mqtt_packet(sock)
            except (_MqttClosed, TimeoutError):
                break
            if (packet_type & 0xF0) != 0x30:
                continue
            qos = (packet_type & 0x06) >> 1
            topic, payload = _parse_publish(body, qos)
            if topic.startswith('$SYS/') and len(payload) <= 1024:
                observed[topic] = payload
        try:
            sock.sendall(bytes((0xE0, 0x00)))
        except OSError:
            pass
        return observed
    except _MqttClosed as exc:
        raise SafetyError('MQTT broker closed before the observer subscribed') from exc
    except TimeoutError as exc:
        raise SafetyError('MQTT broker observation timed out') from exc
    except OSError as exc:
        raise SafetyError(f'MQTT broker observation failed: {exc.__class__.__name__}') from exc
    finally:
        sock.close()


def read_mosquitto_subscriptions(path: Path, max_bytes: int = MOSQUITTO_DB_MAX_BYTES) -> list[dict] | None:
    """Parse SUB chunks from a mosquitto v5/v6 persistence file.

    Returns None when the file is absent. Message-store payloads are skipped
    by chunk length and never copied into the result. Symlinks are refused.
    """
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SafetyError(f'mosquitto persistence file unreadable: {exc.__class__.__name__}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SafetyError(f'Refusing to read a non-regular file or symlink: {path}')
    if metadata.st_size > max_bytes:
        raise SafetyError(f'mosquitto persistence file exceeds {max_bytes} bytes')
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise SafetyError(f'mosquitto persistence file unreadable: {exc.__class__.__name__}') from exc
    if len(blob) < 23 or blob[:15] != MOSQUITTO_DB_MAGIC:
        raise SafetyError('mosquitto persistence file is not a v5/v6 database')
    version = int.from_bytes(blob[19:23], 'big')
    if version not in (5, 6):
        raise SafetyError(f'mosquitto persistence version {version} is not supported')
    pos = 23
    found = []
    while pos < len(blob):
        if pos + 8 > len(blob):
            raise SafetyError('mosquitto persistence chunk header is truncated')
        chunk = int.from_bytes(blob[pos:pos + 4], 'big')
        length = int.from_bytes(blob[pos + 4:pos + 8], 'big')
        pos += 8
        if length < 0 or pos + length > len(blob):
            raise SafetyError('mosquitto persistence chunk is truncated')
        payload = blob[pos:pos + length]
        pos += length
        if chunk != MOSQUITTO_SUB_CHUNK:
            continue
        if length < 12 or len(payload) < 12:
            raise SafetyError('mosquitto subscription chunk is truncated')
        identifier = int.from_bytes(payload[0:4], 'big')
        id_len = int.from_bytes(payload[4:6], 'big')
        topic_len = int.from_bytes(payload[6:8], 'big')
        qos = payload[8]
        options = payload[9]
        body = payload[12:]
        if len(body) != id_len + topic_len:
            raise SafetyError('mosquitto subscription chunk length does not match its strings')
        try:
            client_id = body[:id_len].decode('utf-8')
            topic = body[id_len:].decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SafetyError('mosquitto subscription is not UTF-8') from exc
        found.append({
            'client_id': client_id,
            'topic': topic,
            'qos': qos,
            'options': options,
            'identifier': identifier,
        })
    found.sort(key=lambda item: (item['client_id'], item['topic'], item['qos'], item['identifier']))
    return found


def consumer_attention_lines(consumers: dict) -> list[str]:
    lines = []
    reverse = consumers.get('reverse_dependencies') or {}
    required = list(reverse.get('RequiredBy') or [])
    requisite = list(reverse.get('RequisiteOf') or [])
    if required:
        lines.append('RequiredBy ' + ' '.join(required))
    if requisite:
        lines.append('RequisiteOf ' + ' '.join(requisite))
    listeners = consumers.get('listeners') or []
    client_count = sum(len(item.get('clients') or []) for item in listeners)
    if client_count:
        lines.append(f'{client_count} connected listener client(s)')
    unattributed = consumers.get('unattributed_listeners') or []
    if unattributed:
        lines.append(
            f'{len(unattributed)} listeners had no process owner and were not attributed')
    mqtt = consumers.get('mqtt') or {}
    subscriptions = list(mqtt.get('subscriptions') or [])
    connections = list(mqtt.get('unit_connections') or [])
    if connections:
        lines.append(
            f'unit has {len(connections)} MQTT connection(s); '
            f'{len(subscriptions)} broker subscription(s) observed')
    elif subscriptions:
        lines.append(f'{len(subscriptions)} MQTT subscription(s) observed on the broker')
    sys_props = mqtt.get('sys') or {}
    count = sys_props.get('$SYS/broker/subscriptions/count')
    if mqtt.get('persistence') == 'absent' and count not in (None, '0', ''):
        lines.append(
            f'broker reports {count} subscriptions but the persistence file is absent; '
            'topic filters were not listed')
    elif mqtt.get('persistence') == 'parsed' and isinstance(count, str) and count.isdigit() and int(count) != len(subscriptions):
        lines.append('persistence subscription count differs from $SYS; the database may be stale until mosquitto autosave')
    if not mqtt.get('reachable'):
        lines.append('MQTT broker was not observed')
    for item in consumers.get('observation_errors') or []:
        lines.append(str(item))
    return lines


def consumer_summary(consumers: dict) -> dict:
    listeners = consumers.get('listeners') or []
    mqtt = consumers.get('mqtt') or {}
    reverse = consumers.get('reverse_dependencies') or {}
    return {
        'required_by': list(reverse.get('RequiredBy') or []),
        'wanted_by': list(reverse.get('WantedBy') or []),
        'reverse_closure': list(consumers.get('reverse_dependency_closure') or []),
        'listeners': len(listeners),
        'listener_clients': sum(len(item.get('clients') or []) for item in listeners),
        'mqtt_subscriptions': len(mqtt.get('subscriptions') or []),
        'mqtt_unit_connections': len(mqtt.get('unit_connections') or []),
        'mqtt_reachable': bool(mqtt.get('reachable')),
    }


def empty_consumer_map(unit: str) -> dict:
    mqtt = {
        'endpoint': f'{MQTT_OBSERVE_HOST}:{MQTT_OBSERVE_PORT}',
        'reachable': False,
        'sys': {},
        'subscriptions': [],
        'persistence': 'absent',
        'persistence_path': str(DEFAULT_MOSQUITTO_DB),
        'unit_connections': [],
    }
    consumers = {
        'unit': unit,
        'unit_pids': [],
        'reverse_dependencies': {key: [] for key in REVERSE_DEPENDENCY_KEYS},
        'forward_dependencies': {key: [] for key in FORWARD_DEPENDENCY_KEYS},
        'reverse_dependency_closure': [],
        'listeners': [],
        'unattributed_listeners': [],
        'mqtt': mqtt,
        'observation_errors': [],
    }
    consumers['attention'] = consumer_attention_lines(consumers)
    return consumers


def _run_stdout(runner, args: list[str], timeout: int, strict: bool, errors: list[str], what: str):
    try:
        result = runner.run(list(args), timeout)
    except SafetyError as exc:
        if strict:
            raise
        errors.append(str(exc))
        return None
    if result.returncode != 0:
        message = f'{what} failed with exit {result.returncode}'
        stderr = getattr(result, 'stderr', '') or ''
        if stderr.strip():
            message = f'{message}: {stderr.strip()[:160]}'
        if strict:
            raise SafetyError(message)
        errors.append(message)
        return None
    return result.stdout


def observe_runtime(*, strict: bool, runner=None, persistence_path: Path | None = None,
                    mqtt_observe=None, mqtt_timeout: float = MQTT_OBSERVE_TIMEOUT,
                    mqtt_port: int = MQTT_OBSERVE_PORT) -> dict:
    runner = RUN if runner is None else runner
    persistence_path = DEFAULT_MOSQUITTO_DB if persistence_path is None else persistence_path
    errors = []
    listen_rows = []
    established_rows = []
    for proto, kind, args in SS_COMMANDS:
        stdout = _run_stdout(runner, args, 15, strict, errors, 'ss')
        if stdout is None:
            continue
        try:
            rows = parse_ss_table(stdout, proto)
        except SafetyError as exc:
            if strict:
                raise
            errors.append(str(exc))
            continue
        if kind == 'listen':
            listen_rows.extend(rows)
        else:
            established_rows.extend(rows)
    if listen_rows and all(not row['processes'] for row in listen_rows):
        message = 'ss returned listeners but no process owners'
        if strict:
            raise SafetyError(message)
        errors.append(message)
    mqtt = {
        'endpoint': f'{MQTT_OBSERVE_HOST}:{mqtt_port}',
        'reachable': False,
        'sys': {},
        'subscriptions': [],
        'persistence': 'absent',
        'persistence_path': str(persistence_path),
        'unit_connections': [],
    }
    observer = mqtt_observe_sys if mqtt_observe is None else mqtt_observe
    try:
        mqtt['sys'] = observer(MQTT_OBSERVE_HOST, mqtt_port, mqtt_timeout)
        mqtt['reachable'] = True
    except SafetyError as exc:
        mqtt['error'] = str(exc)
        if strict:
            raise
        errors.append(str(exc))
    try:
        subscriptions = read_mosquitto_subscriptions(persistence_path)
    except SafetyError as exc:
        mqtt['persistence'] = 'error'
        mqtt['persistence_error'] = str(exc)
        if strict:
            raise
        errors.append(str(exc))
    else:
        if subscriptions is None:
            mqtt['persistence'] = 'absent'
        else:
            mqtt['persistence'] = 'parsed'
            mqtt['subscriptions'] = subscriptions
    return {
        'listen': listen_rows,
        'established': established_rows,
        'mqtt': mqtt,
        'errors': errors,
    }


def map_unit_consumers(unit: str, state: dict, runtime: dict, *, runner=None,
                       cgroup_root: Path | None = None, strict: bool = True) -> dict:
    runner = RUN if runner is None else runner
    root = Path('/sys/fs/cgroup') if cgroup_root is None else cgroup_root
    pids, pid_note = unit_pids(state, root)
    lists = dependency_lists(state)
    errors = list(runtime.get('errors') or [])
    if pid_note:
        errors.append(pid_note)
    closure_args = ['systemctl', 'list-dependencies', '--reverse', '--plain', '--no-pager', '--all', unit]
    stdout = _run_stdout(runner, closure_args, 15, strict, errors, 'systemctl list-dependencies')
    closure = [] if stdout is None else dependency_closure(unit, stdout)
    pid_set = set(pids)
    listeners = associate_listener_clients(runtime.get('listen') or [], runtime.get('established') or [], pid_set)
    unattributed = sorted({
        row['local'] for row in (runtime.get('listen') or [])
        if row.get('state') in ('LISTEN', 'UNCONN') and not row.get('processes')
    })
    mqtt = dict(runtime.get('mqtt') or {})
    mqtt['unit_connections'] = mqtt_connections_for_pids(runtime.get('established') or [], pid_set)
    consumers = {
        'unit': unit,
        'unit_pids': pids,
        'reverse_dependencies': lists['reverse'],
        'forward_dependencies': lists['forward'],
        'reverse_dependency_closure': closure,
        'listeners': listeners,
        'unattributed_listeners': unattributed,
        'mqtt': mqtt,
        'observation_errors': errors,
    }
    consumers['attention'] = consumer_attention_lines(consumers)
    if strict and errors:
        raise SafetyError('; '.join(errors))
    return consumers


def build_consumer_map(unit: str, state: dict, *, strict: bool = True, runner=None,
                       cgroup_root: Path | None = None, persistence_path: Path | None = None,
                       mqtt_observe=None, mqtt_timeout: float = MQTT_OBSERVE_TIMEOUT,
                       mqtt_port: int = MQTT_OBSERVE_PORT) -> dict:
    runtime = observe_runtime(
        strict=strict, runner=runner, persistence_path=persistence_path,
        mqtt_observe=mqtt_observe, mqtt_timeout=mqtt_timeout, mqtt_port=mqtt_port)
    return map_unit_consumers(
        unit, state, runtime, runner=runner, cgroup_root=cgroup_root, strict=strict)


def check_unit_policy(unit: str, state: dict, allowlist: dict | None = None) -> dict:
    if allowlist is None:
        allowlist = load_reviewed_allowlist(None)
    if unit in PROTECTED_UNITS or unit not in allowlist:
        if unit not in PROTECTED_UNITS:
            raise SafetyError(f'exact reviewed allowlist entry required for {unit}')
        raise SafetyError(f'Not an approved legacy UI unit: {unit}')
    if state.get('LoadState') != 'loaded' or not state.get('FragmentPath'):
        raise SafetyError('Unit is not loaded from a regular fragment; inspect manually')
    if state.get('UnitFileState') not in ('enabled','disabled'):
        raise SafetyError('Only standard enabled/disabled systemd units are supported')
    if state.get('ActiveState') not in ('active','inactive'):
        raise SafetyError('Unit is not in a stable active/inactive state')
    if unit not in state.get('Names', '').split():
        raise SafetyError('Resolved unit name does not match requested unit')
    cascading = {
        key: state.get(key, '').strip()
        for key in ('PropagatesStopTo', 'BoundBy', 'ConsistsOf')
        if state.get(key, '').strip()
    }
    if cascading:
        detail = ', '.join(f'{key}={value}' for key, value in cascading.items())
        raise SafetyError(f'Stop propagation to other units detected ({detail}); manual review required')
    if state.get('TriggeredBy', '').strip():
        raise SafetyError('Socket/timer/path activation detected; retire the trigger as a separate reviewed change')
    entry = allowlist[unit]
    if entry.get('unit') != unit:
        raise SafetyError(f'exact reviewed allowlist entry required for {unit}')
    return entry

def prepare(unit: str, url: str, state_dir: Path, allowlist_path: Path | None = None) -> Path:
    root_required(); jetson_required()
    url = validate_private_url(url)
    allowlist = load_reviewed_allowlist(allowlist_path)
    state = show_unit(unit)
    entry = check_unit_policy(unit, state, allowlist)
    unit_sha256=unit_definition_fingerprint(unit)
    airi_health = probe_airi(url)
    if not airi_health['http_ok']:
        raise SafetyError(f'AIRI HTTP endpoint unavailable: {airi_health}')
    consumers = build_consumer_map(unit, state, strict=True)
    manifest_path = backup_manifest_path(unit)
    created = utc_now()
    plan = {'schema':SCHEMA, 'type':'airi_legacy_ui_retirement',
            'machine_tag':machine_tag(), 'hostname':socket.gethostname(),
            'created_utc':created,'airi_url':url,'original':state,
            'unit_sha256':unit_sha256,
            'unit':unit,'phase':'prepared', 'history':[],
            'airi_http_preflight':airi_health,
            'allowlist_entry': {
                'unit': entry['unit'],
                'review_id': entry['review_id'],
                'reason': entry['reason'],
            },
            'consumers': consumers,
            'consumer_attention': list(consumers.get('attention') or []),
            'backup_manifest_path': str(manifest_path),
            'instructions':('Before apply, independently verify ten REAL local AIRI chat '
                            'turns. HTTP 200 does not prove model/chat/audio works. '
                            'Review the recorded consumer map: reverse dependencies, '
                            'listener clients and MQTT subscriptions. Take a WS1 backup '
                            'and leave its manifest at backup_manifest_path. '
                            'No data is deleted: only this exact allowlisted service '
                            'may be stopped/disabled; reversible via rollback.')}
    plan['plan_id'] = compute_id(plan)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination=state_dir / f'{stamp}-{unit.removesuffix(".service")}-{plan["plan_id"]}.json'
    write_json(destination,plan)
    return destination

def record(path: Path, plan: dict, phase: str, message: str) -> None:
    plan['phase']=phase
    plan.setdefault('history',[]).append({'utc':utc_now(),'phase':phase,'message':message})
    write_json(path,plan,allow_existing=True)

def restore_original(plan: dict) -> None:
    unit = plan['unit']
    state = plan['original']
    if unit_definition_fingerprint(unit) != plan['unit_sha256']:
        raise SafetyError('Unit definition changed; operator must review recovery manually')
    # Restore both enable state and current running state. No unit files/data deleted.
    if state['UnitFileState'] == 'enabled':
        RUN.require(['systemctl','enable',unit], 30)
    else:
        RUN.require(['systemctl','disable',unit], 30)
    if state['ActiveState'] == 'active':
        RUN.require(['systemctl','start',unit], 40)
    else:
        RUN.require(['systemctl','stop',unit], 40)
    new = show_unit(unit)
    if new.get('UnitFileState') != state['UnitFileState'] or new.get('ActiveState') != state['ActiveState']:
        raise SafetyError(f'Rollback mismatch: expected {state}, got {new}')

def apply_plan(path: Path, confirmation: str, chat_ack: bool,
               backup_manifest: Path | None = None, allowlist_path: Path | None = None) -> dict:
    root_required(); jetson_required()
    plan = load_plan(path, allowlist_path=allowlist_path)
    if plan['phase'] != 'prepared':
        raise SafetyError(f'Plan must be prepared, not {plan["phase"]}')
    if confirmation != plan['plan_id'] or not chat_ack:
        raise SafetyError('Require exact --confirm PLAN_ID and --airi-chat-tested after 10 real replies')
    previous = show_unit(plan['unit'])
    check_unit_policy(plan['unit'], previous, load_reviewed_allowlist(allowlist_path))
    if unit_definition_fingerprint(plan['unit']) != plan['unit_sha256']:
        raise SafetyError('Unit-file contents changed since prepare; create a fresh plan')
    if previous != plan['original']:
        raise SafetyError('Unit state drift since prepare; create a fresh plan')
    health = probe_airi(plan['airi_url'])
    if not health['http_ok']:
        raise SafetyError(f'AIRI HTTP endpoint unavailable: {health}')
    if not isinstance(plan.get('consumers'), dict) or plan['consumers'].get('unit') != plan['unit']:
        raise SafetyError('Plan is missing the consumer map for this unit')
    manifest_path = backup_manifest_path(plan['unit']) if backup_manifest is None else backup_manifest
    plan['backup_manifest'] = load_backup_manifest(
        manifest_path, unit=plan['unit'], machine=machine_tag())
    record(path,plan,'applying','Started one-unit cutover')
    try:
        RUN.require(['systemctl','disable','--now',plan['unit']], 40)
        now = show_unit(plan['unit'])
        if now.get('ActiveState') != 'inactive' or now.get('UnitFileState') != 'disabled':
            raise SafetyError(f'Legacy UI not properly retired: {now}')
        health = probe_airi(plan['airi_url'])
        if not health['http_ok']:
            raise SafetyError(f'AIRI HTTP check failed after cutover: {health}')
        record(path,plan,'pending_acceptance','Legacy UI disabled; AIRI HTTP still responds. Manual chat retest required.')
    except Exception as exc:
        record(path,plan,'rolling_back',f'Auto rollback following failure: {exc}')
        try:
            restore_original(plan)
            record(path,plan,'rolled_back',f'Automatic rollback successful after: {exc}')
        except Exception as rb_exc:
            record(path,plan,'rolling_back',f'ROLLBACK FAILED: {rb_exc}; investigate before any further action')
            raise SafetyError(f'Cutover failed: {exc}; automatic rollback also failed: {rb_exc}') from rb_exc
        raise SafetyError(f'Cutover failed, automatic rollback succeeded: {exc}') from exc
    return plan

def accept_plan(path: Path, confirmation: str, chat_ack: bool,
                allowlist_path: Path | None = None) -> dict:
    root_required(); jetson_required()
    plan=load_plan(path, allowlist_path=allowlist_path)
    if plan['phase'] != 'pending_acceptance':
        raise SafetyError(f'Plan not awaiting acceptance: {plan["phase"]}')
    if confirmation != plan['plan_id'] or not chat_ack:
        raise SafetyError('Require exact plan ID and 10 real chat replies after cutover')
    unit=show_unit(plan['unit'])
    if unit.get('ActiveState')!='inactive' or unit.get('UnitFileState')!='disabled':
        raise SafetyError('Legacy UI state drift after apply; cannot accept')
    if not probe_airi(plan['airi_url'])['http_ok']:
        raise SafetyError('AIRI HTTP failed; rollback should be performed')
    record(path,plan,'accepted','Operator verified 10 real AIRI chat replies after cutover')
    return plan

def rollback(path: Path, confirmation: str, allowlist_path: Path | None = None) -> dict:
    root_required(); jetson_required()
    plan=load_plan(path, allowlist_path=allowlist_path)
    if plan['phase'] not in ('pending_acceptance','accepted','applying','rolling_back'):
        raise SafetyError(f'Cannot rollback plan in phase {plan["phase"]}')
    if confirmation != plan['plan_id']:
        raise SafetyError('Require exact plan ID to rollback')
    record(path,plan,'rolling_back','Operator requested rollback')
    restore_original(plan)
    record(path,plan,'rolled_back','Original unit state restored and verified')
    return plan

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(
        description='AIRI-first one-unit cutover. The module docstring is the WS1 '
                    'backup-manifest contract, the exact allowlist format, and the consumer map.')
    sub=parser.add_subparsers(dest='command',required=True)
    inv=sub.add_parser('inventory',help='Read-only metadata collection, no sudo required')
    inv.add_argument('--output',type=Path,help='Optional local JSON output; default stdout')
    pre=sub.add_parser('prepare',help='Save exact service state; NO service changes')
    pre.add_argument('--unit',required=True,
        help='Exact allowlisted unit. Built-ins are the four legacy UI services; other units need --allowlist')
    pre.add_argument('--airi-url',required=True,help='Known, verified AIRI local origin/port')
    pre.add_argument('--state-dir',type=Path,default=DEFAULT_STATE_DIR)
    pre.add_argument('--allowlist',type=Path,
        help='Reviewed JSON file of exact extra unit entries (schema 1)')
    app=sub.add_parser('apply',help='Disable one verified redundant unit, auto-rollback on error')
    app.add_argument('--plan',type=Path,required=True)
    app.add_argument('--confirm',required=True)
    app.add_argument('--airi-chat-tested',action='store_true',
        help='Confirm actual live model-backed AIRI chat has passed BEFORE cutover')
    app.add_argument('--backup-manifest',type=Path,
        help='WS1 manifest for this unit. Default: /var/lib/orin-airi-recovery/backups/<unit>/manifest.json')
    app.add_argument('--allowlist',type=Path,
        help='Same reviewed allowlist file used at prepare, when the unit is not built in')
    accept=sub.add_parser('accept',help='Record 10 real AIRI chat replies after one-unit cutover')
    accept.add_argument('--plan',type=Path,required=True)
    accept.add_argument('--confirm',required=True)
    accept.add_argument('--airi-chat-tested',action='store_true')
    accept.add_argument('--allowlist',type=Path,
        help='Same reviewed allowlist file used at prepare, when the unit is not built in')
    back=sub.add_parser('rollback',help='Restore original active/enabled state')
    back.add_argument('--plan',type=Path,required=True)
    back.add_argument('--confirm',required=True)
    back.add_argument('--allowlist',type=Path,
        help='Same reviewed allowlist file used at prepare, when the unit is not built in')
    status=sub.add_parser('status',help='Read a staged plan without modifying anything')
    status.add_argument('--plan',type=Path,required=True)
    status.add_argument('--allowlist',type=Path,
        help='Same reviewed allowlist file used at prepare, when the unit is not built in')
    args=parser.parse_args(argv)
    try:
        if args.command=='inventory':
            obj=inventory()
            if args.output:
                write_json(args.output,obj)
                print(f'Inventory written: {args.output}')
            else:
                print(json.dumps(obj,indent=2))
        elif args.command=='prepare':
            target=prepare(args.unit,args.airi_url,args.state_dir,allowlist_path=args.allowlist)
            p=load_plan(target,allowlist_path=args.allowlist)
            print(f'Plan prepared: {target}\nPlan ID: {p["plan_id"]}')
            print('No service changes performed. Verify 10 real AIRI chat responses before apply.')
            for line in p.get('consumer_attention') or []:
                print(f'CONSUMER: {line}')
            print('WS1 backup manifest required before apply: ' + p['backup_manifest_path'])
        elif args.command=='apply':
            p=apply_plan(args.plan,args.confirm,args.airi_chat_tested,
                         backup_manifest=args.backup_manifest,allowlist_path=args.allowlist)
            print(f'{p["unit"]} disabled provisionally. AIRI HTTP checked, but manual chat retest is still REQUIRED. Use accept or rollback.')
        elif args.command=='accept':
            p=accept_plan(args.plan,args.confirm,args.airi_chat_tested,allowlist_path=args.allowlist)
            print(f'{p["unit"]} cutover accepted after operator live chat test. Rollback remains available.')
        elif args.command=='rollback':
            p=rollback(args.plan,args.confirm,allowlist_path=args.allowlist)
            print(f'{p["unit"]} original enabled/active state restored and verified.')
        elif args.command=='status':
            p=load_plan(args.plan,allowlist_path=args.allowlist)
            shown={k:p[k] for k in ('plan_id','unit','phase','created_utc','airi_url','history')}
            if isinstance(p.get('allowlist_entry'), dict):
                shown['review_id']=p['allowlist_entry'].get('review_id')
            if 'backup_manifest_path' in p:
                shown['backup_manifest_path']=p['backup_manifest_path']
            if 'consumer_attention' in p:
                shown['consumer_attention']=p['consumer_attention']
            if isinstance(p.get('consumers'), dict):
                shown['consumer_summary']=consumer_summary(p['consumers'])
            print(json.dumps(shown,indent=2))
    except (SafetyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'BLOCKED: {exc}',file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())

