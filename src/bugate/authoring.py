"""Deterministic artifact authoring with optimistic concurrency and preserved history."""
from __future__ import annotations
from copy import deepcopy
from .errors import BugateError
from .protocol import content_digest,load_json,object_digest,validate_schema
from .workspace import Workspace,within,locked,write_json,safe_id

def record_artifact(task,input_file,*,expected_digest=None):
    w=Workspace(task);input_path=within(w.root,input_file,must_exist=True);value=deepcopy(load_json(input_path))
    if not isinstance(value,dict):raise BugateError('STRUCTURE_INVALID','Artifact input must be an object')
    safe_id(value.get('id'));target='artifacts/'+value['id']+'.json'
    if value.get('kind')!='Artifact':raise BugateError('STRUCTURE_INVALID','Artifact authoring requires kind Artifact')
    # Draft authoring may ask the deterministic core to fill file/review digests.
    content=value.get('content',{})
    if isinstance(content,dict) and value.get('artifact_type')=='inventory':
        for case in content.get('cases',[]):
            ref=case.get('implementation',{})
            if isinstance(ref,dict) and ref.get('path'):
                path=within(w.root,ref['path'])
                if path.is_file():
                    actual=content_digest(path.read_bytes())
                    if ref.get('digest') is None:ref['digest']=actual
                    elif ref['digest']!=actual:raise BugateError('REFERENCE_DIGEST_MISMATCH','Implementation reference changed')
    if isinstance(content,dict) and value.get('artifact_type')=='semantic_review':
        for aid,expected in list(content.get('artifact_digests',{}).items()):
            existing=validate_schema(w.read('artifacts/'+safe_id(aid)+'.json'),'Artifact')
            actual=object_digest(existing)
            if existing['digest']!=actual:raise BugateError('OBJECT_DIGEST_MISMATCH','Reviewed artifact has invalid digest')
            if expected is None:content['artifact_digests'][aid]=actual
            elif expected!=actual:raise BugateError('SEMANTIC_REVIEW_STALE','Review does not refer to current artifact bytes')
    actual=object_digest(value)
    if 'digest' in value and value['digest']!=actual:raise BugateError('OBJECT_DIGEST_MISMATCH','Artifact digest differs from actual content')
    value['digest']=actual;validate_schema(value,'Artifact')
    sources={s['id'] for s in w.profile['sources']}
    for ref in value['source_refs']:
        if ref not in sources:raise BugateError('REFERENCE_MISSING','Artifact source is absent from Profile',details=ref)
    existing_artifacts=w.objects('artifacts','Artifact')
    existing_ids={a['id'] for a in existing_artifacts}|{e['id'] for e in w.objects('evidence','Evidence')}|sources|{value['id']}
    for artifact in existing_artifacts+[value]:
        for group in ('propositions','oracles','cases'):
            existing_ids.update(item['id'] for item in artifact['content'].get(group,[]))
    for ref in value['relations']:
        if ref not in existing_ids:raise BugateError('REFERENCE_MISSING','Artifact relation is absent',details=ref)
    before=w.snapshot()
    with locked(w.root):
        if w.snapshot()!=before:raise BugateError('CONCURRENT_CONFLICT','Workspace changed during artifact authoring')
        path=w.path(target)
        if path.exists():
            old=validate_schema(load_json(path),'Artifact')
            if old['digest']!=object_digest(old):raise BugateError('OBJECT_DIGEST_MISMATCH','Current artifact digest is invalid')
            if old==value:return value
            if expected_digest!=old['digest']:raise BugateError('CONCURRENT_CONFLICT','Updating an artifact requires its current expected_digest',details={'current_digest':old['digest']})
            write_json(w.root,'.bugate/artifact-history/'+value['id']+'/'+old['digest'].split(':')[1]+'.json',old,immutable=True)
        elif expected_digest is not None:raise BugateError('CONCURRENT_CONFLICT','Expected artifact does not exist')
        write_json(w.root,target,value)
    return value
