#!/usr/bin/env python3
"""Inspect/recover local GLBs and install versioned static avatar releases.
Python 3.10+, standard library only. No services or model stores are modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
KNOWN = [
    Path('/mnt/orin-ai/archive-cortana-v1/frontend/public/avatar.glb'),
    Path('/mnt/orin-ai/archive-cortana-v1/frontend/public/avatar_static.glb'),
    Path('/mnt/orin-ai/sentient-core-v1/frontend/public/avatar.glb'),
    Path('/mnt/orin-ai/sentient-core-v1/avatar/avatar.glb'),
]

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False, encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        temporary = stream.name
    os.replace(temporary, path)

def audit(path):
    path = Path(path)
    size = path.stat().st_size
    if not 20 <= size <= 256 * 1024 * 1024:
        raise ValueError('GLB size must be between 20 bytes and 256 MiB.')
    with path.open('rb') as stream:
        header = stream.read(20)
        magic, version, length, chunk_length, chunk_type = struct.unpack('<4sIIII', header)
        if magic != b'glTF' or version != 2 or length != size or chunk_type != 0x4E4F534A or chunk_length > size-20:
            raise ValueError('Invalid GLB 2.0 header.')
        data = json.loads(stream.read(chunk_length))
    meshes = data.get('meshes', [])
    shapes = sorted({name for mesh in meshes for name in mesh.get('extras', {}).get('targetNames', [])})
    nodes = data.get('nodes', [])
    joint_ids = {joint for skin in data.get('skins', []) for joint in skin.get('joints', [])}
    bones = [nodes[i].get('name', '') for i in sorted(joint_ids)]
    def normalized(name):
        return name.split(':')[-1].removeprefix('mixamorig').lower()
    bone_names = {normalized(name) for name in bones}
    essential = {'hips', 'spine', 'neck', 'head'}
    embedded = all(not entry.get('uri') or entry['uri'].startswith('data:') for group in ('buffers','images') for entry in data.get(group, []))
    lips = sum(name.startswith('viseme_') for name in shapes)
    basic_compatibility = essential.issubset(bone_names) and {'eyeBlinkLeft','eyeBlinkRight'}.issubset(shapes) and lips >= 8 and embedded
    return {'source': str(path.resolve()), 'bytes':size, 'sha256':digest(path), 'bones':len(joint_ids),
            'boneNames':bones, 'facialShapes':len(shapes), 'shapeNames':shapes, 'embedded':embedded,
            'visemeCount':lips, 'profileCandidate':basic_compatibility,
            'verdict':'CANDIDATE_REQUIRES_BROWSER_LOAD' if basic_compatibility else 'PRESERVED_REQUIRES_RIG_ADAPTER'}

def recover(root=ROOT, sources=None):
    root = Path(root)
    config_path = root / 'config.json'
    config = json.loads(config_path.read_text())
    results = []
    for source in sources if sources is not None else KNOWN:
        source = Path(source)
        if not source.is_file():
            results.append({'source':str(source), 'verdict':'NOT_FOUND'})
            continue
        try:
            info = audit(source)
            model_id = 'recovered-' + info['sha256'][:12]
            destination = root / 'models' / (model_id + '.glb')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and digest(destination) != info['sha256']:
                raise ValueError('Existing recovered destination has different contents.')
            if not destination.exists():
                shutil.copy2(source, destination)
            if digest(source) != info['sha256'] or digest(destination) != info['sha256']:
                raise ValueError('Source changed during copy; no profile activated.')
            info['copy'] = str(destination)
            if info['profileCandidate'] and not any(p['id'] == model_id for p in config['profiles']):
                config['profiles'].append({'id':model_id, 'label':'Recovered Orin avatar (load-test pending)',
                    'url':'models/'+destination.name, 'body':'F', 'license':'Existing local asset: see original terms'})
            results.append(info)
        except (OSError, ValueError, KeyError, IndexError) as error:
            results.append({'source':str(source), 'verdict':'REQUIRES_REVIEW', 'error':str(error)})
    # Keep the validated supplied model as default; never silently switch to an untested rig.
    atomic_json(config_path, config)
    atomic_json(root / 'recovery-report.json', results)
    return results

def static_directory(explicit=None):
    if explicit:
        path=Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise ValueError('The specified static directory does not exist.')
        return path
    candidates=[]
    for project in ['sentient-core-v1','sentient-core','sentient-core/sentient-core','coretana-platform']:
        path=Path('/mnt/orin-ai')/project/'interfaces/web_chat/static'
        if path.is_dir():
            candidates.append(path.resolve())
    candidates=list(dict.fromkeys(candidates))
    if len(candidates)!=1:
        raise ValueError('Select the running server static directory with --static-dir. Candidates: '+', '.join(map(str,candidates)))
    return candidates[0]

def install(explicit=None, replace_renderer=False, root=ROOT):
    root=Path(root).resolve()
    required=['index.html','boot.js','ui.js','config.json','athena.bundle.mjs','models/athena.glb']
    for item in required:
        if not (root/item).is_file():
            raise ValueError('Incomplete release: '+item+'. Use the full release ZIP or run build.sh first.')
    audit(root/'models/athena.glb')
    static=static_directory(explicit)
    code_hash=hashlib.sha256(''.join(digest(root/item) for item in required).encode()).hexdigest()[:12]
    release_name='athena3d-2.0.0-'+code_hash
    release=static/release_name
    if not release.exists():
        with tempfile.TemporaryDirectory(prefix='.athena-stage-',dir=static) as stage:
            staged=Path(stage)/release_name
            shutil.copytree(root,staged,ignore=shutil.ignore_patterns('node_modules','vendor','.git','__pycache__'))
            # Compiled runtime needs no vendor directory. Never touch the original asset stores.
            staged.rename(release)
    else:
        for item in required:
            if digest(release/item)!=digest(root/item):
                raise ValueError('Existing versioned release differs; it was left unchanged.')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=static/('.athena-backup-'+stamp)
    backup.mkdir()
    redirect='<!doctype html><meta charset="utf-8"><title>Athena 3D</title><script>location.replace(new URL("./'+release_name+'/index.html"+location.search+location.hash,location.href));</script>'
    changes={'athena.html':redirect}
    if replace_renderer:
        changes['avatar-hologram.js']=(root/'boot.js').read_text().replace("new URL('./',scriptURL)","new URL('./"+release_name+"/',scriptURL)")
    receipt={'release':str(release),'staticDirectory':str(static),'files':[],'status':'installed_static_assets_only'}
    try:
        for filename,content in changes.items():
            target=static/filename
            if target.is_symlink():
                raise ValueError('Refusing to replace symlink '+str(target))
            previous=backup/filename
            existed=target.exists()
            if existed:
                shutil.copy2(target,previous)
            with tempfile.NamedTemporaryFile('w',dir=static,delete=False,encoding='utf-8') as stream:
                stream.write(content);temporary=Path(stream.name)
            temporary.chmod(0o644)
            os.replace(temporary,target)
            receipt['files'].append({'target':str(target),'backup':str(previous) if existed else None,'installedSha256':digest(target)})
        atomic_json(backup/'receipt.json',receipt)
    except Exception:
        for entry in reversed(receipt['files']):
            if entry['backup']:
                shutil.copy2(entry['backup'],entry['target'])
            else:
                Path(entry['target']).unlink(missing_ok=True)
        raise
    receipt['rollbackReceipt']=str(backup/'receipt.json')
    receipt['kioskPath']='/static/athena.html'
    return receipt

def rollback(receipt_path):
    receipt=json.loads(Path(receipt_path).read_text())
    for entry in receipt['files']:
        if digest(entry['target'])!=entry['installedSha256']:
            raise ValueError('A file changed after installation. It was left untouched: '+entry['target'])
    for entry in receipt['files']:
        if entry['backup']:
            shutil.copy2(entry['backup'],entry['target'])
        else:
            Path(entry['target']).unlink()
    return {'status':'restored_previous_entrypoints','releaseRetained':receipt['release']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('audit');p.add_argument('source',type=Path)
    p=sub.add_parser('recover');p.add_argument('--source',action='append',type=Path)
    p=sub.add_parser('install');p.add_argument('--static-dir');p.add_argument('--replace-renderer',action='store_true')
    p=sub.add_parser('rollback');p.add_argument('receipt',type=Path)
    args=parser.parse_args()
    try:
        if args.command=='audit': result=audit(args.source)
        elif args.command=='recover': result=recover(sources=args.source)
        elif args.command=='install': result=install(args.static_dir,args.replace_renderer)
        else: result=rollback(args.receipt)
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,KeyError) as error:
        parser.exit(1,str(error)+'\n')

if __name__=='__main__':
    main()
