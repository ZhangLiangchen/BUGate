#!/usr/bin/env python3
"""Standalone offline-wheel bootstrap, with explicit ownership and bounded work.

This file and the .tgz are sufficient outside a source checkout. DSH itself must
already be installed. An existing venv without this installer's ownership record
is never adopted or modified. Partial owned environments are safe to retry.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--dsh-profile', required=True)
    parser.add_argument('--dsh', default='dsh')
    parser.add_argument('--dsh-home', type=Path)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--venv', required=True, type=Path, help='New or BUGate-owned bootstrap environment')
    args = parser.parse_args()
    package = args.package.resolve(strict=True)
    project = args.project.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='bugate-bootstrap-') as directory:
        stage = Path(directory)
        with tarfile.open(package, 'r:gz') as archive:
            entries = {}
            for member in archive.getmembers():
                if member.name in entries:
                    parser.error('duplicate archive entry')
                entries[member.name] = member
            index = entries.get('package/python/inventory.json')
            if index is None or not index.isfile() or index.size > 1024 * 1024:
                parser.error('missing or invalid wheel inventory')
            inventory = json.load(archive.extractfile(index))
            files = inventory.get('files', {})
            if not files:
                parser.error('empty wheelhouse')
            total = 0
            for name, expected in files.items():
                if Path(name).name != name or not name.endswith('.whl'):
                    parser.error('unsafe wheel filename')
                member = entries.get('package/python/wheels/' + name)
                if member is None or not member.isfile() or member.size > 100 * 1024 * 1024:
                    parser.error('missing or invalid wheel')
                total += member.size
                if total > 300 * 1024 * 1024:
                    parser.error('wheelhouse exceeds extraction limit')
                payload = archive.extractfile(member).read()
                if hashlib.sha256(payload).hexdigest() != expected:
                    parser.error('wheel checksum mismatch')
                (stage / name).write_bytes(payload)
        core = list(stage.glob('bugate_core-*.whl'))
        if len(core) != 1:
            parser.error('expected one core wheel')
        # Reuse the shipped stdlib-only installation boundary instead of silently
        # calling subprocess.run without cancellation/time/output limits here.
        with zipfile.ZipFile(core[0]) as wheel:
            integration_path = stage / 'integrations.py'
            integration_path.write_bytes(wheel.read('bugate/integrations.py'))
        spec = importlib.util.spec_from_file_location('bugate_bootstrap_integrations', integration_path)
        integration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(integration)
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        integration._check_wheel_target(args.python, inventory, stage, env)
        requested = args.venv.expanduser().absolute()
        if requested.is_symlink():
            parser.error('bootstrap environment cannot be a symlink')
        venv = requested.resolve()
        marker = venv / '.bugate-bootstrap.json'
        identity = {'owner': 'bugate-dsh-bootstrap/v1', 'project': str(project), 'venv': str(venv)}
        found = {}
        if venv.exists():
            if not marker.is_file() or marker.is_symlink():
                parser.error('existing environment is not owned by BUGate; choose a new --venv path')
            found = json.loads(marker.read_text())
            if any(found.get(key) != value for key, value in identity.items()):
                parser.error('bootstrap environment belongs to another project or owner')
        else:
            venv.mkdir(parents=True)
            integration._write(marker, {**identity, 'status': 'pending'})
        python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        try:
            if not python.exists():
                integration._run([args.python, '-m', 'venv', str(venv)], cwd=stage, env=env, timeout=180)
            package_digest = hashlib.sha256(package.read_bytes()).hexdigest()
            if found.get('status') != 'installed' or found.get('package_sha256') != package_digest:
                integration._write(marker, {**identity, 'status': 'pending', 'package_sha256': package_digest})
                integration._run([str(python), '-m', 'pip', 'install', '--no-index', '--find-links', str(stage),
                                  '--force-reinstall', 'bugate-core==2.0.0a2'], cwd=stage, env=env, timeout=180)
            integration._python_health(python, stage, env)
            integration._write(marker, {**identity, 'status': 'installed', 'package_sha256': package_digest})
            command = [str(python), '-m', 'bugate', 'setup', 'deepseek-harness', '--project', str(project),
                       '--dsh-profile', args.dsh_profile, '--package', str(package), '--dsh', args.dsh, '--json']
            if args.dsh_home:
                command += ['--dsh-home', str(args.dsh_home.resolve())]
            stdout, stderr = integration._run(command, cwd=stage, env=env, timeout=600)
            sys.stdout.write(stdout)
            sys.stderr.write(stderr)
            return 0
        except integration.IntegrationError as error:
            if error.details and 'stdout' in error.details:
                output = error.details['stdout']
                try:
                    parsed = json.loads(output)
                    if parsed.get('kind') == 'CommandResult':
                        sys.stdout.write(output)
                        return error.details['returncode']
                except (ValueError, AttributeError):
                    pass
            sys.stdout.write(json.dumps({'apiVersion': 'bugate.io/v2', 'kind': 'CommandResult', 'operation': 'bootstrap',
                                        'request_id': __import__('uuid').uuid4().hex, 'invocation': 'runtime_error', 'data': None,
                                        'errors': [{'code': error.code, 'message': error.message}]}) + '\n')
            return error.exit_code


if __name__ == '__main__':
    try:
        code = main()
    except Exception as error:
        code = getattr(error, 'exit_code', 1)
        print(json.dumps({'apiVersion': 'bugate.io/v2', 'kind': 'CommandResult', 'operation': 'bootstrap',
                          'request_id': __import__('uuid').uuid4().hex, 'invocation': 'invalid_input' if code == 2 else 'runtime_error',
                          'data': None, 'errors': [{'code': getattr(error, 'code', 'RUNTIME_FAILURE'), 'message': str(error), 'details': getattr(error, 'details', None)}]}))
    raise SystemExit(code)
