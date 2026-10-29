"""Acceptance scope authority, immutable history and bounded activity admission.

This is a local managed-authoring boundary, not authentication against the user
who owns the filesystem. A host must capture real user events, never turn model
text into ScopeRequest. Structural checks cannot judge business necessity.
"""
from __future__ import annotations
from copy import deepcopy
from . import API_VERSION
from .errors import BugateError
from .protocol import load_json, object_digest, validate_schema, canonical
from .workspace import Workspace, within, locked, write_json, safe_id

COVERAGE_STATEMENT = 'This conclusion covers only the agreed acceptance scope.'


def _bound_draft(w,value,kind):
    expected={'apiVersion':API_VERSION,'kind':kind,'task_id':w.task['id'],'workspace_id':w.id}
    if kind=='ScopeContract':expected['schema_version']='2.1.0-dev.1'
    for field,actual in expected.items():
        if field in value and value[field]!=actual:
            raise BugateError('WORKSPACE_MISMATCH' if field in ('task_id','workspace_id') else 'PROTOCOL_MISMATCH','Explicit draft identity/version is unsupported; author a current task-bound '+kind,details=field)


def scope_ref(scope):
    return {k:scope[k] for k in ('id','revision','digest')} if scope else None


def plan_ref(facts):
    plans=facts.by_type.get('inventory',[])
    return {'id':plans[0]['id'],'digest':plans[0]['digest']} if len(plans)==1 else None


def active_scope(w, required=True):
    if not w.path('scope/active.json').exists():
        if required:raise BugateError('SCOPE_MISSING','Create a current ScopeContract with scope record before governed implementation or execution.')
        return None
    scope=validate_schema(w.read('scope/active.json'),'ScopeContract')
    if scope['task_id']!=w.task['id'] or scope['workspace_id']!=w.id:raise BugateError('WORKSPACE_MISMATCH','Scope belongs to another task')
    if scope['digest']!=object_digest(scope):raise BugateError('OBJECT_DIGEST_MISMATCH','Scope content differs from its digest')
    _validate_requests(w,scope)
    return scope


def record_request(task,input_file):
    """Only direct human CLI or host-captured user event; never a model-facing tool."""
    w=Workspace(task);value=deepcopy(load_json(within(w.root,input_file,must_exist=True)))
    if not isinstance(value,dict):raise BugateError('STRUCTURE_INVALID','User request must be an object')
    _bound_draft(w,value,'ScopeRequest')
    value.update(apiVersion=API_VERSION,kind='ScopeRequest',task_id=w.task['id'],workspace_id=w.id)
    actual=object_digest(value)
    if value.get('digest',actual)!=actual:raise BugateError('OBJECT_DIGEST_MISMATCH','User request digest is invalid')
    value['digest']=actual;validate_schema(value,'ScopeRequest')
    with locked(w.root):write_json(w.root,'.bugate/scope-requests/'+safe_id(value['id'])+'.json',value,immutable=True)
    return value


def _request(w,request_id):
    path=w.path('.bugate/scope-requests/'+safe_id(request_id)+'.json')
    if not path.exists():raise BugateError('SCOPE_AUTHORITY_MISSING','No direct user request receipt exists',details=request_id)
    value=validate_schema(load_json(path),'ScopeRequest')
    if value['workspace_id']!=w.id or value['task_id']!=w.task['id']:raise BugateError('WORKSPACE_MISMATCH','User request belongs to another task')
    if value['digest']!=object_digest(value):raise BugateError('OBJECT_DIGEST_MISMATCH','User request bytes changed')
    return value


def _citations(scope):
    yield from scope['request_refs'];yield from scope['change']['request_refs']
    for objective in scope['objectives']:yield from objective['request_refs']
    for requirement in scope['requirements']:yield from requirement['basis']['request_refs']
    for scenario in scope['scenarios']:yield from scenario['necessity']['request_refs']


def _validate_requests(w,scope):
    cited=set()
    for citation in _citations(scope):
        value=_request(w,citation['request_id']);cited.add(value['id'])
        if citation['quote'] not in value['message']:raise BugateError('SCOPE_AUTHORITY_INVALID','Scope citation is not verbatim text from its user event',details=value['id'])
        if scope['request_digests'].get(value['id'])!=value['digest']:raise BugateError('SCOPE_AUTHORITY_INVALID','Scope does not bind the exact direct user request',details=value['id'])
    if cited!=set(scope['request_digests']):raise BugateError('SCOPE_AUTHORITY_INVALID','Scope request digest set differs from its citations')


