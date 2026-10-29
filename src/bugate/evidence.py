"""Evidence import keeps provenance; managed capture observes a real process."""
from __future__ import annotations
import contextlib
import os
import selectors
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path
from . import API_VERSION
from .errors import BugateError
from .protocol import canonical,content_digest,object_digest,load_json,validate_schema
from .workspace import Workspace,within,locked,write_json,atomic_write,safe_id,execution_input_manifest

LIMITATIONS=['Local managed process observation; not tamper-proof against a same-permission filesystem owner.','A framework result does not prove the correctness of its business oracle.']

def record(task,input_file):
    w=Workspace(task);value=load_json(within(w.root,input_file,must_exist=True))
    if not isinstance(value,dict):raise BugateError('STRUCTURE_INVALID','Evidence input must be an object')
    if value.get('source_type')=='host_observed':raise BugateError('EVIDENCE_SOURCE_FORBIDDEN','Imported evidence cannot claim host_observed provenance')
    for ref in value.get('references',[]):
        if isinstance(ref,dict) and ref.get('path') and ref.get('digest') is None:ref['digest']=content_digest(within(w.root,ref['path'],must_exist=True).read_bytes())
    if 'digest' not in value:value['digest']=object_digest(value)
    validate_schema(value,'Evidence')
    if value['source_type']=='host_observed':raise BugateError('EVIDENCE_SOURCE_FORBIDDEN','Imported evidence cannot claim host_observed provenance')
    if value['task_id']!=w.task['id'] or value['workspace_id']!=w.id:raise BugateError('WORKSPACE_MISMATCH','Evidence is for another task/workspace')
    if value['digest']!=object_digest(value):raise BugateError('OBJECT_DIGEST_MISMATCH','Evidence digest does not match content')
    for ref in value['references']:
        p=within(w.root,ref['path'],must_exist=True)
        if content_digest(p.read_bytes())!=ref['digest']:raise BugateError('REFERENCE_DIGEST_MISMATCH','Evidence reference differs')
    with locked(w.root):write_json(w.root,'evidence/'+safe_id(value['id'])+'.json',value,immutable=True)
    return value


def _junit(path,selectors_to_cases):
    if not path.is_file():return None,[],{'tests':0,'passed':0,'failed':0,'errors':0,'skipped':0,'xfail':0}
    if path.stat().st_size>8*1024*1024:raise BugateError('OUTPUT_LIMIT','JUnit report exceeds 8 MiB',exit_code=1)
    try:root=ET.fromstring(path.read_bytes())
    except ET.ParseError as exc:raise BugateError('TEST_REPORT_INVALID','Invalid JUnit report',exit_code=1) from exc
    if root.tag not in ('testsuite','testsuites'):raise BugateError('TEST_REPORT_INVALID','Expected a JUnit testsuite/testsuites root',exit_code=1)
    stats={'tests':0,'passed':0,'failed':0,'errors':0,'skipped':0,'xfail':0};cases=[];seen=set()
    for test in root.iter('testcase'):
        selector='.'.join(x for x in [test.get('classname',''),test.get('name','')] if x)
        if not selector or selector in seen:raise BugateError('TEST_REPORT_INVALID','Missing or duplicate JUnit test selector',exit_code=1)
        seen.add(selector);outcome='passed'
        if test.find('failure') is not None:outcome='failed'
        elif test.find('error') is not None:outcome='error'
        elif test.find('skipped') is not None:
            skipped=test.find('skipped');outcome='xfail' if 'xfail' in ((skipped.get('type','')+' '+skipped.get('message','')).lower()) else 'skipped'
        stats['tests']+=1;stats['errors' if outcome=='error' else outcome]+=1
        if selector in selectors_to_cases:cases.append({'id':selectors_to_cases[selector],'selector':selector,'outcome':outcome})
    for suite in root.iter():
        if suite.tag not in ('testsuite','testsuites'):continue
        children=list(suite.iter('testcase'))
        actual={'tests':len(children),'failures':sum(c.find('failure') is not None for c in children),'errors':sum(c.find('error') is not None for c in children),'skipped':sum(c.find('skipped') is not None for c in children)}
        for key,count in actual.items():
            if key in suite.attrib:
                try:declared=int(suite.attrib[key])
                except ValueError as exc:raise BugateError('TEST_REPORT_INVALID','JUnit aggregate is not an integer',exit_code=1) from exc
                if declared!=count:raise BugateError('TEST_REPORT_INVALID','JUnit aggregate contradicts testcase observations',exit_code=1,details={'field':key,'declared':declared,'observed':count})
    return {'path':str(path),'digest':content_digest(path.read_bytes())},cases,stats


