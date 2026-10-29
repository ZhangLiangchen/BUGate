"""Versioned JSON process contract. stdout contains exactly one JSON envelope."""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from . import API_VERSION,PROTOCOL_VERSION,__version__
from .errors import BugateError

class Parser(argparse.ArgumentParser):
    def error(self,message):raise BugateError('INVALID_ARGUMENT',message)

def parser():
    p=Parser(prog='bugate');sub=p.add_subparsers(dest='command',required=True)
    def common(name, task=True):
        c=sub.add_parser(name)
        if task:c.add_argument('--task',required=True)
        c.add_argument('--json',action='store_true');return c
    common('version',False)
    init=common('init',False);init.add_argument('--project',required=True);init.add_argument('--task',required=True);init.add_argument('--sut-profile',required=True)
    prot=common('protocol',False);ps=prot.add_subparsers(dest='protocol_command',required=True);prep=ps.add_parser('prepare');prep.add_argument('--task',required=True);prep.add_argument('--max-bytes',type=int,default=8000);prep.add_argument('--json',action='store_true')
    common('status');common('validate');a=common('assess');a.add_argument('--claim',required=True);a.add_argument('--expected-snapshot')
    ar=common('artifact',False);ars=ar.add_subparsers(dest='artifact_command',required=True);record=ars.add_parser('record');record.add_argument('--task',required=True);record.add_argument('--input',required=True);record.add_argument('--expected-digest');record.add_argument('--json',action='store_true')
    e=common('evidence',False);es=e.add_subparsers(dest='evidence_command',required=True);er=es.add_parser('record');er.add_argument('--task',required=True);er.add_argument('--input',required=True);er.add_argument('--json',action='store_true')
    x=common('execution',False);xs=x.add_subparsers(dest='execution_command',required=True);run=xs.add_parser('run');run.add_argument('--task',required=True);run.add_argument('--run-id',required=True);run.add_argument('--case',dest='case_ids',action='append',default=[]);run.add_argument('--report');run.add_argument('--agent-id',default='cli');run.add_argument('--session-id',default='cli');run.add_argument('--call-id');run.add_argument('--parent-call-id');run.add_argument('--root-call-id');run.add_argument('--timeout',type=float,default=60);run.add_argument('--max-output',type=int,default=1048576);run.add_argument('--json',action='store_true');run.add_argument('argv',nargs=argparse.REMAINDER)
    for command in ('setup','remove'):
        c=common(command,False);c.add_argument('host',choices=['deepseek-harness']);c.add_argument('--project',required=True);c.add_argument('--dsh-profile',required=True);c.add_argument('--dsh',default='dsh');c.add_argument('--dsh-home')
        if command=='setup':c.add_argument('--package');c.add_argument('--python');c.add_argument('--task-path')
        else:c.add_argument('--rollback',action='store_true')
    d=common('doctor',False);d.add_argument('target',choices=['integrations']);d.add_argument('--project',required=True);d.add_argument('--dsh-profile');d.add_argument('--dsh',default='dsh');d.add_argument('--dsh-home')
    return p

def dispatch(a):
    from .protocol import protocol_identity
    from .workspace import initialize
    from .context import prepare
    from .assessment import status,validation,assess
    from .evidence import record,execute
    if a.command=='version':return {'core_version':__version__,'protocol_version':PROTOCOL_VERSION,'api_version':API_VERSION,'protocol_digest':protocol_identity()['digest']},0
    if a.command=='init':return initialize(a.project,a.task,a.sut_profile),0
    if a.command=='protocol':return prepare(a.task,a.max_bytes),0
    if a.command=='status':return status(a.task),0
    if a.command=='validate':
        data=validation(a.task);return data,0 if data['valid'] else 3
    if a.command=='assess':
        data=assess(a.task,a.claim,expected_snapshot=a.expected_snapshot);return data,0 if data['verdict']=='satisfied' else 3
    if a.command=='artifact':
        from .authoring import record_artifact
        return record_artifact(a.task,a.input,expected_digest=a.expected_digest),0
    if a.command=='evidence':return record(a.task,a.input),0
    if a.command=='execution':return execute(a.task,a.argv[1:] if a.argv and a.argv[0]=='--' else a.argv,run_id=a.run_id,case_ids=a.case_ids,report=a.report,agent_id=a.agent_id,session_id=a.session_id,call_id=a.call_id,parent_call_id=a.parent_call_id,root_call_id=a.root_call_id,timeout=a.timeout,max_output=a.max_output),0
    from . import integrations
    if a.command=='setup':return integrations.setup(a.project,a.dsh_profile,package=a.package,dsh=a.dsh,dsh_home=a.dsh_home,python=a.python,task_path=a.task_path),0
    if a.command=='remove':return integrations.remove(a.project,a.dsh_profile,dsh=a.dsh,dsh_home=a.dsh_home,rollback=a.rollback),0
    if a.command=='doctor':return integrations.doctor(a.project,dsh_profile=a.dsh_profile,dsh=a.dsh,dsh_home=a.dsh_home),0
    raise BugateError('INVALID_ARGUMENT','Unknown command')

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv);operation=' '.join(args[:2]) if args else 'unknown';request_id=str(uuid.uuid4());data=None;errors=[];invocation='completed';rc=0
    try:
        parsed=parser().parse_args(args);operation=parsed.command
        for key in ('protocol_command','artifact_command','evidence_command','execution_command'):
            if getattr(parsed,key,None):operation+='.'+getattr(parsed,key)
        data,rc=dispatch(parsed)
    except KeyboardInterrupt:
        rc=130;invocation='cancelled';errors=[{'code':'CANCELLED','message':'Operation cancelled','details':None}]
    except Exception as exc:
        if hasattr(exc,'code') and hasattr(exc,'exit_code'):
            rc=exc.exit_code;errors=[{'code':exc.code,'message':str(exc),'details':getattr(exc,'details',None)}]
        else:rc=1;errors=[{'code':'DEPENDENCY_MISSING' if isinstance(exc,ModuleNotFoundError) else 'RUNTIME_FAILURE','message':str(exc),'details':type(exc).__name__}]
        invocation={2:'invalid_input',124:'timeout',130:'cancelled'}.get(rc,'runtime_error')
    result={'apiVersion':API_VERSION,'kind':'CommandResult','operation':operation,'request_id':request_id,'invocation':invocation,'data':data,'errors':errors}
    print(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    return rc