def record_scope(task,input_file,*,expected_digest=None):
    w=Workspace(task);value=deepcopy(load_json(within(w.root,input_file,must_exist=True)))
    if not isinstance(value,dict):raise BugateError('STRUCTURE_INVALID','Scope draft must be an object')
    _bound_draft(w,value,'ScopeContract')
    value.update(apiVersion=API_VERSION,kind='ScopeContract',task_id=w.task['id'],workspace_id=w.id,schema_version='2.1.0-dev.1')
    # Fill only identity/digests. Business goals, necessity and authorization are never invented.
    try:value['request_digests']={c['request_id']:_request(w,c['request_id'])['digest'] for c in _citations(value)}
    except (KeyError,TypeError) as exc:raise BugateError('STRUCTURE_INVALID','Scope draft requires objectives, requirements, scenarios and user request citations') from exc
    actual=object_digest(value)
    if value.get('digest',actual)!=actual:raise BugateError('OBJECT_DIGEST_MISMATCH','Scope digest is invalid')
    value['digest']=actual;validate_schema(value,'ScopeContract');_validate_requests(w,value)
    before=w.snapshot()
    with locked(w.root):
        if before!=w.snapshot():raise BugateError('CONCURRENT_CONFLICT','Workspace changed during scope authoring')
        old=active_scope(w,False)
        if old==value:return value
        if old:
            if {r['id'] for r in value['requirements']}!=set(w.profile['requirements']) or set(value['risk_ids'])!=set(w.profile['risks']):
                raise BugateError('SCOPE_PROFILE_CHANGE_REQUIRED','The locked Profile fixes requirement/risk denominators. Author an explicitly sourced new Profile version and initialize a new task; preserve this task and its failures.',details={'scope_ref':scope_ref(old),'current_profile':w.binding['profile'],'usage':'bugate init --project '+str(w.project)+' --task <new-task-id> --sut-profile <new-profile.json> --json'})
            if expected_digest!=old['digest']:raise BugateError('CONCURRENT_CONFLICT','Scope change requires the active expected digest',details=scope_ref(old))
            if value['id']!=old['id'] or value['revision']!=old['revision']+1 or value['change']['previous_digest']!=old['digest']:raise BugateError('SCOPE_CHANGE_INVALID','Scope change must preserve id, increment revision and reference its predecessor')
            new_requests={c['request_id'] for c in value['change']['request_refs']}-set(old['request_digests'])
            if not new_requests:raise BugateError('SCOPE_CHANGE_UNAUTHORIZED','Changing agreed scope requires a new direct user request; failure or time pressure is not authority')
            write_json(w.root,'.bugate/scope-history/'+old['digest'].split(':')[1]+'.json',old,immutable=True)
        else:
            if expected_digest is not None or value['revision']!=1 or value['change']['previous_digest'] is not None:raise BugateError('SCOPE_CHANGE_INVALID','Initial scope must have revision 1 and no predecessor')
            if {r['id'] for r in value['requirements']}!=set(w.profile['requirements']) or set(value['risk_ids'])!=set(w.profile['risks']):raise BugateError('SCOPE_DENOMINATOR_CHANGED','Initial scope must preserve all Profile requirements and risks; later changes require direct user authority')
        write_json(w.root,'scope/active.json',value)
    return value


def record_suggestion(task,input_file=None,*,value=None):
    w=Workspace(task);scope=active_scope(w)
    value=deepcopy(value if value is not None else load_json(within(w.root,input_file,must_exist=True)))
    if not isinstance(value,dict):raise BugateError('STRUCTURE_INVALID','Suggestion must be an object')
    _bound_draft(w,value,'Suggestion')
    if 'scope_ref' in value and value['scope_ref']!=scope_ref(scope):raise BugateError('SCOPE_STALE','Suggestion draft explicitly refers to a different scope')
    value.update(apiVersion=API_VERSION,kind='Suggestion',task_id=w.task['id'],workspace_id=w.id,scope_ref=scope_ref(scope),status='suggested')
    actual=object_digest(value)
    if value.get('digest',actual)!=actual:raise BugateError('OBJECT_DIGEST_MISMATCH','Suggestion digest is invalid')
    value['digest']=actual;validate_schema(value,'Suggestion')
    if not set(value['source_refs'])<= {s['id'] for s in w.profile['sources']}:raise BugateError('REFERENCE_MISSING','Suggestion cites undeclared sources')
    with locked(w.root):write_json(w.root,'suggestions/'+safe_id(value['id'])+'.json',value,immutable=True)
    return value


