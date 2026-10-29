"""Ephemeral SUT generator. No product-specific source or credentials ship here."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from bugate import API_VERSION
from bugate.protocol import content_digest,object_digest,canonical
from bugate.workspace import initialize,Workspace,write_json


def fixture(project:Path, *, with_artifacts=True):
    project.mkdir(parents=True,exist_ok=True)
    (project/'requirements.md').write_text('R1: Positive integers retain their value; zero and negative integers produce zero. K1: Negative boundary.\n')
    profile={'apiVersion':API_VERSION,'kind':'Profile','id':'neutral-profile','version':'1','sources':[{'id':'SRC1','path':'requirements.md','digest':content_digest((project/'requirements.md').read_bytes()),'description':'Fixture requirement contract'}],'requirements':['R1'],'risks':['K1'],'goal':'Verify the bounded integer function','delivery_criteria':['business_understanding','test_design','implementation','execution','report','knowledge'],'resource_boundaries':{'network':False,'destructive':False,'notes':'Temporary generated fixture only'},'unknowns':[]}
    (project/'profile-input.json').write_bytes(canonical(profile))
    initialized=initialize(project,'same-task',project/'profile-input.json');root=Path(initialized['task_root'])
    if not with_artifacts:return Workspace(root)
    (root/'sut.py').write_text('def bounded(value):\n    return max(0, value)\n')
    runner='''import importlib.util, sys, xml.etree.ElementTree as E
from pathlib import Path
from sut import bounded
mode=sys.argv[2] if len(sys.argv)>2 else 'normal'
root=E.Element('testsuite')
failed=0
for name,value,expected in [('test_positive',3,3),('test_negative',-1,0)]:
    if mode=='zero':continue
    case=E.SubElement(root,'testcase',classname='fixture',name=name)
    if mode in ('skip','xfail'):
        E.SubElement(case,'skipped',type='pytest.xfail' if mode=='xfail' else 'skip');continue
    try:
        assert bounded(value)==expected, 'wrong business result'
    except AssertionError as exc:
        failed+=1;E.SubElement(case,'failure',message=str(exc))
E.ElementTree(root).write(sys.argv[1],encoding='utf-8')
print('executed',len(root),'cases')
sys.exit(1 if failed else 0)
'''
    (root/'tests.py').write_text(runner)
    brief={'propositions':[{'id':'P1','statement':'Integer input preserves positive values and floors negatives to zero','source_refs':['SRC1'],'requirement_ids':['R1'],'classification':'fact'}],'oracles':[{'id':'O1','proposition_ids':['P1'],'expected':'Exact integer result equals max(0,input)','observation':'Return value assertion','deadline':'Synchronous function return','preconditions':['Integer input'],'source_refs':['SRC1']}],'states':['integer input','bounded output'],'boundaries':['zero separates positive and negative domains'],'unknowns':[]}
    testability={'decisions':[{'proposition_id':'P1','oracle_id':'O1','observation':'Exact integer return','deadline':'Synchronous','preconditions':['Generated local fixture'],'layer':'unit','rationale':'Pure deterministic function','alternatives':['E2E adds no observable behavior'],'side_effects':'None','environment':'Python local process'}],'gaps':[]}
    cases=[]
    for i,name in enumerate(['test_positive','test_negative'],1):
        cases.append({'id':'C'+str(i),'proposition_ids':['P1'],'oracle_ids':['O1'],'risk_ids':['K1'],'preconditions':['Integer fixture available'],'expected_observations':['Exact result matches O1'],'layer':'unit','layer_reason':'Pure function sufficient','implementation':{'path':'tests.py','digest':content_digest((root/'tests.py').read_bytes())},'test_selector':'fixture.'+name,'negative':i==2})
    put_artifact(root,'brief','business_brief',brief)
    put_artifact(root,'testability','testability',testability)
    put_artifact(root,'inventory','inventory',{'cases':cases,'requirement_scope':['R1'],'risk_scope':['K1']})
    review(root)
    return Workspace(root)


def put_artifact(root,aid,kind,content):
    value={'apiVersion':API_VERSION,'kind':'Artifact','id':aid,'artifact_type':kind,'schema_version':'2.0.0','revision':'1','source_refs':['SRC1'],'relations':[],'content':content}
    value['digest']=object_digest(value);write_json(root,'artifacts/'+aid+'.json',value);return value


def review(root):
    artifacts=[json.loads(p.read_text()) for p in (root/'artifacts').glob('*.json') if p.stem in ('brief','testability','inventory')]
    return put_artifact(root,'review','semantic_review',{'reviewer':'deterministic-fixture reviewer','source_refs':['SRC1'],'artifact_digests':{a['id']:a['digest'] for a in artifacts},'conclusion':'accepted','limitations':['Synthetic contract review; not live model quality evidence'],'reasoning':'Each case asserts the exact fixture contract and covers the negative boundary.'})


def claim(w,kind='requirement_verified',cid='claim1',evidence_ids=None,requirements=None):
    value={'apiVersion':API_VERSION,'kind':'Claim','id':cid,'claim_type':kind,'task_id':w.task['id'],'workspace_id':w.id,'statement':'The named claim has the referenced facts','requirement_ids':['R1'] if requirements is None else requirements,'artifact_ids':[a['id'] for a in w.objects('artifacts','Artifact')],'evidence_ids':evidence_ids if evidence_ids is not None else [e['id'] for e in w.objects('evidence','Evidence')],'workspace_snapshot':w.snapshot(),'known_gaps':[],'open_questions':[]}
    write_json(w.root,'claims/'+cid+'.json',value);return value


def run(w,run_id='one',mode='normal',**kwargs):
    from bugate.evidence import execute
    return execute(w.root,[sys.executable,'tests.py',f'logs/{run_id}.xml',mode],run_id=run_id,case_ids=['C1','C2'],report=f'logs/{run_id}.xml',**kwargs)


def delivery_artifacts(w,evidence):
    put_artifact(w.root,'report','execution_report',{'evidence_ids':[evidence['id']],'case_ids':['C1','C2'],'outcome':'product_failure' if evidence['execution']['status']=='failed' else 'passed','findings':['Fixture defect reproduced'] if evidence['execution']['status']=='failed' else [],'limitations':['Local generated fixture only'],'analysis':'Managed execution and per-case oracle results preserved.'})
    put_artifact(w.root,'knowledge','knowledge',{'entries':[{'statement':'The fixture boundary is covered by C2','source_refs':[evidence['id']],'scope':'Generated fixture','status':'confirmed'}]})
