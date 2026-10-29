"""Deterministic fact assessment, not an Agent controller or semantic oracle."""
from __future__ import annotations
from pathlib import Path
from . import API_VERSION, __version__
from .errors import BugateError
from .semantics import check_sources_and_placeholders, quality_models
from .protocol import digest, object_digest, content_digest, load_json, validate_schema, evaluator_identity, canonical, quality_rules
from .workspace import Workspace, within, write_json, locked, safe_id, execution_input_manifest, filesystem_manifest


def finding(code,subject,message,dimension='integrity',*,blocking=True,basis=None,remediation='Correct the referenced input and reassess the current snapshot.'):
    return {'code':code,'subject':str(subject),'dimension':dimension,'severity':'error' if blocking else 'warning','blocking':blocking,'message':message,'basis':basis or [],'remediation':remediation}

class Facts:
    def __init__(self,w:Workspace):
        self.w=w;self.findings=[];self.artifacts={};self.evidence={}
        for folder,kind,container in [('artifacts','Artifact',self.artifacts),('evidence','Evidence',self.evidence)]:
            try:
                for o in w.objects(folder,kind):
                    container[o['id']]=o
                    if o['digest']!=object_digest(o):self.add('OBJECT_DIGEST_MISMATCH',o['id'],'Object content differs from its registered digest')
            except BugateError as exc:self.add(exc.code,folder,exc.message,basis=[str(exc.details)])
        self.by_type={}
        for a in self.artifacts.values():self.by_type.setdefault(a['artifact_type'],[]).append(a)
        self.sources={s['id']:s for s in w.profile['sources']}
        self.props={};self.oracles={};self.cases={};self.reviewed=set()
        for source in self.sources.values():self.check_ref(source,w.project,'source:'+source['id'])
        for a in self.artifacts.values():
            for ref in a['source_refs']:
                if ref not in self.sources:self.add('REFERENCE_MISSING',a['id'],'Artifact source is not declared by Profile',basis=[ref])
        for e in self.evidence.values():
            if e['task_id']!=w.task['id'] or e['workspace_id']!=w.id:self.add('WORKSPACE_MISMATCH',e['id'],'Evidence belongs to a different task or workspace')
            for ref in e['references']:self.check_ref(ref,w.root,e['id'])
            ex=e.get('execution')
            if ex:
                if ex['input_manifest']!=execution_input_manifest(w):self.add('EXECUTION_INPUTS_CHANGED',e['id'],'Execution inputs or their path set changed; rerun against current design and source bytes','execution')
                for ref in [ex['stdout'],ex['stderr']]+([ex['report']] if ex['report'] else []):self.check_ref(ref,w.root,e['id'])
                for name,h in ex['implementation_digests'].items():self.check_ref({'path':name,'digest':h},w.root,e['id'])
            if e['source_type']=='host_observed':
                receipt=w.path('.bugate/observations/'+safe_id(e['id'])+'.json')
                if not receipt.exists() or load_json(receipt)!= {'evidence_digest':e['digest'],'workspace_id':w.id,'collector':'bugate.execution/v1'}:
                    self.add('EVIDENCE_UNTRUSTED',e['id'],'No matching local collector receipt; source labels do not authenticate execution')
                if not ex or ex.get('collector')!='bugate.execution/v1':self.add('EVIDENCE_UNTRUSTED',e['id'],'Host observation requires a managed execution record')
        self._indexes()
        check_sources_and_placeholders(self)
        identities=set(self.sources)|set(self.artifacts)|set(self.evidence)|set(self.props)|set(self.oracles)|set(self.cases)
        for a in self.artifacts.values():
            for ref in a['relations']:
                if ref not in identities:self.add('REFERENCE_MISSING',a['id'],'Artifact relation points to an absent identity',basis=[ref])
        for a in self.by_type.get('diagnosis',[]):
            for hypothesis in a['content']['hypotheses']:
                for ref in hypothesis['supporting_evidence']+hypothesis['contradicting_evidence']:
                    if ref not in self.evidence:self.add('REFERENCE_MISSING',a['id'],'Diagnosis evidence is absent',basis=[ref])
                if hypothesis['status']=='confirmed' and not hypothesis['supporting_evidence']:self.add('EVIDENCE_UNTRUSTED',a['id'],'A confirmed diagnosis requires supporting evidence')

    def add(self,code,subject,message,dimension='integrity',**kw):self.findings.append(finding(code,subject,message,dimension,**kw))
    def check_ref(self,ref,root,subject,dimension='integrity'):
        try:
            p=within(root,ref['path'],must_exist=True)
            if content_digest(p.read_bytes())!=ref['digest']:self.add('REFERENCE_DIGEST_MISMATCH',subject,'Referenced bytes changed',dimension,basis=[ref['path']])
        except BugateError as exc:self.add(exc.code,subject,exc.message,dimension,basis=[ref['path']])
    def unique(self,index,items,subject):
        for item in items:
            if item['id'] in index:self.add('DUPLICATE_ID',item['id'],'Duplicate semantic identity',basis=[subject])
            index[item['id']]=item
    def _indexes(self):
        for a in self.by_type.get('business_brief',[]):
            self.unique(self.props,a['content']['propositions'],a['id']);self.unique(self.oracles,a['content']['oracles'],a['id'])
        for a in self.by_type.get('inventory',[]):self.unique(self.cases,a['content']['cases'],a['id'])
        for a in self.by_type.get('semantic_review',[]):
            c=a['content'];valid=c['conclusion']=='accepted'
            for src in c['source_refs']:
                if src not in self.sources:self.add('REFERENCE_MISSING',a['id'],'Reviewer source is undeclared','semantic_review',basis=[src]);valid=False
            for aid,expected in c['artifact_digests'].items():
                reviewed = self.artifacts.get(aid)
                if reviewed and self.w.profile.get('quality_requirements', {}).get('independent_review') and (not reviewed.get('producer') or reviewed['producer'] == c['reviewer']):
                    self.add('REVIEW_INDEPENDENCE_MISSING', a['id'], 'Profile requires a named producer distinct from the reviewer', 'semantic_review', basis=[aid]);valid=False
                if aid not in self.artifacts or self.artifacts[aid]['digest']!=expected:
                    self.add('SEMANTIC_REVIEW_STALE',a['id'],'Review does not bind current artifact content','semantic_review',basis=[aid]);valid=False
            if valid:self.reviewed.update(c['artifact_digests'])

    def business(self):
        if not self.props:self.add('BUSINESS_UNDERSTANDING_MISSING','business_brief','No source-backed business propositions','business_understanding')
        requirements=set(self.w.task['requirements']);covered=set()
        for p in self.props.values():
            covered.update(p['requirement_ids'])
            if p['classification']!='fact':self.add('BUSINESS_SEMANTICS_UNRESOLVED',p['id'],'Proposition remains inference or unknown','business_understanding')
            for rid in p['requirement_ids']:
                if rid not in requirements:self.add('REFERENCE_MISSING',p['id'],'Requirement is outside frozen task scope','business_understanding',basis=[rid])
            for src in p['source_refs']:
                if src not in self.sources:self.add('REFERENCE_MISSING',p['id'],'Proposition source is undeclared','business_understanding',basis=[src])
        for missing in sorted(requirements-covered):self.add('REQUIREMENT_UNCOVERED',missing,'Frozen task requirement has no proposition','business_understanding')
        for o in self.oracles.values():
            for pid in o['proposition_ids']:
                if pid not in self.props:self.add('REFERENCE_MISSING',o['id'],'Oracle references missing proposition','business_understanding',basis=[pid])
            for src in o['source_refs']:
                if src not in self.sources:self.add('REFERENCE_MISSING',o['id'],'Oracle source is undeclared','business_understanding',basis=[src])
        for pid in self.props:
            if not any(pid in o['proposition_ids'] for o in self.oracles.values()):self.add('ORACLE_MISSING',pid,'Proposition has no observable business oracle','business_understanding',remediation='Define the expected business result, observation, deadline, prerequisites and source.')
        for a in self.by_type.get('business_brief',[]):
            if a['content']['unknowns']:self.add('BUSINESS_SEMANTICS_UNRESOLVED',a['id'],'Business brief contains unresolved unknowns','business_understanding',basis=a['content']['unknowns'])
        if self.w.task['unknowns'] or self.w.profile['unknowns']:self.add('BUSINESS_SEMANTICS_UNRESOLVED','task','Task/Profile has unresolved unknowns','business_understanding')
        self.require_review(('business_brief',))
        quality_models(self, 'business_understanding')

    def require_review(self,types):
        for t in types:
            for a in self.by_type.get(t,[]):
                if a['id'] not in self.reviewed:self.add('SEMANTIC_REVIEW_MISSING',a['id'],'Current source-backed semantic review is required','semantic_review',remediation='Provide a reviewer artifact bound to current artifact digests; structural validity is not business correctness.')

    def design(self):
        decisions={}; mapped_oracles=set(); decision_pairs={}
        for a in self.by_type.get('testability',[]):
            if a['content']['gaps']:self.add('TESTABILITY_GAP',a['id'],'Unresolved observability or prerequisite gaps','testability',basis=a['content']['gaps'])
            for d in a['content']['decisions']:
                pid=d['proposition_id']
                if (pid,d['oracle_id']) in decision_pairs:self.add('DUPLICATE_ID',pid,'Duplicate proposition/oracle testability decision','testability')
                decision_pairs[(pid,d['oracle_id'])]=d['layer']; decisions.setdefault(pid,set()).add(d['layer']); mapped_oracles.add(d['oracle_id'])
                if pid not in self.props or d['oracle_id'] not in self.oracles:self.add('REFERENCE_MISSING',pid,'Testability proposition or oracle missing','testability')
                elif pid not in self.oracles[d['oracle_id']]['proposition_ids']:self.add('ORACLE_BINDING_INVALID',pid,'Testability oracle belongs to a different proposition','testability',basis=[d['oracle_id']])
                if d['layer'] not in ('unit','component','integration','api','e2e','static','probe'):self.add('TESTABILITY_GAP',pid,'Unknown test layer is not an evaluated strategy','testability')
        for pid in self.props:
            if pid not in decisions:self.add('TESTABILITY_GAP',pid,'No explicit observation/layer decision','testability')
        for oid in sorted(set(self.oracles)-mapped_oracles):self.add('TESTABILITY_ORACLE_UNMAPPED',oid,'Business oracle has no explicit testability decision','testability')
        if not self.cases:self.add('CASE_MISSING','inventory','No concrete test cases','test_design')
        covered_p=set();covered_r=set();covered_o=set();negative=set()
        for c in self.cases.values():
            covered_p.update(c['proposition_ids']);covered_r.update(c['risk_ids']);covered_o.update(c['oracle_ids'])
            if c['negative']:negative.update(c['proposition_ids'])
            for pid in c['proposition_ids']:
                for oid in c['oracle_ids']:
                    if oid in self.oracles and pid in self.oracles[oid]['proposition_ids'] and decision_pairs.get((pid,oid)) != c['layer']:
                        self.add('LAYER_DECISION_MISMATCH',c['id'],'Case test layer differs from its proposition/oracle testability decision','test_design',basis=[pid,oid])
                if pid not in self.props:self.add('REFERENCE_MISSING',c['id'],'Case proposition is missing','test_design',basis=[pid])
            for oid in c['oracle_ids']:
                if oid not in self.oracles:self.add('ORACLE_MISSING',c['id'],'Case oracle is missing','test_design',basis=[oid])
                elif not set(self.oracles[oid]['proposition_ids']) & set(c['proposition_ids']):self.add('ORACLE_BINDING_INVALID',c['id'],'Case oracle does not cover its proposition','test_design',basis=[oid])
            oracle_props={pid for oid in c['oracle_ids'] if oid in self.oracles for pid in self.oracles[oid]['proposition_ids']}
            for pid in sorted(set(c['proposition_ids'])-oracle_props):self.add('ORACLE_BINDING_INVALID',c['id'],'Case proposition is not covered by any referenced oracle','test_design',basis=[pid])
            for rid in c['risk_ids']:
                if rid not in self.w.task['risks']:self.add('REFERENCE_MISSING',c['id'],'Case risk is outside frozen scope','test_design',basis=[rid])
        for p in sorted(set(self.props)-covered_p):self.add('PROPOSITION_UNCOVERED',p,'Proposition has no case','test_design')
        for o in sorted(set(self.oracles)-covered_o):self.add('ORACLE_UNCOVERED',o,'Oracle has no case','test_design')
        for r in sorted(set(self.w.task['risks'])-covered_r):self.add('RISK_UNCOVERED',r,'Frozen risk has no case','test_design')
        for p in sorted(set(self.props)-negative):self.add('NEGATIVE_COVERAGE_MISSING',p,'No negative/boundary case for proposition','test_design')
        for a in self.by_type.get('inventory',[]):
            if set(a['content']['requirement_scope'])!=set(self.w.task['requirements']) or set(a['content']['risk_scope'])!=set(self.w.task['risks']):self.add('SCOPE_DENOMINATOR_CHANGED',a['id'],'Inventory scope differs from frozen task requirements/risks','test_design')
        self.require_review(('testability','inventory'))
        quality_models(self, 'test_design')

    def implementation(self):
        if not self.cases:self.add('CASE_MISSING','inventory','Implementation requires case/oracle mapping','implementation')
        for c in self.cases.values():
            if not c['implementation'].get('digest'):self.add('IMPLEMENTATION_MISSING',c['id'],'Implementation has not been bound by content digest','implementation')
            else:self.check_ref(c['implementation'],self.w.root,c['id'],'implementation')

    def executions(self,claim):
        runs=[]
        for eid in claim['evidence_ids']:
            e=self.evidence.get(eid)
            if not e:continue
            if e['source_type']!='host_observed' or not e['applicability']['current_task'] or e['task_id']!=self.w.task['id'] or e['workspace_id']!=self.w.id:
                self.add('EVIDENCE_UNTRUSTED',eid,'Imported/model/historical evidence is not current host execution','execution');continue
            ex=e['execution']
            if not ex:continue
            if any(f['subject']==eid and f['blocking'] for f in self.findings):continue
            runs.append(ex)
            observed={c['id'] for c in ex['cases']}
            if set(ex['requested_case_ids'])!=observed:self.add('EXECUTION_CASE_MISSING',eid,'Framework report does not account for every requested case','execution',basis=sorted(set(ex['requested_case_ids'])-observed))
            if ex['status'] in ('cancelled','timeout','blocked'):self.add('EXECUTION_INCOMPLETE',eid,'Execution did not reach a complete test result','execution',basis=[ex['status']])
            if ex['statistics']['tests']==0:self.add('ZERO_TESTS',eid,'No test cases were executed','execution')
            elif ex['statistics']['passed']+ex['statistics']['failed']+ex['statistics']['errors']==0:self.add('ALL_TESTS_SKIPPED',eid,'All observed cases were skipped or expected failures','execution')
            if not ex['report']:self.add('TEST_REPORT_MISSING',eid,'A framework report is required for execution claims','execution')
        if not runs:self.add('EXECUTION_NOT_RUN',claim['id'],'No applicable managed execution evidence','execution')
        return runs

    def verify_requirements(self,claim,runs):
        requirements=claim['requirement_ids']
        if not requirements:self.add('CLAIM_SCOPE_EMPTY',claim['id'],'Requirement verification must name requirements','verification')
        for rid in requirements:
            if rid not in self.w.task['requirements']:self.add('REFERENCE_MISSING',rid,'Requirement is not in frozen task scope','verification');continue
            pids={pid for pid,p in self.props.items() if rid in p['requirement_ids']}
            required={c['id'] for c in self.cases.values() if set(c['proposition_ids']) & pids}
            if not required:self.add('CASE_MISSING',rid,'No cases trace this requirement','verification')
            for cid in sorted(required):
                observations=[(run,c) for run in runs for c in run['cases'] if c['id']==cid and c['selector']==self.cases[cid]['test_selector']]
                if not observations:self.add('CASE_NOT_EXECUTED',cid,'No managed report observation for required case','verification')
                elif any(c['outcome']!='passed' or run['status']!='passed' for run,c in observations):self.add('REQUIREMENT_NOT_VERIFIED',cid,'Observed failure, skip, cancellation or uncertain run cannot verify requirement','verification')

    def reports(self,claim,*,include_report=True,include_knowledge=True):
        reports=self.by_type.get('execution_report',[]) if include_report else []
        if include_report and not reports:self.add('EXECUTION_REPORT_MISSING','execution_report','Task delivery requires an execution report','report')
        for a in reports:
            for eid in a['content']['evidence_ids']:
                if eid not in self.evidence or eid not in claim['evidence_ids']:self.add('REFERENCE_MISSING',a['id'],'Report evidence is missing from claim','report',basis=[eid])
            if set(a['content']['case_ids'])!=set(self.cases):self.add('REPORT_COVERAGE_INCOMPLETE',a['id'],'Report must account for all inventory cases','report')
            observed={c['id'] for e in self.evidence.values() if e['id'] in claim['evidence_ids'] and e['source_type']=='host_observed' and e.get('execution') and e['applicability']['current_task'] and not any(f['subject']==e['id'] and f['blocking'] for f in self.findings) for c in e['execution']['cases']}
            for cid in sorted(set(self.cases)-observed):self.add('CASE_NOT_EXECUTED',cid,'Delivery report lists a case without applicable managed execution','report')
            failures=any(e.get('execution') and e['execution']['status']=='failed' for e in self.evidence.values() if e['id'] in claim['evidence_ids'])
            if failures and a['content']['outcome']=='passed':self.add('FAILURE_HIDDEN',a['id'],'Report marks failed execution as passed','report')
        if include_knowledge and not self.by_type.get('knowledge'):self.add('KNOWLEDGE_MISSING','knowledge','Task delivery requires sourced knowledge notes','knowledge')
        for a in self.by_type.get('knowledge',[]):
            for entry in a['content']['entries']:
                for ref in entry['source_refs']:
                    if ref not in self.sources and ref not in self.evidence:self.add('REFERENCE_MISSING',a['id'],'Knowledge source is unavailable','knowledge',basis=[ref])