def suggestions(w):
    values=[]
    # Advisory corruption is reported as an advisory problem, never a completion dependency.
    for path in sorted(w.path('suggestions').glob('*.json')) if w.path('suggestions').exists() else []:
        try:
            value=validate_schema(load_json(within(w.root,path,must_exist=True)),'Suggestion')
            if value['task_id']!=w.task['id'] or value['workspace_id']!=w.id or value['digest']!=object_digest(value):raise BugateError('SUGGESTION_INVALID','Suggestion identity or digest is invalid')
            values.append(value)
        except BugateError as exc:values.append({'id':path.stem,'status':'invalid_advisory','code':exc.code})
    return values


def check_scope(f,*,plan=True,review=True):
    s=f.scope
    if not s:
        f.add('SCOPE_MISSING','scope','A ScopeContract is required for this claim','scope');return
    def index(items,label):
        result={}
        for item in items:
            if item['id'] in result:f.add('DUPLICATE_ID',item['id'],'Duplicate scope '+label,'scope')
            result[item['id']]=item
        return result
    goals=index(s['objectives'],'objective');requirements=index(s['requirements'],'requirement');flows=index(s['flows'],'flow');scenarios=index(s['scenarios'],'scenario')
    source_ids=set(f.sources)
    if set(requirements)!=set(f.w.profile['requirements']) or set(s['risk_ids'])!=set(f.w.profile['risks']):f.add('SCOPE_PROFILE_CHANGE_REQUIRED',s['id'],'Scope cannot alter the locked Profile denominator; use a new explicit Profile version and task','scope')
    if not set(s['source_refs'])<=source_ids:f.add('REFERENCE_MISSING',s['id'],'Scope source is not declared by Profile','scope')
    for assumption in s['assumptions']:
        if assumption['blocking']:f.add('SCOPE_AMBIGUITY',assumption['id'],'Unconfirmed assumption affects acceptance direction; ask the smallest clarification','scope',basis=[assumption['statement']])
    for exclusion in s['exclusions']:
        overlap=set(exclusion['requirement_ids'])&set(requirements)
        if overlap:f.add('SCOPE_EXCLUSION_CONFLICT',exclusion['id'],'An exclusion conflicts with an in-scope requirement','scope',basis=sorted(overlap))
    for flow in flows.values():
        if not set(flow['objective_ids'])<=set(goals) or (flow['branch_of'] is not None and (flow['branch_of'] not in flows or flow['branch_of']==flow['id'])):f.add('SCOPE_REFERENCE_INVALID',flow['id'],'Flow has no matching scope objective or parent branch','scope')
    for flow in flows.values():
        seen=set();node=flow
        while node and node['branch_of'] is not None:
            if node['id'] in seen:
                f.add('SCOPE_REFERENCE_INVALID',flow['id'],'Flow branch ancestry contains a cycle','scope');break
            seen.add(node['id']);node=flows.get(node['branch_of'])
    for requirement in requirements.values():
        if not set(requirement['objective_ids'])<=set(goals):f.add('SCOPE_REFERENCE_INVALID',requirement['id'],'Requirement does not prove a declared objective','scope')
        if not set(requirement['oracle_ids'])<=set(f.oracles):f.add('ORACLE_MISSING',requirement['id'],'Scope requirement lacks its referenced business Oracle','scope')
    for scenario in scenarios.values():
        rid=set(scenario['requirement_ids']);oids=set(scenario['oracle_ids']);flow=flows.get(scenario['flow_id'])
        if not rid<=set(requirements) or not flow:f.add('SCENARIO_UNRELATED',scenario['id'],'Required scenario must prove declared requirements and a flow','scope');continue
        allowed={o for r in rid for o in requirements[r]['oracle_ids']}
        if not oids<=allowed:f.add('SCOPE_REFERENCE_INVALID',scenario['id'],'Scenario Oracle is not an Oracle of its requirements','scope')
        if not {g for r in rid for g in requirements[r]['objective_ids']}<=set(flow['objective_ids']):f.add('SCENARIO_UNRELATED',scenario['id'],'Scenario flow does not serve its requirement objectives','scope')
    covered={r for c in scenarios.values() for r in c['requirement_ids']}
    for rid in sorted(set(requirements)-covered):f.add('SCOPE_SCENARIO_MISSING',rid,'In-scope requirement has no necessary scenario','scope')
    for goal in goals:
        if not any(goal in r['objective_ids'] for r in requirements.values()):f.add('SCOPE_SCENARIO_MISSING',goal,'Objective has no acceptance requirement','scope')
    for a in s['auxiliary']:
        if not set(a['related_scenario_ids'])<=set(scenarios) or (a['purpose']=='diagnosis' and not a['related_scenario_ids']):f.add('DIAGNOSTIC_BOUNDARY_INVALID',a['id'],'Diagnostic policy must name current scenarios and a finite run bound','scope')
    if plan:
        plans=f.by_type.get('inventory',[])
        if len(plans)!=1:f.add('PLAN_AMBIGUOUS','inventory','Exactly one current inventory is the acceptance plan','scope')
        bound={}
        for p in plans:
            if p['content']['scope_ref']!=scope_ref(s):f.add('SCOPE_STALE',p['id'],'Inventory belongs to another scope revision','scope')
            for case in p['content']['cases']:
                scenario=scenarios.get(case['scenario_id'])
                if not scenario:f.add('SCENARIO_OUT_OF_SCOPE',case['id'],'Record this extension as a suggestion; it cannot enter the required plan','scope',remediation='Remove the unaccepted extension from the executable inventory and record a non-executable suggestion.');continue
                if case['scenario_id'] in bound:f.add('SCENARIO_REDUNDANT',case['id'],'A required scenario already has a case; add another only after scope necessity review','scope',basis=[bound[case['scenario_id']]])
                bound[case['scenario_id']]=case['id']
                if set(case['oracle_ids'])!=set(scenario['oracle_ids']):f.add('SCOPE_ORACLE_WEAKENED',case['id'],'Case must retain every agreed business Oracle','scope')
                traced={r for pid in case['proposition_ids'] if pid in f.props for r in f.props[pid]['requirement_ids']}
                if not set(scenario['requirement_ids'])<=traced:f.add('SCENARIO_UNRELATED',case['id'],'Case propositions do not prove its scope requirements','scope')
                if set(case['expected_observations'])!=set(scenario['expected_evidence']):f.add('SCOPE_ORACLE_WEAKENED',case['id'],'Case expected evidence differs from the agreed scenario','scope')
        for sid in sorted(set(scenarios)-set(bound)):f.add('SCOPE_SCENARIO_MISSING',sid,'Required scope scenario is absent from current inventory','scope')
    if review:
        valid=False
        for a in f.by_type.get('semantic_review',[]):
            c=a['content']
            if c['scope_digest']!=s['digest'] or c['conclusion']!='accepted':continue
            if any(aid not in f.artifacts or f.artifacts[aid]['digest']!=h for aid,h in c['artifact_digests'].items()):continue
            decisions={d['scenario_id']:d for d in c['scope_decisions']}
            if set(decisions)!=set(scenarios) or any(d['necessity']!='accepted' or not set(d['source_refs'])<=source_ids for d in decisions.values()):continue
            if s['revision']>1 and c['change_authorization']!='accepted':continue
            required_artifacts={a['id'] for kind in (('business_brief','testability','inventory') if plan else ('business_brief',)) for a in f.by_type.get(kind,[])}
            if not required_artifacts<=set(c['artifact_digests']) or not set(c['source_refs'])<=source_ids:continue
            valid=True
        if not valid:f.add('SCOPE_SEMANTIC_REVIEW_MISSING',s['id'],'Current source-backed review must establish necessity and user authorization; populated fields are not sufficient','semantic_review',remediation='Review the exact user requests, scope changes, requirement/Oracle mapping and each scenario necessity against sources. Record uncertainty instead of claiming user consent.')


