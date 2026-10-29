"""Explicit task identity, bounded filesystem access, snapshots and atomic storage."""
from __future__ import annotations
import contextlib
import fcntl
import os
import re
import tempfile
from pathlib import Path
from .errors import BugateError
from .protocol import canonical, content_digest, digest, load_json, object_digest, protocol_identity, validate_schema, methods, evaluator_identity
from . import API_VERSION

ID_PATTERN=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')

def safe_id(value):
    if not isinstance(value,str) or not ID_PATTERN.fullmatch(value) or value in ('.','..'):
        raise BugateError('PATH_OUTSIDE_SCOPE','Invalid object identity')
    return value

def within(root:Path, value:str|Path, *, must_exist=False) -> Path:
    root=root.resolve()
    p=Path(value)
    candidate=p if p.is_absolute() else root/p
    try: rel=candidate.relative_to(root)
    except ValueError as exc: raise BugateError('PATH_OUTSIDE_SCOPE','Path is outside authorized root') from exc
    cur=root
    for part in rel.parts:
        if part in ('..',''): raise BugateError('PATH_OUTSIDE_SCOPE','Parent traversal is not allowed')
        cur=cur/part
        if cur.is_symlink(): raise BugateError('PATH_SYMLINK','Symbolic links are not authorized in task paths')
    try: candidate.resolve().relative_to(root)
    except ValueError as exc: raise BugateError('PATH_OUTSIDE_SCOPE','Resolved path escapes root') from exc
    if must_exist and not candidate.is_file(): raise BugateError('REFERENCE_MISSING',f'Missing file: {rel.as_posix()}')
    return candidate

