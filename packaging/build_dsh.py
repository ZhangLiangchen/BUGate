#!/usr/bin/env python3
"""Build an offline-installable native bundle from a finite source inventory.

Run the native TypeScript build first. This script copies only declared release
resources to a temporary staging tree; no editable package or development-machine
path enters the tarball. A wheelhouse is resolved once and checksum inventoried.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'adapters/deepseek-harness/plugin'


def run(argv, *, cwd):
    result = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_name(name):
    parts = Path(name).parts
    denied = {'node_modules', '__pycache__', '.git', '.env', '.DS_Store'}
    if any(part in denied for part in parts) or name.endswith(('.pyc', '.pyo')):
        raise SystemExit(f'unsafe packaged member: {name}')


def validate_wheel(path):
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            check_name(info.filename)
        names = set(archive.namelist())
        required = {'bugate/cli.py', 'bugate/integrations.py', 'bugate/resources/protocol/v2/schemas/prepared_protocol_context.schema.json'}
        if not required <= names:
            raise SystemExit(f'core wheel missing members: {sorted(required - names)}')


def build(out, python):
    out.mkdir(parents=True, exist_ok=True)
    if not (PLUGIN / 'lib/index.js').is_file():
        raise SystemExit('Compile the native plugin before packaging: npm run build in adapters/deepseek-harness/plugin')
    with tempfile.TemporaryDirectory(prefix='bugate-build-') as temporary:
        stage = Path(temporary) / 'package'
        stage.mkdir()
        metadata = json.loads((PLUGIN / 'package.json').read_text())
        metadata.pop('devDependencies', None)
        metadata.pop('scripts', None)
        metadata.pop('private', None)
        metadata['files'] = ['lib', 'cordis.patch.yml', 'resources', 'python', 'README.md', 'README.zh-CN.md', 'LICENSE']
        serialized = json.dumps(metadata, indent=2) + '\n'
        if 'workspace:' in serialized or str(ROOT) in serialized:
            raise SystemExit('package manifest contains development-only references')
        (stage / 'package.json').write_text(serialized)
        for name in ('lib', 'cordis.patch.yml', 'README.md', 'README.zh-CN.md'):
            source = PLUGIN / name
            if source.is_dir():
                shutil.copytree(source, stage / name, ignore=shutil.ignore_patterns('*.map', '*.tsbuildinfo'))
            elif source.is_file():
                shutil.copy2(source, stage / name)
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        # Native plugin and Python consume identical canonical resources.
        resources = stage / 'resources'
        projection = PLUGIN / 'resources'
        if not projection.is_dir():
            raise SystemExit('Native build must generate canonical skill projection resources')
        shutil.copytree(projection, resources, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        canonical_files = {p.relative_to(ROOT / 'protocol/v2').as_posix(): 'sha256:' + digest(p) for p in sorted((ROOT / 'protocol/v2').rglob('*')) if p.is_file()}
        projected_files = {p.relative_to(resources / 'protocol').as_posix(): 'sha256:' + digest(p) for p in sorted((resources / 'protocol').rglob('*')) if p.is_file()}
        if canonical_files != projected_files:
            raise SystemExit('Native protocol projection is stale; rebuild the native plugin')
        projection_record = json.loads((resources / 'projection.json').read_text())
        protocol_digest = 'sha256:' + hashlib.sha256(b'protocol-bundle/v1\0' + json.dumps(canonical_files, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if projection_record['protocol_digest'] != protocol_digest or projection_record['skill_digest'] != 'sha256:' + digest(resources / 'SKILL.md'):
            raise SystemExit('Native MethodSpec/Skill projection digest is stale')
        wheels = stage / 'python/wheels'
        wheels.mkdir(parents=True)
        run([python, '-m', 'pip', 'wheel', '--wheel-dir', str(wheels), str(ROOT)], cwd=Path(temporary))
        core_wheels = list(wheels.glob('bugate_core-*.whl'))
        if len(core_wheels) != 1:
            raise SystemExit('Expected exactly one bugate-core wheel')
        validate_wheel(core_wheels[0])
        with zipfile.ZipFile(core_wheels[0]) as archive:
            prefix = 'bugate/resources/protocol/v2/'
            wheel_protocol = {name[len(prefix):]: 'sha256:' + hashlib.sha256(archive.read(name)).hexdigest()
                              for name in archive.namelist() if name.startswith(prefix) and not name.endswith('/')}
        if wheel_protocol != canonical_files:
            raise SystemExit('Protocol changed during build or wheel and native resources disagree')
        target = json.loads(run([python, '-c', "import json,platform,sys,sysconfig; print(json.dumps({'python':sys.version.split()[0],'platform':sys.platform,'machine':platform.machine(),'platform_tag':sysconfig.get_platform(),'soabi':sysconfig.get_config_var('SOABI')}))"], cwd=Path(temporary)))
        wheel_inventory = {'apiVersion': 'bugate.wheelhouse/v1', **target,
                           'files': {p.name: digest(p) for p in sorted(wheels.glob('*.whl'))}}
        (stage / 'python/inventory.json').write_text(json.dumps(wheel_inventory, indent=2) + '\n')
        # Resource inventory is independently checked by clean-install acceptance.
        inventory = {}
        for path in sorted(stage.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(stage).as_posix()
            check_name(relative)
            if path.suffix in {'.json', '.js', '.yml', '.yaml'}:
                content = path.read_text(encoding='utf-8')
                if str(ROOT) in content or '/Users/zhangliangchen/' in content or 'workspace:*' in content:
                    raise SystemExit(f'developer path/reference leaked into {relative}')
            inventory[relative] = digest(path)
        (stage / 'inventory.json').write_text(json.dumps({'apiVersion': 'bugate.bundle/v1', 'files': inventory}, indent=2) + '\n')
        metadata['files'].append('inventory.json')
        (stage / 'package.json').write_text(json.dumps(metadata, indent=2) + '\n')
        # Update inventory for final manifest bytes.
        inventory['package.json'] = digest(stage / 'package.json')
        (stage / 'inventory.json').write_text(json.dumps({'apiVersion': 'bugate.bundle/v1', 'files': inventory}, indent=2) + '\n')
        result = json.loads(run(['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', str(out)], cwd=stage))
        package = out / result[0]['filename']
        with tarfile.open(package, 'r:gz') as archive:
            for member in archive.getmembers():
                check_name(member.name)
                if not (member.isfile() or member.isdir()):
                    raise SystemExit(f'unsupported package member type: {member.name}')
        shutil.copy2(core_wheels[0], out / core_wheels[0].name)
        shutil.copy2(Path(__file__).with_name('install_dsh.py'), out / 'install_dsh.py')
        manifest = {'package': package.name, 'version': metadata['version'],
                    'core_wheel': core_wheels[0].name, 'dsh': metadata['engines']['dsh'],
                    'files': {p.name: digest(p) for p in (package, out / core_wheels[0].name, out / 'install_dsh.py')}}
        (out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        checksums = {**manifest['files'], 'build-manifest.json': digest(out / 'build-manifest.json')}
        (out / 'SHA256SUMS').write_text(''.join(f'{value}  {name}\n' for name, value in sorted(checksums.items())))
        return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    print(json.dumps(build(args.out_dir.resolve(), args.python), indent=2))