def evaluate(w:Workspace,claim):
    validate_schema(claim,'Claim')
    if claim['task_id']!=w.task['id'] or claim['workspace_id']!=w.id:raise BugateError('WORKSPACE_MISMATCH','Claim belongs to another task/workspace')
    current=w.snapshot()
    f=Facts(w)
    selected_evidence=set(claim['evidence_ids'])
    for issue in f.findings:
        if issue['subject'] in f.evidence and issue['subject'] not in selected_evidence:
            issue['blocking']=False;issue['severity']='warning'
    if claim['workspace_snapshot']!=current:f.add('ASSESSMENT_STALE',claim['id'],'Claim input snapshot differs from current workspace')
    for aid in claim['artifact_ids']:
        if aid not in f.artifacts:f.add('REFERENCE_MISSING',claim['id'],'Claim artifact is unavailable',basis=[aid])
    for eid in claim['evidence_ids']:
        if eid not in f.evidence:f.add('REFERENCE_MISSING',claim['id'],'Claim evidence is unavailable',basis=[eid])
    if claim['known_gaps'] or claim['open_questions']:f.add('CLAIM_HAS_GAPS',claim['id'],'Claim declares unresolved gaps',basis=claim['known_gaps']+claim['open_questions'])
    dims=quality_rules()['claim_dimensions'][claim['claim_type']]
    if claim['claim_type']=='task_delivery_complete':
        dims=tuple({'business_understanding':'business_understanding','test_design':'test_design','implementation':'implementation','execution':'execution','report':'report','knowledge':'knowledge'}[x] for x in w.task['delivery_criteria'])
        if any(d in dims for d in ('test_design','implementation')):dims=tuple(set(dims)|{'business_understanding','testability','test_design'})
    if 'business_understanding' in dims:f.business()
    if 'test_design' in dims:f.design()
    if 'implementation' in dims:f.implementation()
    runs=f.executions(claim) if 'execution' in dims else []
    if 'verification' in dims:f.verify_requirements(claim,runs)
    if 'report' in dims or 'knowledge' in dims:f.reports(claim,include_report='report' in dims,include_knowledge='knowledge' in dims)
    # A claim cannot omit the inputs whose semantics were used to satisfy it.
    types=set()
    if 'business_understanding' in dims:types|={'business_brief','semantic_review','domain_model','state_model'}
    if 'test_design' in dims:types|={'testability','inventory','semantic_review','adversarial_review','dimension_matrix'}
    if 'report' in dims:types.add('execution_report')
    if 'knowledge' in dims:types.add('knowledge')
    for a in f.artifacts.values():
        if a['artifact_type'] in types and a['id'] not in claim['artifact_ids']:f.add('CLAIM_INPUT_MISSING',claim['id'],'Used artifact is not referenced by claim',basis=[a['id']])
    deduplicated={canonical(item):item for item in f.findings}
    findings=sorted(deduplicated.values(),key=lambda x:(x['dimension'],x['code'],x['subject'],x['message']))
    semantic=any(x in dims for x in ('business_understanding','test_design'))
    runtime='execution' in dims
    blocked=[x for x in findings if x['blocking']]
    inconclusive=bool(blocked) and all(x['code'] in ('SEMANTIC_REVIEW_MISSING','BUSINESS_SEMANTICS_UNRESOLVED','EXECUTION_NOT_RUN','EVIDENCE_UNTRUSTED') for x in blocked)
    verdict='inconclusive' if inconclusive else 'unsatisfied' if blocked else 'satisfied'
    def dimension(name,applicable,limitations):
        problems=[x for x in findings if x['blocking'] and (x['dimension']==name if name!='deterministic' else x['dimension'] not in ('semantic_review','execution','verification'))]
        return {'status':('failed' if problems else 'passed') if applicable else 'not_applicable','applicable':applicable,'basis':sorted({x['code']+':'+x['subject'] for x in problems}) if problems else ['canonical-rules/v2'] if applicable else ['claim-type applicability/v2'],'limitations':limitations}
    request={'apiVersion':API_VERSION,'kind':'AssessmentRequest','id':'request-'+claim['id'],'task_id':w.task['id'],'workspace_id':w.id,'binding':w.binding['protocol'],'profile':w.binding['profile'],'claim':claim,'workspace_snapshot':current,'evaluator_version':__version__,'rules_digest':evaluator_identity()}
    request['request_digest']=object_digest(request);validate_schema(request)
    result={'apiVersion':API_VERSION,'kind':'AssessmentResult','id':'assessment-'+claim['id'],'task_id':w.task['id'],'workspace_id':w.id,'claim_id':claim['id'],'claim_type':claim['claim_type'],'claim_digest':object_digest(claim),'evaluator_version':__version__,'request_digest':request['request_digest'],'workspace_snapshot':current,'verdict':verdict,'dimensions':{'deterministic':dimension('deterministic',True,['Schema and content hashes establish consistency, not the truth of supplied facts.']),'semantic_review':dimension('semantic_review',semantic,['Source-backed reviewer opinion is not a mathematical proof.']),'runtime_validation':dimension('execution',runtime,['Managed local-process observation; same-permission filesystem owners can alter local evidence.'])},'findings':findings,'evidence_ids':claim['evidence_ids'],'unknowns':claim['open_questions']+claim['known_gaps'],'scope':claim['requirement_ids'] or [claim['claim_type']],'quality_posture':'satisfactory' if verdict=='satisfied' else 'uncertain' if verdict=='inconclusive' else 'needs_improvement'}
    if runtime and not runs:result['dimensions']['runtime_validation']['status']='not_run'
    if semantic and any(x['code'] in ('SEMANTIC_REVIEW_MISSING','SEMANTIC_REVIEW_STALE') for x in findings):result['dimensions']['semantic_review']['status']='not_run'
    if runtime and runs and any(x['dimension']=='verification' for x in findings):result['dimensions']['runtime_validation']['status']='failed'
    validate_schema(result)
    return request,result