def execute(task,argv,*,run_id,case_ids=(),report=None,agent_id='cli',session_id='cli',call_id=None,parent_call_id=None,root_call_id=None,timeout=60,max_output=1048576):
    w=Workspace(task);safe_id(run_id)
    if not argv or not all(isinstance(x,str) and x and '\x00' not in x for x in argv):raise BugateError('INVALID_ARGUMENT','Execution argv must be a non-empty array of strings')
    if timeout<=0 or timeout>3600 or max_output<1 or max_output>16*1024*1024:raise BugateError('INVALID_ARGUMENT','Execution bounds are invalid')
    eid='run-'+run_id;destination='evidence/'+eid+'.json';intent='.bugate/executions/'+run_id+'.json'
    command={'argv':list(argv),'case_ids':list(case_ids),'report':report,'workspace_id':w.id}
    with locked(w.root):
        if w.path(intent).exists():
            previous=w.read(intent)
            if previous['command']!=command:raise BugateError('CONCURRENT_CONFLICT','Run id was used for a different execution')
            if w.path(destination).exists():return validate_schema(w.read(destination),'Evidence')
            raise BugateError('EXECUTION_FAILED_NO_EFFECT' if previous.get('state')=='failed_no_effect' else 'EXECUTION_OUTCOME_UNKNOWN','Prior execution has no completed evidence; retain original run id and diagnose before retry',exit_code=1)
    implementations={};mapping={}
    from .assessment import Facts
    facts=Facts(w)
    for cid in case_ids:
        if cid not in facts.cases:raise BugateError('REFERENCE_MISSING','Execution case is absent from inventory',details=cid)
        case=facts.cases[cid];path=within(w.root,case['implementation']['path'],must_exist=True)
        actual=content_digest(path.read_bytes())
        if actual!=case['implementation']['digest']:raise BugateError('REFERENCE_DIGEST_MISMATCH','Implementation changed before execution',details=cid)
        implementations[case['implementation']['path']]=actual
        selector=case['test_selector']
        if selector in mapping:raise BugateError('DUPLICATE_ID','Multiple cases share a JUnit selector')
        mapping[selector]=cid
    report_path=within(w.root,report) if report else None
    if report_path and report_path.exists() and not w.path(destination).exists():raise BugateError('TEST_REPORT_EXISTS','A run must produce a fresh report path; refusing pre-existing report')
    with locked(w.root):
        if w.path(intent).exists():
            previous=w.read(intent)
            if previous['command']!=command:raise BugateError('CONCURRENT_CONFLICT','Run id was used for a different execution')
            if w.path(destination).exists():return validate_schema(w.read(destination),'Evidence')
            raise BugateError('EXECUTION_FAILED_NO_EFFECT' if previous.get('state')=='failed_no_effect' else 'EXECUTION_OUTCOME_UNKNOWN','Prior execution has no completed evidence; retain original run id and diagnose before retry',exit_code=1)
        snapshot=w.snapshot()
        write_json(w.root,intent,{'command':command,'input_snapshot':snapshot},immutable=True)
    input_manifest=execution_input_manifest(w)
    started=datetime.now(timezone.utc).isoformat();buffers={'stdout':bytearray(),'stderr':bytearray()};stop_reason=None;proc=None
    old_handlers={}
    def cancel(signum,frame):
        nonlocal stop_reason
        stop_reason='cancelled'
    try:
        for sig in (signal.SIGTERM,signal.SIGINT):
            try:old_handlers[sig]=signal.signal(sig,cancel)
            except ValueError:break
        try:proc=subprocess.Popen(list(argv),cwd=w.root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        except (OSError,ValueError) as exc:
            with locked(w.root):write_json(w.root,intent,{'command':command,'input_snapshot':snapshot,'state':'failed_no_effect'})
            raise BugateError('DEPENDENCY_MISSING','Cannot start execution process',exit_code=1,details=str(exc)) from exc
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ,'stdout');sel.register(proc.stderr,selectors.EVENT_READ,'stderr');deadline=time.monotonic()+timeout;term_at=None
        while sel.get_map() or proc.poll() is None:
            if stop_reason is None and time.monotonic()>=deadline:stop_reason='timeout'
            if stop_reason and term_at is None:
                with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGTERM)
                term_at=time.monotonic()
            if term_at and time.monotonic()-term_at>0.5:
                with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
            for key,_ in sel.select(0.05):
                data=os.read(key.fileobj.fileno(),65536)
                if not data:sel.unregister(key.fileobj);continue
                remaining=max_output-sum(len(x) for x in buffers.values())
                buffers[key.data].extend(data[:max(0,remaining)])
                if len(data)>remaining:stop_reason='output_limit'
        proc.wait();sel.close();proc.stdout.close();proc.stderr.close()
    finally:
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
            proc.wait()
        for sig,handler in old_handlers.items():signal.signal(sig,handler)
    finished=datetime.now(timezone.utc).isoformat();refs={}
    with locked(w.root):
        for stream,data in buffers.items():
            rel='logs/'+run_id+'.'+stream+'.log';atomic_write(w.root,rel,bytes(data),immutable=True);refs[stream]={'path':rel,'digest':content_digest(bytes(data))}
    report_ref=None;cases=[];stats={'tests':0,'passed':0,'failed':0,'errors':0,'skipped':0,'xfail':0};report_error=None
    if report_path:
        try:
            report_path=within(w.root,report)
            report_ref,cases,stats=_junit(report_path,mapping)
            if report_ref:report_ref['path']=str(report_path.relative_to(w.root))
        except BugateError as exc:report_error=exc.code
    result_status= 'cancelled' if stop_reason=='cancelled' else 'timeout' if stop_reason=='timeout' else 'blocked' if stop_reason or report_error else 'failed' if proc.returncode or stats['failed'] or stats['errors'] else 'passed'
    ex={'run_id':run_id,'agent_id':agent_id,'session_id':session_id,'call_id':call_id or run_id,'parent_call_id':parent_call_id,'root_call_id':root_call_id or call_id or run_id,'argv':list(argv),'cwd':str(w.root),'started_at':started,'finished_at':finished,'status':result_status,'exit_code':proc.returncode,'stdout':refs['stdout'],'stderr':refs['stderr'],'report':report_ref,'statistics':stats,'requested_case_ids':list(case_ids),'cases':cases,'input_snapshot':snapshot,'input_manifest':input_manifest,'implementation_digests':implementations,'collector':'bugate.execution/v1'}
    value={'apiVersion':API_VERSION,'kind':'Evidence','id':eid,'task_id':w.task['id'],'workspace_id':w.id,'source_type':'host_observed','evidence_type':'test_execution' if report else 'runtime_probe','description':'Managed execution capture'+(' ('+str(stop_reason or report_error)+')' if stop_reason or report_error else ''),'references':[refs['stdout'],refs['stderr']]+([report_ref] if report_ref else []),'applicability':{'run_id':run_id,'current_task':True,'limitations':LIMITATIONS},'execution':ex}
    value['digest']=object_digest(value);validate_schema(value)
    with locked(w.root):
        # Publish collector receipt first; only a complete Evidence is discoverable by evaluators.
        write_json(w.root,'.bugate/observations/'+eid+'.json',{'evidence_digest':value['digest'],'workspace_id':w.id,'collector':'bugate.execution/v1'},immutable=True)
        write_json(w.root,destination,value,immutable=True)
    if stop_reason=='timeout':raise BugateError('EXECUTION_TIMEOUT','Managed process timed out and was terminated',exit_code=124,details={'evidence_id':eid})
    if stop_reason=='cancelled':raise BugateError('EXECUTION_CANCELLED','Managed process was cancelled and terminated',exit_code=130,details={'evidence_id':eid})
    if stop_reason=='output_limit':raise BugateError('OUTPUT_LIMIT','Managed process exceeded output budget and was terminated',exit_code=1,details={'evidence_id':eid})
    if report_error:raise BugateError(report_error,'Managed execution produced an invalid framework report',exit_code=1,details={'evidence_id':eid})
    return value
