"""Native quality semantics retained when retiring historical file/host dialects."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from bugate.errors import BugateError
from bugate.protocol import object_digest, validate_schema, quality_rules, methods, evaluator_identity, resource_root
from bugate.workspace import Workspace, write_json, initialize
from bugate.assessment import evaluate
from bugate.context import prepare
from helpers import fixture, claim, put_artifact, review


class NativeSemantics(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bugate-native-');self.project=Path(self.tmp.name);self.w=fixture(self.project)
    def tearDown(self):self.tmp.cleanup()
    def mutate(self,aid,fn):
        path=self.w.root/'artifacts'/f'{aid}.json';value=json.loads(path.read_text());fn(value['content']);value['digest']=object_digest(value);write_json(self.w.root,path,value)
    def result(self):return evaluate(self.w,claim(self.w,'test_design_complete'))[1]
    def codes(self):return {f['code'] for f in self.result()['findings']}
    def reviewed_models(self):
        models={a['id']:a['digest'] for a in self.w.objects('artifacts','Artifact') if a['artifact_type']!='semantic_review'}
        self.mutate('review',lambda c:c.update(artifact_digests=models))
    def model(self,kind,content):return put_artifact(self.w.root,kind,kind,content)
    def domain(self):return self.model('domain_model',{'objects':[{'id':'OBJ1','description':'integer input'}],'invariants':[{'id':'INV1','object_ids':['OBJ1'],'proposition_ids':['P1'],'statement':'Output is nonnegative'}],'relationships':[]})
    def state(self):return self.model('state_model',{'states':[{'id':'S1','object_id':'OBJ1','description':'input'},{'id':'S2','object_id':'OBJ1','description':'output'}],'transitions':[{'id':'TR1','from_state':'S1','to_state':'S2','oracle_id':'O1','trigger':'call bounded','expected':'exact bounded value'}]})
    def adversarial(self):return self.model('adversarial_review',{'findings':[{'id':'ADV1','proposition_ids':['P1'],'oracle_ids':['O1'],'risk_ids':['K1'],'description':'Negative input boundary','disposition':'included','rationale':'C2 asserts negative boundary','case_ids':['C2']}],'residual_risks':[]})
    def dimension(self):return self.model('dimension_matrix',{'dimensions':[{'id':'DIM1','category':'boundary','proposition_ids':['P1'],'oracle_ids':['O1'],'object_ids':['OBJ1'],'transition_ids':['TR1'],'selected_layer':'unit','rationale':'Pure function','included_case_ids':['C2']}]})
    def requirements(self,settings):
        profile=deepcopy(self.w.profile);profile['quality_requirements']=settings
        write_json(self.project,'strict-profile.json',profile)
        target=initialize(self.project,'strict-task',self.project/'strict-profile.json')['task_root']
        for a in self.w.objects('artifacts','Artifact'):write_json(Path(target),'artifacts/'+a['id']+'.json',a)
        (Path(target)/'tests.py').write_bytes((self.w.root/'tests.py').read_bytes())
        self.w=Workspace(target)
    def test_old_artifact_and_old_profile_are_rejected(self):
        old={'gate_status':'passed','cases':[]}
        for kind in ('Artifact','Profile'):
            with self.subTest(kind=kind),self.assertRaises(BugateError) as cm:validate_schema(old,kind)
            self.assertEqual(cm.exception.code,'STRUCTURE_INVALID')
    def test_blank_business_statement_is_not_valid(self):
        self.mutate('brief',lambda c:c['propositions'][0].update(statement=' \n\t'))
        with self.assertRaises(BugateError) as cm:self.w.objects('artifacts','Artifact')
        self.assertEqual(cm.exception.code,'STRUCTURE_INVALID')
    def test_placeholder_oracle_does_not_pass_even_with_current_review(self):
        self.mutate('brief',lambda c:c['oracles'][0].update(expected='TBD'));review(self.w.root)
        self.assertIn('SEMANTIC_PLACEHOLDER',self.codes())
        self.assertEqual(self.result()['verdict'],'unsatisfied')
    def test_quoted_placeholder_in_requirement_is_data(self):
        self.mutate('brief',lambda c:c['propositions'][0].update(statement='Literal TBD input is rejected by the parser'));review(self.w.root)
        self.assertNotIn('SEMANTIC_PLACEHOLDER',self.codes())
    def test_inventory_requires_resolved_source_plan(self):
        a=self.w.read('artifacts/inventory.json');a['source_refs']=[];a['digest']=object_digest(a);write_json(self.w.root,'artifacts/inventory.json',a);review(self.w.root)
        self.assertIn('SOURCE_PLAN_MISSING',self.codes())
    def test_case_source_must_be_declared(self):
        self.mutate('inventory',lambda c:c['cases'][0].update(source_refs=['made-up']));review(self.w.root)
        self.assertIn('REFERENCE_MISSING',self.codes())
    def test_every_oracle_needs_testability_mapping(self):
        def extra(c):
            o=deepcopy(c['oracles'][0]);o['id']='O2';c['oracles'].append(o)
        self.mutate('brief',extra);review(self.w.root)
        self.assertIn('TESTABILITY_ORACLE_UNMAPPED',self.codes())
    def test_multiple_oracles_per_proposition_are_supported(self):
        def extra(c):
            o=deepcopy(c['oracles'][0]);o['id']='O2';c['oracles'].append(o)
        self.mutate('brief',extra)
        def decision(c):
            d=deepcopy(c['decisions'][0]);d['oracle_id']='O2';c['decisions'].append(d)
        self.mutate('testability',decision)
        self.mutate('inventory',lambda c:c['cases'][0]['oracle_ids'].append('O2'));review(self.w.root)
        self.assertEqual(self.result()['verdict'],'satisfied',self.result()['findings'])
    def test_oracle_specific_layer_cannot_borrow_another_oracle_layer(self):
        def extra(c):
            o=deepcopy(c['oracles'][0]);o['id']='O2';c['oracles'].append(o)
        self.mutate('brief',extra)
        def decision(c):
            d=deepcopy(c['decisions'][0]);d.update(oracle_id='O2',layer='probe');c['decisions'].append(d)
        self.mutate('testability',decision)
        self.mutate('inventory',lambda c:c['cases'][0].update(layer='probe'));review(self.w.root)
        self.assertIn('LAYER_DECISION_MISMATCH',self.codes())
    def test_case_layer_matches_testability(self):
        self.mutate('inventory',lambda c:c['cases'][0].update(layer='e2e'));review(self.w.root)
        self.assertIn('LAYER_DECISION_MISMATCH',self.codes())
    def test_optional_models_do_not_change_default_requirements(self):self.assertEqual(self.result()['verdict'],'satisfied')
    def test_profile_can_only_tighten_model_requirements(self):
        p=deepcopy(self.w.profile);p['quality_requirements']={'domain_model':False}
        with self.assertRaises(BugateError):validate_schema(p,'Profile')
    def test_profile_required_model_missing_is_visible(self):
        self.requirements({'adversarial_review':True});self.assertIn('QUALITY_MODEL_MISSING',self.codes())
    def test_full_native_model_is_reviewed_and_traceable(self):
        self.domain();self.state();self.dimension();self.adversarial();self.reviewed_models()
        self.assertEqual(self.result()['verdict'],'satisfied',self.result()['findings'])
    def test_model_review_is_required(self):
        self.domain();self.assertIn('SEMANTIC_REVIEW_MISSING',self.codes())
    def test_state_model_cannot_invent_objects_or_oracles(self):
        self.state();self.reviewed_models();self.assertIn('MODEL_REFERENCE_INVALID',self.codes())
    def test_domain_invariant_references_real_propositions(self):
        self.domain();self.mutate('domain_model',lambda c:c['invariants'][0].update(proposition_ids=['absent']));self.reviewed_models();self.assertIn('MODEL_REFERENCE_INVALID',self.codes())
    def test_included_adversarial_finding_requires_actual_case(self):
        self.adversarial();self.mutate('adversarial_review',lambda c:c['findings'][0].update(case_ids=[]));self.reviewed_models();self.assertIn('ADVERSARIAL_CASE_UNMAPPED',self.codes())
    def test_unresolved_adversarial_risk_is_not_hidden_by_review(self):
        self.adversarial();self.mutate('adversarial_review',lambda c:c.update(residual_risks=['Unmeasured boundary']));self.reviewed_models();self.assertIn('ADVERSARIAL_UNRESOLVED',self.codes())
    def test_excluded_adversarial_finding_requires_rationale(self):
        a=self.adversarial();a['content']['findings'][0].update(disposition='excluded',case_ids=[],rationale=' ');a['digest']=object_digest(a)
        with self.assertRaises(BugateError):validate_schema(a,'Artifact')
    def test_dimension_candidates_must_be_absorbed(self):
        self.domain();self.state();self.dimension();self.mutate('dimension_matrix',lambda c:c['dimensions'][0].update(included_case_ids=['missing']));self.reviewed_models();self.assertIn('MODEL_REFERENCE_INVALID',self.codes())
    def test_independence_requires_distinct_named_producer(self):
        self.requirements({'independent_review':True});self.assertIn('REVIEW_INDEPENDENCE_MISSING',self.codes())
        for a in self.w.objects('artifacts','Artifact'):
            if a['artifact_type']!='semantic_review':
                a['producer']='fixture-author';a['digest']=object_digest(a);write_json(self.w.root,'artifacts/'+a['id']+'.json',a)
        self.reviewed_models();self.assertEqual(self.result()['verdict'],'satisfied',self.result()['findings'])
        self.mutate('review',lambda c:c.update(reviewer='fixture-author'));self.assertIn('REVIEW_INDEPENDENCE_MISSING',self.codes())
    def test_unique_canonical_constraints_and_claim_dimensions(self):
        rules=quality_rules()
        for method in methods():
            self.assertNotIn('hard_constraints',method)
            self.assertEqual(method['hard_constraint_refs'],[rules['hard_constraint_set']])
        text=prepare(self.w.root)['render']['content']
        for constraint in rules['hard_constraints']:self.assertEqual(text.count(constraint),1)
        claims=json.loads((resource_root()/'schemas/claim.schema.json').read_text())['properties']['claim_type']['enum']
        self.assertEqual(set(claims),set(rules['claim_dimensions']))
    def test_evaluator_has_only_v2_inputs(self):
        import inspect,bugate.protocol
        self.assertNotIn('legacy',inspect.getsource(bugate.protocol.evaluator_identity))
        self.assertTrue(evaluator_identity().startswith('sha256:'))

if __name__=='__main__':unittest.main(verbosity=2)