def assess(task,claim_file,*,expected_snapshot=None):
    w=Workspace(task);path=within(w.root,claim_file,must_exist=True);claim=validate_schema(load_json(path),'Claim')
    safe_id(claim['id'])
    with locked(w.root):
        before=w.snapshot()
        if expected_snapshot and before!=expected_snapshot:raise BugateError('CONCURRENT_CONFLICT','Snapshot changed before assessment')
        request,result=evaluate(w,claim)
        if w.snapshot()!=before:raise BugateError('CONCURRENT_CONFLICT','Workspace changed during assessment')
        if load_json(path)!=claim:raise BugateError('CONCURRENT_CONFLICT','Claim changed during assessment')
        # Hash-addressed immutable requests/results retain audit history across re-assessment.
        key=request['request_digest'].split(':')[1]
        write_json(w.root,'claims/'+claim['id']+'.json',claim)
        write_json(w.root,'assessments/'+key+'.request.json',request,immutable=True)
        write_json(w.root,'assessments/'+key+'.result.json',result,immutable=True)
        if w.snapshot()!=before:raise BugateError('CONCURRENT_CONFLICT','Workspace changed at assessment commit')
    return result


def validation(task):
    w=Workspace(task);f=Facts(w);f.business();f.design()
    return {'valid':not any(x['blocking'] for x in f.findings),'workspace_snapshot':w.snapshot(),'binding':w.binding,'findings':f.findings,'limitations':['Deterministic validation is not execution or business correctness.']}


