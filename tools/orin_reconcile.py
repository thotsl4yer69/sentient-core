#!/usr/bin/env python3
"""AIRI-first Orin service cutover: evidence, one legacy UI at a time, rollback.

No installation, deletion, model download, config rewrite, or automatic discovery
is interpreted as permission to mutate the host. Python 3.10+, stdlib only.
"""
from __future__ import annotations

import argparse
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
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = 2
DEFAULT_STATE_DIR = Path('/var/lib/orin-airi-recovery')
BACKUP_LOCATIONS = (
    DEFAULT_STATE_DIR,
    Path('/mnt/orin-ai/backups'),
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
    raw = RUN.require(['systemctl','show',unit,
        '--no-pager','-p','LoadState','-p','ActiveState','-p','UnitFileState',
        '-p','FragmentPath','-p','Names','-p','PropagatesStopTo',
        '-p','BoundBy','-p','ConsistsOf','-p','TriggeredBy',
        '-p','MainPID','-p','User','-p','WorkingDirectory'])
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

def inventory() -> dict:
    """Metadata only: no environment, docker inspect, process arguments or journals."""
    units=units_inventory()
    containers=read_command(['docker','ps','-a','--format',
          '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'])
    return {
        'schema':SCHEMA, 'observed_utc':utc_now(), 'hostname':socket.gethostname(),
        'platform':platform.platform(), 'architecture':platform.machine(),
        'jetson_marker':Path('/etc/nv_tegra_release').read_text().strip()
           if Path('/etc/nv_tegra_release').is_file() else None,
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
                  'Ollama and docker may return permission errors.'),
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

def load_plan(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise SafetyError('Plan must be a regular file, not a symlink')
    data = json.loads(path.read_text())
    if data.get('schema') != SCHEMA or data.get('type') != 'airi_legacy_ui_retirement':
        raise SafetyError('Invalid plan schema')
    if data.get('unit') not in LEGACY_UI_UNITS:
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
    return data

def compute_id(plan: dict) -> str:
    fields = {k:plan[k] for k in ('schema','machine_tag','unit','original','unit_sha256','airi_url','created_utc')}
    canonical = json.dumps(fields,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]

def check_unit_policy(unit: str, state: dict) -> None:
    if unit in PROTECTED_UNITS or unit not in LEGACY_UI_UNITS:
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

def prepare(unit: str, url: str, state_dir: Path) -> Path:
    root_required(); jetson_required()
    url = validate_private_url(url)
    state = show_unit(unit)
    check_unit_policy(unit,state)
    unit_sha256=unit_definition_fingerprint(unit)
    airi_health = probe_airi(url)
    if not airi_health['http_ok']:
        raise SafetyError(f'AIRI HTTP endpoint unavailable: {airi_health}')
    created = utc_now()
    plan = {'schema':SCHEMA, 'type':'airi_legacy_ui_retirement',
            'machine_tag':machine_tag(), 'hostname':socket.gethostname(),
            'created_utc':created,'airi_url':url,'original':state,
            'unit_sha256':unit_sha256,
            'unit':unit,'phase':'prepared', 'history':[],
            'airi_http_preflight':airi_health,
            'instructions':('Before apply, independently verify ten REAL local AIRI chat '
                            'turns. HTTP 200 does not prove model/chat/audio works. '
                            'No data is deleted: only this exact legacy UI service '
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

def apply_plan(path: Path, confirmation: str, chat_ack: bool) -> dict:
    root_required(); jetson_required()
    plan = load_plan(path)
    if plan['phase'] != 'prepared':
        raise SafetyError(f'Plan must be prepared, not {plan["phase"]}')
    if confirmation != plan['plan_id'] or not chat_ack:
        raise SafetyError('Require exact --confirm PLAN_ID and --airi-chat-tested after 10 real replies')
    previous = show_unit(plan['unit'])
    check_unit_policy(plan['unit'],previous)
    if unit_definition_fingerprint(plan['unit']) != plan['unit_sha256']:
        raise SafetyError('Unit-file contents changed since prepare; create a fresh plan')
    if previous != plan['original']:
        raise SafetyError('Unit state drift since prepare; create a fresh plan')
    health = probe_airi(plan['airi_url'])
    if not health['http_ok']:
        raise SafetyError(f'AIRI HTTP endpoint unavailable: {health}')
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

def accept_plan(path: Path, confirmation: str, chat_ack: bool) -> dict:
    root_required(); jetson_required()
    plan=load_plan(path)
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

def rollback(path: Path, confirmation: str) -> dict:
    root_required(); jetson_required()
    plan=load_plan(path)
    if plan['phase'] not in ('pending_acceptance','accepted','applying','rolling_back'):
        raise SafetyError(f'Cannot rollback plan in phase {plan["phase"]}')
    if confirmation != plan['plan_id']:
        raise SafetyError('Require exact plan ID to rollback')
    record(path,plan,'rolling_back','Operator requested rollback')
    restore_original(plan)
    record(path,plan,'rolled_back','Original unit state restored and verified')
    return plan

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    inv=sub.add_parser('inventory',help='Read-only metadata collection, no sudo required')
    inv.add_argument('--output',type=Path,help='Optional local JSON output; default stdout')
    pre=sub.add_parser('prepare',help='Save exact service state; NO service changes')
    pre.add_argument('--unit',required=True,choices=sorted(LEGACY_UI_UNITS))
    pre.add_argument('--airi-url',required=True,help='Known, verified AIRI local origin/port')
    pre.add_argument('--state-dir',type=Path,default=DEFAULT_STATE_DIR)
    app=sub.add_parser('apply',help='Disable one verified redundant UI, auto-rollback on error')
    app.add_argument('--plan',type=Path,required=True)
    app.add_argument('--confirm',required=True)
    app.add_argument('--airi-chat-tested',action='store_true',
        help='Confirm actual live model-backed AIRI chat has passed BEFORE cutover')
    accept=sub.add_parser('accept',help='Record 10 real AIRI chat replies after one-unit cutover')
    accept.add_argument('--plan',type=Path,required=True)
    accept.add_argument('--confirm',required=True)
    accept.add_argument('--airi-chat-tested',action='store_true')
    back=sub.add_parser('rollback',help='Restore original active/enabled state')
    back.add_argument('--plan',type=Path,required=True)
    back.add_argument('--confirm',required=True)
    status=sub.add_parser('status',help='Read a staged plan without modifying anything')
    status.add_argument('--plan',type=Path,required=True)
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
            target=prepare(args.unit,args.airi_url,args.state_dir)
            p=load_plan(target)
            print(f'Plan prepared: {target}\nPlan ID: {p["plan_id"]}')
            print('No service changes performed. Verify 10 real AIRI chat responses before apply.')
        elif args.command=='apply':
            p=apply_plan(args.plan,args.confirm,args.airi_chat_tested)
            print(f'{p["unit"]} disabled provisionally. AIRI HTTP checked, but manual chat retest is still REQUIRED. Use accept or rollback.')
        elif args.command=='accept':
            p=accept_plan(args.plan,args.confirm,args.airi_chat_tested)
            print(f'{p["unit"]} cutover accepted after operator live chat test. Rollback remains available.')
        elif args.command=='rollback':
            p=rollback(args.plan,args.confirm)
            print(f'{p["unit"]} original enabled/active state restored and verified.')
        elif args.command=='status':
            p=load_plan(args.plan)
            print(json.dumps({k:p[k] for k in ('plan_id','unit','phase','created_utc','airi_url','history')},indent=2))
    except (SafetyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'BLOCKED: {exc}',file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())

