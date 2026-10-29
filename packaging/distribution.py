"""Finite v2 release surface validation; no retired runtime or SUT payloads."""
from __future__ import annotations
import ast
import configparser
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import tomllib
import zipfile

CORE_MODULES = {
    '__init__.py', '__main__.py', 'assessment.py', 'authoring.py', 'cli.py',
    'context.py', 'errors.py', 'evidence.py', 'integrations.py', 'protocol.py',
    'workspace.py', 'semantics.py',
}
RETIRED_MODULES = {'legacy', 'bugate_core', 'check_bugate_v13_semantics',
                   'memory_bus', 'role_governance', 'role_lineage',
                   'sdtd_orchestrator', 'bugate_update', 'bugate_init'}
RETIRED_ROOTS = {'.codex', '.claude', '.agents', '.shared', '.codex-plugin',
                 '.claude-plugin', 'hooks', 'bin', 'commands', 'context'}


def resource_files(root: Path) -> set[str]:
    manifest = json.loads((root / 'packaging/resources.json').read_text())
    assert manifest['apiVersion'] == 'bugate.distribution/v1'
    return set(manifest['protocol_files'])


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError(f'unsafe distribution path: {name}')
    for part in path.parts:
        if part in RETIRED_ROOTS | {'.git', '__pycache__', 'node_modules', '.env', '.DS_Store', 'sut', 'fixtures'}:
            raise ValueError(f'forbidden distribution member: {name}')
        if part.startswith('.env.') or part.endswith(('.pyc', '.pyo', '.pem', '.key', '.secret')):
            raise ValueError(f'credential/cache distribution member: {name}')


def check_python(name: str, data: bytes) -> None:
    tree = ast.parse(data, filename=name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name.split('.')[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = (node.module or '').split('.')
        else:
            continue
        if RETIRED_MODULES.intersection(names):
            raise ValueError(f'retired runtime import in {name}')
    # Native core must not invoke old runtime scripts through process APIs either.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(x + '.py' in node.value for x in RETIRED_MODULES):
                raise ValueError(f'retired runtime path in {name}')


def check_source(root: Path) -> dict:
    expected = resource_files(root)
    actual = {p.relative_to(root).as_posix() for p in (root / 'protocol/v2').rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError(f'canonical resource inventory differs: {sorted(actual ^ expected)}')
    configuration = tomllib.loads((root / 'pyproject.toml').read_text())
    mapping = configuration['tool']['hatch']['build']['targets']['wheel']['force-include']
    wanted = {f: 'bugate/resources/' + f for f in expected}
    if mapping != wanted:
        raise ValueError('wheel resource mapping must equal finite canonical inventory')
    modules = {p.name for p in (root / 'src/bugate').glob('*.py')}
    if modules != CORE_MODULES:
        raise ValueError(f'core module inventory differs: {sorted(modules ^ CORE_MODULES)}')
    for module in modules:
        check_python(module, (root / 'src/bugate' / module).read_bytes())
    # Check entry files, not entire .claude: a separately managed worktree may live there.
    for entry in ('.codex/hooks.json', '.claude/settings.json', 'hooks/hooks.json',
                  '.codex-plugin/plugin.json', '.claude-plugin/plugin.json', 'CLAUDE.md',
                  'bugate.config.yaml', 'src/bugate/legacy.py'):
        p = root / entry
        if p.exists() or p.is_symlink():
            raise ValueError(f'retired automatic entry remains: {entry}')
    return {'modules': len(modules), 'protocol_files': len(expected), 'status': 'PASS'}


def check_wheel(data: bytes, resources: set[str]) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError('duplicate wheel member')
        expected = {'bugate/' + n for n in CORE_MODULES} | {'bugate/resources/' + r for r in resources}
        actual = set()
        metadata_root = 'bugate_core-2.0.0a2.dist-info/'
        metadata_names = {metadata_root + n for n in ('METADATA', 'WHEEL', 'entry_points.txt', 'licenses/LICENSE', 'RECORD')}
        observed_metadata = {n for n in names if '.dist-info/' in n}
        if observed_metadata != metadata_names:
            raise ValueError('unexpected wheel metadata inventory')
        entries = configparser.ConfigParser(interpolation=None, strict=True)
        entries.read_string(archive.read(metadata_root + 'entry_points.txt').decode('utf-8'))
        if entries.sections() != ['console_scripts'] or dict(entries['console_scripts']) != {'bugate': 'bugate.cli:main'}:
            raise ValueError('wheel must expose only bugate = bugate.cli:main')
        for name in names:
            safe_name(name)
            if '.dist-info/' in name:
                continue
            actual.add(name)
            if name.endswith('.py'):
                check_python(name, archive.read(name))
        if actual != expected:
            raise ValueError(f'wheel differs from finite v2 inventory: {sorted(actual ^ expected)}')
    return {'members': len(names), 'status': 'PASS'}


def check_bundle(path: Path, resources: set[str]) -> dict:
    prefix_files = {'package.json', 'cordis.patch.yml', 'README.md', 'README.zh-CN.md',
                    'LICENSE', 'inventory.json', 'python/inventory.json',
                    'resources/SKILL.md', 'resources/projection.json', 'resources/compatibility.json'}
    with tarfile.open(path, 'r:gz') as archive:
        names = set()
        wheels = []
        for member in archive.getmembers():
            safe_name(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f'non-regular bundle member: {member.name}')
            if not member.isfile():
                continue
            if member.name in names:
                raise ValueError(f'duplicate bundle member: {member.name}')
            names.add(member.name)
            if not member.name.startswith('package/'):
                raise ValueError('unexpected bundle root')
            relative = member.name[len('package/'):]
            allowed = relative in prefix_files or relative in {'resources/protocol/' + r.removeprefix('protocol/v2/') for r in resources}
            allowed |= relative in {'lib/index.js', 'lib/bridge.js', 'lib/contracts.js', 'lib/index.d.ts', 'lib/bridge.d.ts', 'lib/contracts.d.ts'}
            allowed |= relative.startswith('python/wheels/') and relative.endswith('.whl') and len(PurePosixPath(relative).parts) == 3
            if not allowed:
                raise ValueError(f'undeclared bundle payload: {relative}')
            if relative.startswith('python/wheels/bugate_core-'):
                wheels.append(check_wheel(archive.extractfile(member).read(), resources))
        if len(wheels) != 1:
            raise ValueError('bundle must contain exactly one v2 core wheel')
    return {'members': len(names), 'core_wheels': wheels, 'status': 'PASS'}