def status(task):
    w=Workspace(task);f=Facts(w)
    for issue in f.findings:
        if issue['subject'] in f.evidence:issue['blocking']=False;issue['severity']='warning'
    f.business();f.design();snap=w.snapshot();results=[];ready=False
    for p in sorted(w.path('claims').glob('*.json')):
        try:
            claim=validate_schema(load_json(within(w.root,p, must_exist=True)),'Claim')
            _,result=evaluate(w,claim)
            results.append({'claim_id':claim['id'],'claim_type':claim['claim_type'],'verdict':result['verdict'],'fresh':claim['workspace_snapshot']==snap,'claim_digest':object_digest(claim),'request_digest':result['request_digest'],'findings':result['findings']})
            if result['verdict']=='satisfied' and claim['claim_type']=='test_design_complete':ready=True
        except BugateError as exc:f.add(exc.code,p.name,exc.message)
    quality='unclaimed' if not results else 'satisfactory' if all(x['verdict']=='satisfied' for x in results) else 'needs_improvement'
    witness=filesystem_manifest(w)
    if witness!=filesystem_manifest(w) or w.snapshot()!=snap:
        raise BugateError('CONCURRENT_CONFLICT','Workspace changed during execution-world verification')
    return {'task_id':w.task['id'],'workspace_id':w.id,'workspace_snapshot':snap,'binding':w.binding,'quality_posture':{'overall':quality},'implementation_ready':ready,'execution_state':_execution_state(f),'findings':f.findings,'assessments':results,'filesystem_manifest':witness}

def _execution_state(f):
    runs=[x['execution'] for x in f.evidence.values() if x['source_type']=='host_observed' and x['execution'] and not any(y['subject']==x['id'] for y in f.findings)]
    if not runs:return 'not_run'
    if any(x['status']=='failed' for x in runs):return 'failed'
    if any(x['status'] in ('cancelled','timeout','blocked') for x in runs):return 'blocked'
    if any(x['statistics']['tests']==0 or x['statistics']['passed']+x['statistics']['failed']+x['statistics']['errors']==0 for x in runs):return 'blocked'
    return 'passed'