def atomic_write(root:Path, value:str|Path, data:bytes, *, expected=None, immutable=False):
    p=within(root,value)
    p.parent.mkdir(parents=True,exist_ok=True)
    p=within(root,value)
    if p.exists():
        old=p.read_bytes()
        if immutable:
            if old==data:return
            raise BugateError('CONCURRENT_CONFLICT','Identity already has different immutable content')
        if expected is not None and content_digest(old)!=expected:
            raise BugateError('CONCURRENT_CONFLICT','Compare-and-swap content changed')
    elif expected is not None:
        raise BugateError('CONCURRENT_CONFLICT','Compare-and-swap target disappeared')
    fd,tmp=tempfile.mkstemp(prefix='.pending-',dir=p.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
        # Caller holds the workspace lock; external concurrent writers are outside this transaction domain.
        within(root,value)
        os.replace(tmp,p)
        dfd=os.open(p.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def write_json(root,value,obj,**kwargs):atomic_write(root,value,canonical(obj)+b'\n',**kwargs)

@contextlib.contextmanager
def locked(root):
    p=within(root,'.bugate/write.lock');p.parent.mkdir(parents=True,exist_ok=True)
    flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(p,flags,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise BugateError('CONCURRENT_CONFLICT','Another writer owns the task transaction') from exc
        yield
    finally:fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

class Workspace:
    def __init__(self,task):
        requested=Path(task).absolute()
        self.root=requested.resolve()
        if not self.root.is_dir():raise BugateError('TASK_NOT_FOUND','Task directory does not exist')
        self.task=validate_schema(load_json(within(self.root,'task.json',must_exist=True)),'TestTask')
        self.project=Path(self.task['project_root']).resolve()
        within(self.project,requested)
        if str(self.root)!=self.task['task_root']:raise BugateError('WORKSPACE_MISMATCH','Task moved or workspace path differs from identity')
        self.profile=validate_schema(load_json(within(self.root,'profile.json',must_exist=True)),'Profile')
        lock=within(self.root,'.bugate/protocol.lock.json')
        if not lock.exists():raise BugateError('BINDING_MISSING','Exact ProtocolBinding is missing')
        self.binding=validate_schema(load_json(lock),'ProtocolBinding')
        expected=protocol_identity()
        if self.binding['protocol']!=expected:raise BugateError('PROTOCOL_MISMATCH','Protocol version or digest differs from lock',details={'expected':self.binding['protocol'],'actual':expected})
        profile_identity={'id':self.profile['id'],'version':self.profile['version'],'digest':object_digest(self.profile)}
        if self.binding['profile']!=profile_identity:raise BugateError('PROFILE_MISMATCH','Profile content differs from lock')
        ident=digest({'project':str(self.project),'task':str(self.root),'task_id':self.task['id'],'protocol':expected,'profile':profile_identity},'workspace-identity/v1')
        if self.binding['workspace_id']!=ident or self.task['workspace_id']!=ident:raise BugateError('WORKSPACE_MISMATCH','Task, binding and filesystem identities disagree')
        if self.binding['methods']!=[m['id'] for m in methods()]:raise BugateError('PROTOCOL_MISMATCH','Method binding differs from canonical protocol')
        for field in ('goal','requirements','risks','delivery_criteria','unknowns'):
            if self.task[field]!=self.profile[field]:raise BugateError('TASK_SCOPE_MISMATCH','Task fields differ from the locked Profile',details=field)
        if self.task['source_ids']!=[s['id'] for s in self.profile['sources']]:raise BugateError('TASK_SCOPE_MISMATCH','Task sources differ from locked Profile')
        self.id=ident

    def path(self,relative,must_exist=False):return within(self.root,relative,must_exist=must_exist)
    def read(self,relative):return load_json(self.path(relative,True))
    def objects(self,folder,kind):
        p=self.path(folder)
        if not p.exists():return []
        result=[];seen=set()
        for item in sorted(p.glob('*.json')):
            value=validate_schema(load_json(self.path(item,True)),kind)
            if value['id'] in seen:raise BugateError('DUPLICATE_ID',f'Duplicate {kind} identity: '+value['id'])
            seen.add(value['id']);result.append(value)
        return result
    def snapshot(self):
        values={'binding-file':content_digest(self.path('.bugate/protocol.lock.json',True).read_bytes())}
        if load_json(self.path('.bugate/protocol.lock.json',True))!=self.binding or self.read('profile.json')!=self.profile or self.read('task.json')!=self.task:
            raise BugateError('CONCURRENT_CONFLICT','Task, Profile or Binding changed after workspace resolution')
        for p in sorted(self.root.rglob('*')):
            rel=p.relative_to(self.root)
            if rel.parts[0] in ('claims','assessments','.bugate') or any(x.startswith('.pending-') for x in rel.parts):continue
            if p.is_symlink():raise BugateError('PATH_SYMLINK',f'Task contains a symbolic link: {rel}')
            if p.is_file():
                if p.suffix=='.json' and rel.parts[0] not in ('artifacts','evidence'):
                    try:
                        value=load_json(p)
                        if isinstance(value,dict) and value.get('kind') in ('Claim','AssessmentRequest','AssessmentResult','PreparedProtocolContext'):continue
                    except BugateError:pass
                values[rel.as_posix()]=content_digest(p.read_bytes())
        for source in self.profile['sources']:
            p=within(self.project,source['path'],must_exist=True)
            values['source:'+source['id']]=content_digest(p.read_bytes())
        return digest({'files':values,'binding':self.binding,'evaluator':evaluator_identity()},'workspace-snapshot/v1')


def initialize(project,task_id,profile_file):
    project=Path(project).resolve();safe_id(task_id)
    if not project.is_dir():raise BugateError('TASK_NOT_FOUND','Project directory does not exist')
    profile=validate_schema(load_json(Path(profile_file).resolve()),'Profile')
    source_ids=[s['id'] for s in profile['sources']]
    if len(set(source_ids))!=len(source_ids):raise BugateError('DUPLICATE_ID','Duplicate Profile source id')
    for source in profile['sources']:
        p=within(project,source['path'],must_exist=True)
        if content_digest(p.read_bytes())!=source['digest']:raise BugateError('REFERENCE_DIGEST_MISMATCH','Profile source bytes do not match their digest',details=source['id'])
    root=within(project,Path('test-task')/task_id)
    existed=(root/'task.json').exists()
    root.mkdir(parents=True,exist_ok=True)
    protocol=protocol_identity();pi={'id':profile['id'],'version':profile['version'],'digest':object_digest(profile)}
    ident=digest({'project':str(project),'task':str(root),'task_id':task_id,'protocol':protocol,'profile':pi},'workspace-identity/v1')
    binding={'apiVersion':API_VERSION,'kind':'ProtocolBinding','id':'binding-'+task_id,'protocol':protocol,'profile':pi,'methods':[m['id'] for m in methods()],'resolved_from':'.bugate/protocol.lock.json','workspace_id':ident}
    task={'apiVersion':API_VERSION,'kind':'TestTask','id':task_id,'workspace_id':ident,'project_root':str(project),'task_root':str(root),'goal':profile['goal'],'requirements':profile['requirements'],'risks':profile['risks'],'delivery_criteria':profile['delivery_criteria'],'source_ids':source_ids,'unknowns':profile['unknowns']}
    with locked(root):
        for file,value in [('task.json',task),('profile.json',profile),('.bugate/protocol.lock.json',binding)]:
            validate_schema(value);write_json(root,file,value,immutable=True)
        for folder in ('artifacts','evidence','claims','assessments','logs'):within(root,folder).mkdir(exist_ok=True)
    return {'task_root':str(root),'task':task,'binding':binding,'created':not existed}


def discover(project):
    project=Path(project).resolve()
    candidates=[str(p.parent) for p in sorted((project/'test-task').glob('*/task.json')) if not p.is_symlink()]
    if not candidates:raise BugateError('TASK_NOT_FOUND','No BUGate task in project')
    if len(candidates)!=1:raise BugateError('TASK_AMBIGUOUS','Multiple tasks require explicit selection',details={'candidates':candidates})
    return Workspace(candidates[0])


def execution_input_manifest(w):
    """All applicable input bytes, including newly appearing files; explicit output roots excluded."""
    values={'@protocol':w.binding['protocol']['digest'],'@profile':w.binding['profile']['digest'],'@evaluator':evaluator_identity()}
    for source in w.profile['sources']:
        values['@source/'+source['id']]=content_digest(within(w.project,source['path'],must_exist=True).read_bytes())
    for p in sorted(w.root.rglob('*')):
        rel=p.relative_to(w.root)
        if rel.parts[0] in ('evidence','claims','assessments','logs','.bugate') or any(x in ('__pycache__','.pytest_cache','.mypy_cache','.ruff_cache') for x in rel.parts):continue
        if p.is_symlink():raise BugateError('PATH_SYMLINK','Execution input contains symbolic link')
        if not p.is_file():continue
        if p.suffix=='.json':
            try:
                value=load_json(p)
                if isinstance(value,dict) and (value.get('kind') in ('Claim','AssessmentRequest','AssessmentResult','PreparedProtocolContext') or (rel.parts[0]=='artifacts' and value.get('artifact_type') in ('execution_report','knowledge','diagnosis'))):continue
            except BugateError:pass
        values[rel.as_posix()]=content_digest(p.read_bytes())
    return values


FILESYSTEM_LIMITS={'max_files':2048,'max_directories':2048,'max_total_bytes':33554432}
FILESYSTEM_EXCLUSIONS={'exact':['.bugate/write.lock'],'patterns':[]}
FILESYSTEM_RUNTIME_DIRECTORIES=['.bugate/runtime']

def filesystem_manifest(w):
    """Finite, raw-byte execution-world witness. Not an assessment input or quality rule."""
    import hashlib
    files=[];directories=[];sources=[];total=0
    def entry(path,label):
        nonlocal total
        size=path.stat().st_size
        if total+size>FILESYSTEM_LIMITS['max_total_bytes']:raise BugateError('OUTPUT_LIMIT','Execution-world filesystem witness exceeds its byte bound',exit_code=1)
        hasher=hashlib.sha256();count=0
        with path.open('rb') as stream:
            while True:
                block=stream.read(65536)
                if not block:break
                count+=len(block)
                if total+count>FILESYSTEM_LIMITS['max_total_bytes']:raise BugateError('OUTPUT_LIMIT','Execution-world filesystem witness exceeds its byte bound',exit_code=1)
                hasher.update(block)
        if count!=size:raise BugateError('CONCURRENT_CONFLICT','Filesystem file size changed during execution-world verification')
        total+=count
        return {'path':label,'digest':'sha256:'+hasher.hexdigest(),'bytes':count}
    for path in sorted(w.root.rglob('*')):
        rel=path.relative_to(w.root).as_posix()
        within(w.root,path)
        if any(rel==excluded or rel.startswith(excluded+'/') for excluded in FILESYSTEM_RUNTIME_DIRECTORIES):continue
        if path.is_dir():
            directories.append(rel)
            if len(directories)>FILESYSTEM_LIMITS['max_directories']:raise BugateError('OUTPUT_LIMIT','Execution-world directory count exceeds its bound',exit_code=1)
        elif path.is_file():
            if rel in FILESYSTEM_EXCLUSIONS['exact'] or any(re.fullmatch(pattern,rel) for pattern in FILESYSTEM_EXCLUSIONS['patterns']):continue
            if len(files)+len(sources)>=FILESYSTEM_LIMITS['max_files']:raise BugateError('OUTPUT_LIMIT','Execution-world file count exceeds its bound',exit_code=1)
            files.append(entry(path,rel))
        else:raise BugateError('PATH_OUTSIDE_SCOPE','Execution-world witness requires regular files and directories')
    for source in w.profile['sources']:
        if len(files)+len(sources)>=FILESYSTEM_LIMITS['max_files']:raise BugateError('OUTPUT_LIMIT','Execution-world file count exceeds its bound',exit_code=1)
        path=within(w.project,source['path'],must_exist=True)
        sources.append({'id':source['id'],**entry(path,str(path))})
    return {'version':1,'task_root':str(w.root),'task_files':files,'task_directories':directories,'sources':sources,'excluded_runtime_files':FILESYSTEM_EXCLUSIONS,'excluded_runtime_directories':FILESYSTEM_RUNTIME_DIRECTORIES,'limits':FILESYSTEM_LIMITS}
