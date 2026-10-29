"""Source-bound acceptance regression. SC names retain candidate example provenance, not user-mandated product requirements."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from bugate import API_VERSION
from bugate.assessment import assess,evaluate,status,Facts
from bugate.authoring import record_artifact
from bugate.context import prepare
from bugate.errors import BugateError
from bugate.evidence import execute
from bugate.protocol import canonical,content_digest,object_digest,validate_schema
from bugate.scope import active_scope,scope_ref,record_scope,record_request,record_suggestion,suggestions,authorize,check,COVERAGE_STATEMENT
from bugate.workspace import initialize,Workspace,write_json,execution_input_manifest
from helpers import fixture,create_scope,revise_scope,put_artifact,review,claim,run,delivery_artifacts


def order_fixture(project,branches=('create',),broken=False):
    project.mkdir(parents=True,exist_ok=True)
    meanings={'create':'An ordinary user creates an order in their tenant; read back the persisted tenant, owner, quantity and status.','deny':'An ordinary user cannot create an order for another tenant; permission denial must leave persisted orders unchanged.','boundary':'Quantity 1 is accepted and persisted exactly.','failure':'Quantity 0 is rejected with invalid_quantity and no persisted order.'}
    request=' '.join(meanings[b] for b in branches)
    (project/'requirements.md').write_text(request)
    rids=['R'+str(i+1) for i in range(len(branches))]
    profile={'apiVersion':API_VERSION,'kind':'Profile','id':'orders','version':'1','sources':[{'id':'SRC1','path':'requirements.md','digest':content_digest((project/'requirements.md').read_bytes()),'description':'Explicit finite order acceptance contract'}],'requirements':rids,'risks':[],'goal':request,'delivery_criteria':['business_understanding','test_design','implementation','execution','report'],'resource_boundaries':{'network':False,'destructive':False,'notes':'Generated local order store only'},'unknowns':[]}
    write_json(project,'profile.json',profile);w=Workspace(initialize(project,'orders',project/'profile.json')['task_root'])
    request_value={'id':'orders-user','message':request,'origin':'user','session_id':'human-orders','source_event_id':'event-orders'}
    write_json(w.root,'.bugate/drafts/request.json',request_value);record_request(w.root,'.bugate/drafts/request.json')
    citation={'request_id':'orders-user','quote':request};basis={'kind':'user_explicit','request_refs':[citation],'explanation':'This branch is explicitly included in the direct user request.'}
    oids=['O'+str(i+1) for i in range(len(branches))];cids=['C'+str(i+1) for i in range(len(branches))]
    scope={'id':'orders-scope','revision':1,'request_refs':[citation],'source_refs':['SRC1'],'objectives':[{'id':'orders-goal','statement':request,'request_refs':[citation]}],'subject':{'object':'Generated order service','environment_ids':['local'],'context_boundaries':['Ordinary user alice in tenant A; temporary order store']},'flows':[{'id':b,'objective_ids':['orders-goal'],'entry':'Login ordinary user alice','key_nodes':['Submit order with selected tenant and quantity','Read persisted order store'],'terminal':meanings[b],'branch_of':None if i==0 else branches[0]} for i,b in enumerate(branches)],'requirements':[{'id':r,'objective_ids':['orders-goal'],'oracle_ids':[o],'basis':basis} for r,o in zip(rids,oids)],'risk_ids':[],'scenarios':[{'id':c,'requirement_ids':[r],'flow_id':b,'oracle_ids':[o],'expected_evidence':[meanings[b]],'necessity':basis} for c,r,b,o in zip(cids,rids,branches,oids)],'exclusions':[{'id':'not-requested','description':'No additional fuzzing, concurrency, load or unrelated business workflows.','requirement_ids':[]}],'auxiliary':[{'id':a,'purpose':p,'description':p,'related_scenario_ids':[],'max_runs':10,'stop_condition':'Stop when the named preparation or cleanup is complete'} for a,p in [('login','data_preparation'),('read','evidence_read'),('cleanup','cleanup')]],'assumptions':[],'completion':{'execution_complete':'all_required_scenarios_observed','functional_pass':'all_required_oracles_passed','suggestions_block':False,'preserve_failures':True},'change':{'previous_digest':None,'request_refs':[],'impact':'Initial explicit scope'}}
    write_json(w.root,'.bugate/drafts/scope.json',scope);scope=record_scope(w.root,'.bugate/drafts/scope.json')
    sut='''import json
from pathlib import Path
STORE=Path('logs/orders.json')
def read_orders():
    return json.loads(STORE.read_text()) if STORE.exists() else []
def create_order(actor,tenant,quantity):
    if actor['tenant'] != tenant: return {'error':'permission_denied'}
    if quantity < 1: return {'error':'invalid_quantity'}
    orders=read_orders()
    order={'id':len(orders)+1,'tenant':tenant,'owner':actor['id'],'quantity':quantity,'status':'created'}
    orders.append(order);STORE.write_text(json.dumps(orders))
    return {'order_id':order['id']}
'''
    if broken:sut=sut.replace("'status':'created'","'status':'pending'")
    (w.root/'orders.py').write_text(sut)
    runner='''import sys,xml.etree.ElementTree as E
from pathlib import Path
from orders import create_order,read_orders
suite=E.Element('testsuite');failed=0
actor={'id':'alice','tenant':'A'}
for branch in sys.argv[2:]:
    case=E.SubElement(suite,'testcase',classname='orders',name=branch)
    try:
        before=read_orders()
        if branch in ('create','boundary'):
            quantity=2 if branch=='create' else 1
            result=create_order(actor,'A',quantity)
            persisted=read_orders()
            assert len(persisted)==len(before)+1
            assert persisted[-1]=={'id':result['order_id'],'tenant':'A','owner':'alice','quantity':quantity,'status':'created'}
        elif branch=='deny':
            assert create_order(actor,'B',2)=={'error':'permission_denied'}
            assert read_orders()==before
        elif branch=='failure':
            assert create_order(actor,'A',0)=={'error':'invalid_quantity'}
            assert read_orders()==before
    except AssertionError:
        failed+=1;E.SubElement(case,'failure',message='Business state violates the agreed order Oracle')
E.ElementTree(suite).write(sys.argv[1]);sys.exit(1 if failed else 0)
'''
    (w.root/'tests.py').write_text(runner)
    put_artifact(w.root,'brief','business_brief',{'propositions':[{'id':'P'+str(i+1),'statement':meanings[b],'source_refs':['SRC1'],'requirement_ids':[r],'classification':'fact'} for i,(b,r) in enumerate(zip(branches,rids))],'oracles':[{'id':o,'proposition_ids':['P'+str(i+1)],'expected':meanings[b],'observation':'Read persisted order store and assert exact state or exact rejection with unchanged store','deadline':'Synchronous operation return','preconditions':['Ordinary user authenticated'],'source_refs':['SRC1']} for i,(b,o) in enumerate(zip(branches,oids))],'states':['authenticated','order submitted','business state observed'],'boundaries':[],'unknowns':[]})
    put_artifact(w.root,'testability','testability',{'decisions':[{'proposition_id':'P'+str(i+1),'oracle_id':o,'observation':'Persisted business state','deadline':'Synchronous','preconditions':['Temporary order store'],'layer':'component','rationale':'Direct service behavior plus real persistence suffices','alternatives':['No additional workflow requested'],'side_effects':'Writes temporary orders only','environment':'local'} for i,o in enumerate(oids)],'gaps':[]})
    put_artifact(w.root,'inventory','inventory',{'scope_ref':scope_ref(scope),'cases':[{'id':c,'scenario_id':c,'classification':'required','proposition_ids':['P'+str(i+1)],'oracle_ids':[o],'risk_ids':[],'preconditions':['Ordinary user alice authenticated'],'expected_observations':[meanings[b]],'layer':'component','layer_reason':'Asserts real persistence and business outcome','implementation':{'path':'tests.py','digest':content_digest(runner.encode())},'test_selector':'orders.'+b,'negative':b in ('deny','failure')} for i,(c,o,b) in enumerate(zip(cids,oids,branches))],'requirement_scope':rids,'risk_scope':[]})
    review(w.root)
    return w


class ScopeAcceptance(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='bugate-scope-');self.project=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def result(self,w,kind='task_delivery_complete',eid=None,cid='claim'):
        c=claim(w,kind,cid,evidence_ids=eid,requirements=[r['id'] for r in active_scope(w)['requirements']]);return evaluate(w,c)[1]
    def codes(self,result):return {f['code'] for f in result['findings']}
    def order_run(self,w,branches,run_id='orders'):
        ids=list(Facts(w).cases)
        return execute(w.root,[sys.executable,'tests.py','logs/'+run_id+'.xml',*branches],run_id=run_id,case_ids=ids,report='logs/'+run_id+'.xml')
    def report(self,w,e):
        put_artifact(w.root,'report','execution_report',{'evidence_ids':[e['id']],'case_ids':list(Facts(w).cases),'outcome':'product_failure' if e['execution']['status']=='failed' else 'passed','findings':['Actual business result mismatch'] if e['execution']['status']=='failed' else [],'limitations':['Only the finite requested order branches'],'analysis':'Real service invocation and persisted business assertions.'})
    def test_SC01_minimum_order_flow_has_real_business_evidence(self):
        w=order_fixture(self.project);self.assertEqual(self.result(w,'test_design_complete')['verdict'],'satisfied')
        self.assertEqual(len(Facts(w).cases),1);self.assertFalse(next(iter(Facts(w).cases.values()))['negative'])
        e=self.order_run(w,['create']);self.report(w,e)
        self.assertEqual(json.loads((w.root/'logs/orders.json').read_text())[0]['status'],'created')
        self.assertEqual(self.result(w,'requirement_verified')['verdict'],'satisfied');self.assertEqual(self.result(w)['verdict'],'satisfied')
    def test_SC02_explicit_tenant_denial_is_required_and_has_no_side_effect(self):
        w=order_fixture(self.project,('create','deny'));e=self.order_run(w,['create','deny']);self.report(w,e)
        self.assertEqual(e['execution']['statistics']['passed'],2);self.assertEqual(len(json.loads((w.root/'logs/orders.json').read_text())),1)
        self.assertEqual(self.result(w,'requirement_verified')['verdict'],'satisfied');self.assertEqual(active_scope(w)['scenarios'][1]['flow_id'],'deny')
    def test_SC03_all_explicit_normal_boundary_failure_branches_remain(self):
        w=order_fixture(self.project,('create','boundary','failure'));e=self.order_run(w,['create','boundary','failure']);self.report(w,e)
        self.assertEqual(e['execution']['statistics']['passed'],3);self.assertEqual(len(Facts(w).cases),3);self.assertEqual(self.result(w)['verdict'],'satisfied')
    def test_SC04_auxiliary_can_repair_missing_oracles_without_new_goal(self):
        w=fixture(self.project,with_artifacts=False)
        for aid in ('read','prepare','probe','cleanup'):
            self.assertTrue(authorize(w.root,activity_id=aid)['allowed'])
            execute(w.root,[sys.executable,'-c','print("bounded auxiliary operation")'],run_id=aid,activity_id=aid)
        self.assertEqual(len(active_scope(w)['objectives']),1);self.assertFalse(authorize(w.root,case_ids=['C1'])['allowed'])
    def test_SC05_unrelated_plan_extension_is_advisory_and_cannot_execute(self):
        w=fixture(self.project);old=w.read('artifacts/inventory.json');draft=deepcopy(old);draft.pop('digest');extra=deepcopy(draft['content']['cases'][0]);extra.update(id='fuzz',scenario_id='unrequested-fuzz');draft['content']['cases'].append(extra)
        write_json(w.root,'.bugate/drafts/expanded.json',draft)
        with self.assertRaises(BugateError) as cm:record_artifact(w.root,'.bugate/drafts/expanded.json',expected_digest=old['digest'])
        self.assertEqual(cm.exception.code,'SCENARIO_OUT_OF_SCOPE');self.assertEqual(w.read('artifacts/inventory.json'),old);self.assertEqual(len(suggestions(w)),1)
        self.assertFalse(authorize(w.root,case_ids=['fuzz'])['allowed'])
    def test_SC06_suggestions_do_not_invalidate_or_block_completed_scope(self):
        w=fixture(self.project);e=run(w);delivery_artifacts(w,e);c=claim(w,'task_delivery_complete');before=w.snapshot();manifest=execution_input_manifest(w);claim_before=deepcopy(c);initial=assess(w.root,'claims/claim1.json')
        for i in range(3):
            write_json(w.root,'.bugate/drafts/suggestion.json',{'id':'optional-'+str(i),'description':'Consider extra load coverage','rationale':'No request for load testing in this scope','source_refs':['SRC1']});record_suggestion(w.root,'.bugate/drafts/suggestion.json')
        self.assertEqual(w.snapshot(),before);self.assertEqual(execution_input_manifest(w),manifest);self.assertEqual(evaluate(w,c)[1]['verdict'],'satisfied')
        updated=assess(w.root,'claims/claim1.json');self.assertEqual(updated['verdict'],'satisfied');self.assertNotEqual(updated['request_digest'],initial['request_digest']);self.assertEqual(updated['workspace_snapshot'],initial['workspace_snapshot']);self.assertEqual(w.read('claims/claim1.json'),claim_before);self.assertEqual(w.snapshot(),before);self.assertEqual(execution_input_manifest(w),manifest);self.assertEqual(len(list(w.path('assessments').glob('*.result.json'))),2);self.assertEqual(len(updated['acceptance']['suggestion_ids']),3)
        current=status(w.root);self.assertEqual(len(current['suggestions']),3);self.assertTrue(next(a for a in current['assessments'] if a['claim_id']=='claim1')['fresh'])
    def test_SC07_real_failure_completes_execution_not_functional_acceptance(self):
        w=order_fixture(self.project,broken=True);e=self.order_run(w,['create']);self.report(w,e)
        self.assertEqual(e['execution']['status'],'failed');self.assertEqual(self.result(w,'execution_complete')['verdict'],'satisfied');self.assertEqual(self.result(w)['verdict'],'satisfied');self.assertNotEqual(self.result(w,'requirement_verified')['verdict'],'satisfied')
        self.assertEqual(w.read('artifacts/report.json')['content']['functional_result'],'failed')
    def test_SC08_missing_business_evidence_never_closes_execution(self):
        w=fixture(self.project);e=execute(w.root,[sys.executable,'-c','from pathlib import Path;Path("logs/partial.xml").write_text(\'<testsuite><testcase classname="fixture" name="test_positive"/></testsuite>\')'],run_id='partial',case_ids=['C1'],report='logs/partial.xml')
        result=self.result(w,'execution_complete');self.assertIn('CASE_NOT_EXECUTED',self.codes(result));self.assertNotEqual(result['verdict'],'satisfied')
        self.assertNotEqual(self.result(w,'requirement_verified')['verdict'],'satisfied')
    def test_SC09_diagnosis_has_real_failure_link_and_consumable_bound(self):
        w=fixture(self.project);self.assertFalse(authorize(w.root,activity_id='diagnose')['allowed'])
        (w.root/'sut.py').write_text('def bounded(value):\n    return value\n');e=run(w)
        for i in range(2):execute(w.root,[sys.executable,'-c','print("diagnosis observation")'],run_id='diagnose-'+str(i),activity_id='diagnose')
        denied=authorize(w.root,activity_id='diagnose');self.assertFalse(denied['allowed']);self.assertIn('DIAGNOSTIC_LIMIT_REACHED',self.codes(denied));self.assertEqual(w.read('evidence/'+e['id']+'.json')['execution']['status'],'failed')
    def test_SC10_explicit_expansion_versions_scope_and_invalidates_old_plan(self):
        w=fixture(self.project);old=active_scope(w);oldclaim=claim(w,'test_design_complete');run(w)
        def add(s):
            citation={'request_id':'user-change-2','quote':'Also require the zero boundary as a separate acceptance branch.'}
            basis={'kind':'user_explicit','request_refs':[citation],'explanation':'The new direct user request adds a finite zero-input branch.'}
            s['requirements'][0]['oracle_ids'].append('O2')
            s['scenarios'].append({'id':'C3','requirement_ids':['R1'],'flow_id':'main','oracle_ids':['O2'],'expected_evidence':['Zero input returns exactly zero'],'necessity':basis})
        new=revise_scope(w,add,'Also require the zero boundary as a separate acceptance branch.')
        self.assertEqual(new['revision'],2);self.assertEqual(new['change']['previous_digest'],old['digest']);self.assertTrue((w.root/'.bugate/scope-history'/ (old['digest'].split(':')[1]+'.json')).exists())
        self.assertNotEqual(evaluate(w,oldclaim)[1]['verdict'],'satisfied');self.assertIn('SCOPE_SCENARIO_MISSING',self.codes(check(w.root)));self.assertFalse(authorize(w.root,case_ids=['C1'])['allowed'])
        brief=w.read('artifacts/brief.json')['content'];brief['propositions'].append({'id':'P2','statement':'Zero input returns exactly zero','source_refs':['SRC1'],'requirement_ids':['R1'],'classification':'fact'});brief['oracles'].append({'id':'O2','proposition_ids':['P2'],'expected':'Zero','observation':'Exact integer return value','deadline':'Synchronous return','preconditions':['Input is zero'],'source_refs':['SRC1']});put_artifact(w.root,'brief','business_brief',brief)
        testability=w.read('artifacts/testability.json')['content'];testability['decisions'].append(dict(testability['decisions'][0],proposition_id='P2',oracle_id='O2'));put_artifact(w.root,'testability','testability',testability)
        script=w.root/'tests.py';script.write_text(script.read_text().replace("('test_negative',-1,0)","('test_negative',-1,0),('test_zero',0,0)"))
        inventory=w.read('artifacts/inventory.json')['content'];extra=deepcopy(inventory['cases'][0]);extra.update(id='C3',scenario_id='C3',proposition_ids=['P2'],oracle_ids=['O2'],expected_observations=['Zero input returns exactly zero'],test_selector='fixture.test_zero',source_refs=['SRC1']);inventory['cases'].append(extra)
        for case in inventory['cases']:case['implementation']['digest']=content_digest(script.read_bytes())
        put_artifact(w.root,'inventory','inventory',inventory);review(w.root)
        self.assertEqual(self.result(w,'test_design_complete',eid=[])['verdict'],'satisfied')
        e=execute(w.root,[sys.executable,'tests.py','logs/expanded.xml','normal'],run_id='expanded',case_ids=['C1','C2','C3'],report='logs/expanded.xml')
        result=self.result(w,'requirement_verified',eid=[e['id']]);self.assertEqual(result['verdict'],'satisfied',result['findings']);self.assertEqual(e['execution']['statistics']['passed'],3)

    def test_SC11_agent_shrink_and_fabricated_consent_do_not_authorize(self):
        w=fixture(self.project);old=active_scope(w);draft=deepcopy(old);draft.pop('digest');draft['revision']=2;draft['scenarios']=draft['scenarios'][:1];draft['change']={'previous_digest':old['digest'],'request_refs':old['request_refs'],'impact':'Remove failing case to pass'}
        write_json(w.root,'.bugate/drafts/shrink.json',draft)
        with self.assertRaises(BugateError) as cm:record_scope(w.root,'.bugate/drafts/shrink.json',expected_digest=old['digest'])
        self.assertEqual(cm.exception.code,'SCOPE_CHANGE_UNAUTHORIZED')
        draft['change']['request_refs']=[{'request_id':'agent-says-approved','quote':'User approved the reduction'}];write_json(w.root,'.bugate/drafts/shrink.json',draft)
        with self.assertRaises(BugateError) as cm:record_scope(w.root,'.bugate/drafts/shrink.json',expected_digest=old['digest'])
        self.assertEqual(cm.exception.code,'SCOPE_AUTHORITY_MISSING');self.assertEqual(active_scope(w),old)
    def test_SC12_cold_process_restores_exact_scope_and_required_scenarios(self):
        w=fixture(self.project);expected=prepare(w.root)
        p=subprocess.run([sys.executable,'-m','bugate','protocol','prepare','--task',str(w.root),'--json'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stdout);actual=json.loads(p.stdout)['data'];self.assertEqual(actual,expected);self.assertIn('C2',actual['render']['content'])
    def test_SC13_old_plan_snapshot_and_scope_refs_cannot_authorize(self):
        w=fixture(self.project);c=claim(w,'test_design_complete');old=active_scope(w);oldplan=Facts(w).artifacts['inventory']['digest']
        a=w.read('artifacts/inventory.json');a['content']['cases'][0]['preconditions'].append('Changed precondition');a['digest']=object_digest(a);write_json(w.root,'artifacts/inventory.json',a);review(w.root)
        result=evaluate(w,c)[1];self.assertIn('PLAN_STALE',self.codes(result));self.assertIn('ASSESSMENT_STALE',self.codes(result));self.assertFalse(authorize(w.root,case_ids=['C1'],expected_scope_digest=old['digest'],expected_plan_digest=oldplan)['allowed'])
    def test_SC14_parallel_tasks_do_not_share_scope_evidence_or_suggestions(self):
        one=fixture(self.project/'one');two=order_fixture(self.project/'two');e=run(one);foreign=claim(one,'test_design_complete')
        with self.assertRaises(BugateError) as cm:evaluate(two,foreign)
        self.assertEqual(cm.exception.code,'WORKSPACE_MISMATCH');write_json(two.root,'evidence/foreign.json',e);self.assertIn('WORKSPACE_MISMATCH',self.codes(check(two.root)));self.assertNotEqual(scope_ref(active_scope(one)),scope_ref(active_scope(two)))
        record_suggestion(one.root,value={'id':'advice','description':'Consider concurrency later','rationale':'Outside integer request','source_refs':['SRC1']});self.assertEqual(suggestions(two),[])
        commands=[[sys.executable,'-m','bugate','execution','run','--task',str(one.root),'--run-id','parallel-one','--case','C1','--case','C2','--report','logs/parallel-one.xml','--json','--',sys.executable,'tests.py','logs/parallel-one.xml','normal'],[sys.executable,'-m','bugate','execution','run','--task',str(two.root),'--run-id','parallel-two','--case','C1','--report','logs/parallel-two.xml','--json','--',sys.executable,'tests.py','logs/parallel-two.xml','create']]
        processes=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for command in commands]
        outputs=[process.communicate(timeout=15) for process in processes]
        for process,(out,err),workspace in zip(processes,outputs,[one,two]):
            self.assertEqual(process.returncode,0,out+err);evidence=json.loads(out)['data'];self.assertEqual(evidence['workspace_id'],workspace.id);self.assertEqual(evidence['execution']['scope_ref'],scope_ref(active_scope(workspace)))

    def test_SC15_conflicts_and_missing_necessity_are_explicit_gaps(self):
        w=fixture(self.project);revise_scope(w,lambda s:s['exclusions'].append({'id':'conflict','description':'Exclude negative acceptance','requirement_ids':['R1']}),'Explicitly reconsider the requested acceptance boundary.')
        self.assertIn('SCOPE_EXCLUSION_CONFLICT',self.codes(check(w.root)))
        a=w.read('artifacts/review.json');a['content']['scope_decisions'][0]['necessity']='uncertain';a['digest']=object_digest(a);write_json(w.root,'artifacts/review.json',a);self.assertIn('SCOPE_SEMANTIC_REVIEW_MISSING',self.codes(check(w.root)))
    def test_SC18_report_separates_coverage_function_result_blockers_and_advice(self):
        w=order_fixture(self.project,broken=True);e=self.order_run(w,['create']);self.report(w,e)
        report=w.read('artifacts/report.json');self.assertEqual(report['content']['coverage_statement'],COVERAGE_STATEMENT);self.assertTrue(report['content']['execution_complete']);self.assertEqual(report['content']['functional_result'],'failed');self.assertEqual(report['content']['requirement_ids'],['R1'])
        report['content']['functional_result']='passed';report['digest']=object_digest(report);write_json(w.root,'artifacts/report.json',report);self.assertIn('FAILURE_HIDDEN',self.codes(self.result(w)))
    def test_wrong_environment_and_redundant_scenario_are_denied(self):
        w=fixture(self.project);self.assertFalse(authorize(w.root,case_ids=['C1'],environment_id='production')['allowed'])
        a=w.read('artifacts/inventory.json');duplicate=deepcopy(a['content']['cases'][0]);duplicate['id']='duplicate';a['content']['cases'].append(duplicate);a['digest']=object_digest(a);write_json(w.root,'artifacts/inventory.json',a);review(w.root)
        self.assertIn('SCENARIO_REDUNDANT',self.codes(check(w.root)))
    def test_scope_cannot_be_omitted_or_user_text_be_forged(self):
        w=fixture(self.project);old=active_scope(w);(w.root/'scope/active.json').unlink();self.assertIn('SCOPE_MISSING',self.codes(self.result_missing(w)))
        draft=deepcopy(old);draft.pop('digest');draft['objectives'][0]['request_refs'][0]['quote']='Fake user consent';write_json(w.root,'.bugate/drafts/fake.json',draft)
        with self.assertRaises(BugateError) as cm:record_scope(w.root,'.bugate/drafts/fake.json')
        self.assertEqual(cm.exception.code,'SCOPE_AUTHORITY_INVALID')
    def test_explicit_old_version_and_foreign_scope_drafts_are_rejected(self):
        w=fixture(self.project);value=active_scope(w);value.pop('digest');value['schema_version']='2.0.0';write_json(w.root,'.bugate/drafts/old.json',value)
        with self.assertRaises(BugateError) as cm:record_scope(w.root,'.bugate/drafts/old.json')
        self.assertEqual(cm.exception.code,'PROTOCOL_MISMATCH')
        value['schema_version']='2.1.0-dev.1';value['workspace_id']='sha256:'+'0'*64;write_json(w.root,'.bugate/drafts/old.json',value)
        with self.assertRaises(BugateError) as cm:record_scope(w.root,'.bugate/drafts/old.json')
        self.assertEqual(cm.exception.code,'WORKSPACE_MISMATCH')
    def test_untrusted_failure_cannot_open_diagnostic_channel(self):
        w=fixture(self.project);(w.root/'sut.py').write_text('def bounded(value):\n    return value\n');e=run(w)
        (w.root/'.bugate/observations'/ (e['id']+'.json')).unlink()
        result=authorize(w.root,activity_id='diagnose');self.assertFalse(result['allowed']);self.assertIn('DIAGNOSTIC_FAILURE_MISSING',self.codes(result))
    def test_scope_flow_cycles_are_rejected(self):
        w=fixture(self.project)
        def cycle(s):
            s['flows'][0]['branch_of']='second';s['flows'].append(dict(s['flows'][0],id='second',branch_of='main'))
        revise_scope(w,cycle,'Clarify the finite branch arrangement.')
        self.assertIn('SCOPE_REFERENCE_INVALID',self.codes(check(w.root)))
    def test_profile_denominator_change_requires_explicit_new_profile_task(self):
        w=fixture(self.project);old=active_scope(w)
        with self.assertRaises(BugateError) as cm:
            revise_scope(w,lambda s:s['requirements'].append(dict(s['requirements'][0],id='R2')),'Add a separately specified acceptance requirement R2.')
        self.assertEqual(cm.exception.code,'SCOPE_PROFILE_CHANGE_REQUIRED');self.assertEqual(cm.exception.details['scope_ref'],scope_ref(old));self.assertIn('bugate init',cm.exception.details['usage']);self.assertEqual(active_scope(w),old)
        new_profile=deepcopy(w.profile);new_profile['version']='2';new_profile['requirements'].append('R2');write_json(self.project,'explicit-new-profile.json',new_profile)
        new=Workspace(initialize(self.project,'expanded-task',self.project/'explicit-new-profile.json')['task_root'])
        self.assertEqual(new.profile['requirements'],['R1','R2']);self.assertNotEqual(new.id,w.id);self.assertEqual(Workspace(w.root).profile['requirements'],['R1'])
    def test_omitted_current_failure_cannot_be_reported_as_functional_pass(self):
        w=fixture(self.project)
        (w.root/'sut.py').write_text('from pathlib import Path\ndef bounded(value):\n    return value if Path("logs/fail").exists() else max(0,value)\n')
        (w.root/'logs/fail').write_text('fail this run');failed=run(w,'failed-current');(w.root/'logs/fail').unlink();passed=run(w,'passed-current')
        self.assertEqual(passed['execution']['status'],'passed')
        result=self.result(w,'requirement_verified',eid=[passed['id']]);self.assertNotEqual(result['verdict'],'satisfied');self.assertEqual(result['acceptance']['functional_result'],'failed');self.assertIn(failed['id'],result['acceptance']['historical_failure_ids'])
    def test_nonzero_process_without_failed_oracle_is_incomplete_not_product_failure(self):
        w=fixture(self.project);script=w.root/'tests.py';script.write_text(script.read_text().replace('sys.exit(1 if failed else 0)','sys.exit(2)'))
        inventory=w.read('artifacts/inventory.json')['content']
        for case in inventory['cases']:case['implementation']['digest']=content_digest(script.read_bytes())
        put_artifact(w.root,'inventory','inventory',inventory);review(w.root);run(w)
        result=self.result(w,'execution_complete');self.assertNotEqual(result['verdict'],'satisfied');self.assertEqual(result['acceptance']['functional_result'],'undetermined');self.assertFalse(result['acceptance']['execution_complete'])
    def test_named_requirement_verification_preserves_unobserved_task_scope(self):
        # F-04/F-06: a named requirement claim is not a whole-task completion claim.
        w=order_fixture(self.project,('create','deny'))
        e=execute(w.root,[sys.executable,'tests.py','logs/only-create.xml','create'],run_id='only-create',case_ids=['C1'],report='logs/only-create.xml')
        named=claim(w,'requirement_verified','only-r1',evidence_ids=[e['id']],requirements=['R1'])
        result=evaluate(w,named)[1]
        self.assertEqual(result['verdict'],'satisfied',result['findings'])
        self.assertEqual(result['scope'],['R1'])
        self.assertEqual(result['acceptance']['requirement_ids'],['R1','R2'])
        self.assertFalse(result['acceptance']['execution_complete'])
        self.assertEqual(result['acceptance']['functional_result'],'undetermined')
        unobserved=claim(w,'requirement_verified','only-r2',evidence_ids=[e['id']],requirements=['R2'])
        self.assertNotEqual(evaluate(w,unobserved)[1]['verdict'],'satisfied')
        complete=self.result(w,'execution_complete',eid=[e['id']],cid='whole-task')
        self.assertNotEqual(complete['verdict'],'satisfied')
        self.assertIn('CASE_NOT_EXECUTED',self.codes(complete))
    def result_missing(self,w):return evaluate(w,claim(w,'execution_complete'))[1]

if __name__=='__main__':unittest.main(verbosity=2)