def check(task):
    from .assessment import Facts
    w=Workspace(task);f=Facts(w)
    for issue in f.findings:
        if issue['subject'] in f.evidence:issue['blocking']=False;issue['severity']='warning'
    check_scope(f)
    return {'valid':not any(x['blocking'] for x in f.findings),'scope_ref':scope_ref(f.scope),'plan_ref':plan_ref(f),'findings':f.findings,'suggestions':suggestions(w)}


def authorize(task,*,case_ids=(),activity_id=None,environment_id=None,expected_scope_digest=None,expected_plan_digest=None):
    from .assessment import Facts
    w=Workspace(task);f=Facts(w);s=f.scope;initial_findings=list(f.findings)
    if case_ids:check_scope(f)
    elif not s:f.add('SCOPE_MISSING','scope','Auxiliary execution requires a scope activity declaration','scope')
    sr=scope_ref(s);pr=plan_ref(f)
    environments=s['subject']['environment_ids'] if s else []
    if environment_id is None and len(environments)==1:environment_id=environments[0]
    if environment_id not in environments:f.add('SCOPE_ENVIRONMENT_INVALID',environment_id or 'environment','Select an environment explicitly permitted by the current scope','scope')
    if expected_scope_digest and (not sr or sr['digest']!=expected_scope_digest):f.add('SCOPE_STALE','execution','Execution expected another scope digest','scope')
    if expected_plan_digest and (not pr or pr['digest']!=expected_plan_digest):f.add('PLAN_STALE','execution','Execution expected another plan digest','scope')
    if case_ids:
        if activity_id:f.add('ACTIVITY_CLASSIFICATION_INVALID',activity_id,'An execution cannot be both required and auxiliary','scope')
        for cid in case_ids:
            if cid not in f.cases:f.add('SCENARIO_OUT_OF_SCOPE',cid,'Only current inventory cases can execute as required acceptance','scope')
        f.business();f.design();f.implementation()
    else:
        activity=next((a for a in s['auxiliary'] if a['id']==activity_id),None) if s else None
        if not activity:f.add('AUXILIARY_NOT_DECLARED',activity_id or 'execution','Select a declared auxiliary activity; suggestions cannot execute','scope')
        else:
            ledger=w.path('.bugate/activity-runs/'+s['digest'].split(':')[1]+'/'+safe_id(activity_id))
            runs=list(ledger.glob('*.json')) if ledger.exists() else []
            if len(runs)>=activity['max_runs']:f.add('DIAGNOSTIC_LIMIT_REACHED',activity_id,'Activity reached its agreed bound; preserve findings or report the blocking gap','scope')
            if activity['purpose']=='diagnosis':
                ids={c['id'] for c in f.cases.values() if c['scenario_id'] in activity['related_scenario_ids']}
                failures=[e for e in f.evidence.values() if e.get('execution') and e['source_type']=='host_observed' and e['execution'].get('scope_ref')==sr and e['execution'].get('plan_ref')==pr and not any(issue['subject']==e['id'] and issue['blocking'] for issue in f.findings) and any(c['id'] in ids and c['outcome'] in ('failed','error') for c in e['execution']['cases'])]
                if not failures:f.add('DIAGNOSTIC_FAILURE_MISSING',activity_id,'Diagnosis must relate to a recorded failure of a current required scenario','scope')
    if not case_ids:
        for issue in initial_findings:
            if issue['subject']!='scope':issue['blocking']=False;issue['severity']='warning'
    # Historical evidence is never a prerequisite for running a fresh repair.
    for issue in f.findings:
        if issue['subject'] in f.evidence:issue['blocking']=False;issue['severity']='warning'
    return {'allowed':not any(x['blocking'] for x in f.findings),'scope_ref':sr,'plan_ref':pr,'activity_id':activity_id,'environment_id':environment_id,'findings':f.findings}


def show(task):
    w=Workspace(task);requests=w.objects('.bugate/scope-requests','ScopeRequest')
    return {'scope':active_scope(w,False),'requests':requests,'suggestions':suggestions(w)}
