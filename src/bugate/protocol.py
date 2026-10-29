"""Canonical resource discovery and complete Draft 2020-12 validation."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from . import API_VERSION, PROTOCOL_VERSION, __version__
from .errors import BugateError

SCHEMAS = {name: ''.join('_'+c.lower() if c.isupper() else c for c in name).lstrip('_') for name in ('ProtocolBinding','TestTask','Artifact','Evidence','Claim','AssessmentRequest','AssessmentResult','Profile','PreparedProtocolContext','MethodSpec','CommandResult','ExecutionRecord')}

def resource_root() -> Path:
    installed = Path(__file__).parent / 'resources' / 'protocol' / 'v2'
    if installed.is_dir():
        return installed
    checkout = Path(__file__).resolve().parents[2] / 'protocol' / 'v2'
    if checkout.is_dir():
        return checkout
    raise BugateError('RESOURCE_MISSING', 'Canonical protocol resources are unavailable', exit_code=1)

def canonical(value) -> bytes:
    def check(v):
        if isinstance(v, float):
            raise BugateError('STRUCTURE_INVALID', 'Canonical protocol JSON does not permit floating-point numbers')
        if isinstance(v, int) and not isinstance(v, bool) and abs(v) > 9007199254740991:
            raise BugateError('STRUCTURE_INVALID', 'JSON integer exceeds cross-language safe range')
        if isinstance(v, str):
            try: v.encode('utf-8')
            except UnicodeEncodeError as exc: raise BugateError('STRUCTURE_INVALID', 'Unpaired Unicode surrogate') from exc
        if isinstance(v, dict):
            if not all(isinstance(k, str) for k in v): raise BugateError('STRUCTURE_INVALID','Object keys must be strings')
            for k, item in v.items(): check(k); check(item)
        if isinstance(v, list):
            for item in v: check(item)
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value, domain='object/v1') -> str:
    return 'sha256:' + hashlib.sha256(domain.encode()+b'\0'+canonical(value)).hexdigest()

def content_digest(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()

def object_digest(value) -> str:
    return digest({k:v for k,v in value.items() if k not in ('digest','request_digest')}, value.get('kind','object')+'/v1')

def load_json(path: Path):
    try:
        if path.stat().st_size > 8 * 1024 * 1024: raise BugateError('OUTPUT_LIMIT', f'JSON input exceeds 8 MiB: {path.name}')
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d: raise ValueError('duplicate JSON key '+k)
                d[k]=v
            return d
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid constant')))
    except BugateError: raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise BugateError('STRUCTURE_INVALID',f'Cannot read valid JSON: {path.name}',details=str(exc)) from exc

def schema(name):
    file=SCHEMAS.get(name,name)
    path=resource_root()/'schemas'/(file+'.schema.json')
    if not path.is_file(): raise BugateError('SCHEMA_UNKNOWN',f'Unknown schema: {name}')
    return load_json(path)

def validate_schema(value, kind=None):
    if not isinstance(value, dict): raise BugateError('STRUCTURE_INVALID','Expected a JSON object')
    selected = schema(kind or value.get('kind','unknown'))
    resources=[]
    for p in sorted((resource_root()/'schemas').glob('*.json')):
        s=load_json(p); resources.append((s['$id'],Resource.from_contents(s)))
    registry=Registry().with_resources(resources)
    errors=sorted(Draft202012Validator(selected,registry=registry).iter_errors(value),key=lambda e: str(list(e.path)))
    if errors:
        raise BugateError('STRUCTURE_INVALID','Schema validation failed',details=[{'path':'/'+ '/'.join(map(str,e.path)),'message':e.message} for e in errors[:30]])
    canonical(value)
    if value.get('kind')=='PreparedProtocolContext' and value['render']['bytes']!=len(value['render']['content'].encode('utf-8')):
        raise BugateError('STRUCTURE_INVALID','render.bytes differs from UTF-8 content length')
    return value

def protocol_identity():
    root=resource_root()
    files={p.relative_to(root).as_posix():content_digest(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
    return {'id':'bugate','version':PROTOCOL_VERSION,'digest':digest(files,'protocol-bundle/v1')}

def evaluator_identity():
    files={p.name:content_digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob('*.py')) if p.name!='integrations.py'}
    from .legacy import legacy_root
    legacy={p.name:content_digest(p.read_bytes()) for p in sorted((legacy_root()/'scripts').glob('*.py'))}
    return digest({'version':__version__,'files':files,'legacy':legacy},'evaluator/v1')

def methods():
    values=[validate_schema(load_json(p),'MethodSpec') for p in sorted((resource_root()/'methods').glob('*.json'))]
    if not values: raise BugateError('RESOURCE_MISSING','No MethodSpec resources',exit_code=1)
    return values
