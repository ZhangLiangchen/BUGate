import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {toolResponse,textResponse,send,requestText} from './host.mjs';
export const requirement='R1: Positive integers retain their value; zero and negative integers produce zero. K1: Negative boundary.\n';
export async function initializeProject(project,python){
 await mkdir(project,{recursive:true});await writeFile(join(project,'requirements.md'),requirement);
 const profile={apiVersion:'bugate.io/v2',kind:'Profile',id:'neutral-profile',version:'1',sources:[{id:'SRC1',path:'requirements.md',digest:'sha256:'+createHash('sha256').update(requirement).digest('hex'),description:'Generated requirement contract'}],requirements:['R1'],risks:['K1'],goal:'Verify bounded integers',delivery_criteria:['business_understanding','test_design','implementation','execution','report','knowledge'],resource_boundaries:{network:false,destructive:false,notes:'Generated local fixture'},unknowns:[]};
 await writeFile(join(project,'profile.json'),JSON.stringify(profile));
 const result=JSON.parse(execFileSync(python,['-m','bugate','init','--project',project,'--task','same-task','--sut-profile',join(project,'profile.json'),'--json'],{encoding:'utf8',env:{...process.env,PYTHONPATH:''}}));
 assert.equal(result.invocation,'completed');return result.data.task_root;
}
export const runner=`import sys, xml.etree.ElementTree as E
from pathlib import Path
def bounded(value): return value if len(sys.argv)>2 and sys.argv[2]=='broken' else max(0,value)
root=E.Element('testsuite')
failed=0
for name,value,expected in [('test_positive',3,3),('test_negative',-1,0)]:
    case=E.SubElement(root,'testcase',classname='fixture',name=name)
    try: assert bounded(value)==expected, 'wrong business result'
    except AssertionError as exc:
        failed+=1;E.SubElement(case,'failure',message=str(exc))
E.ElementTree(root).write(sys.argv[1],encoding='utf-8')
print('executed',len(root),'cases')
sys.exit(1 if failed else 0)
`;
export const artifact=(id,type,content)=>({apiVersion:'bugate.io/v2',kind:'Artifact',id,artifact_type:type,schema_version:'2.1.0-dev.1',revision:'1',source_refs:['SRC1'],relations:[],content});
export function scopeTemplate(request){
 const refs=[{request_id:request.id,quote:requirement.trim()}];
 const necessity={kind:'user_explicit',request_refs:refs,explanation:'The direct request explicitly includes positive and negative integer behavior; both branches are needed, with no new risk categories.'};
 return{id:'acceptance',revision:1,request_refs:refs,source_refs:['SRC1'],objectives:[{id:'G1',statement:'Verify only the requested bounded integer results',request_refs:refs}],subject:{object:'Generated bounded integer function',environment_ids:['local-fixture'],context_boundaries:['Temporary Python process; no network or company SUT']},flows:[{id:'F1',objective_ids:['G1'],entry:'Integer argument',key_nodes:['Evaluate bounded value','Observe exact result'],terminal:'Expected integer returned',branch_of:null}],requirements:[{id:'R1',objective_ids:['G1'],oracle_ids:['O1'],basis:necessity}],risk_ids:['K1'],scenarios:['S1','S2'].map(id=>({id,requirement_ids:['R1'],flow_id:'F1',oracle_ids:['O1'],expected_evidence:['Exact result matches O1'],necessity})),exclusions:[{id:'EX1',description:'Security, load, concurrency and unrelated compatibility campaigns',requirement_ids:[]}],auxiliary:[{id:'read',purpose:'requirements_read',description:'Read the declared requirement source',related_scenario_ids:['S1','S2'],max_runs:10,stop_condition:'Source read or bounded failure'},{id:'diagnose',purpose:'diagnosis',description:'Inspect the observed integer failure',related_scenario_ids:['S1','S2'],max_runs:2,stop_condition:'Two probes or a discriminating observation'}],assumptions:[],completion:{execution_complete:'all_required_scenarios_observed',functional_pass:'all_required_oracles_passed',suggestions_block:false,preserve_failures:true},change:{previous_digest:null,request_refs:[],impact:'Initial finite scope from the direct user request'}};
}
export function templates(scopeRef){return[
 artifact('brief','business_brief',{propositions:[{id:'P1',statement:'Integer input preserves positive values and floors negatives to zero',source_refs:['SRC1'],requirement_ids:['R1'],classification:'fact'}],oracles:[{id:'O1',proposition_ids:['P1'],expected:'Exact integer result equals max(0,input)',observation:'Return value assertion',deadline:'Synchronous function return',preconditions:['Integer input'],source_refs:['SRC1']}],states:['integer input','bounded output'],boundaries:['zero separates positive and negative domains'],unknowns:[]}),
 artifact('testability','testability',{decisions:[{proposition_id:'P1',oracle_id:'O1',observation:'Exact integer return',deadline:'Synchronous',preconditions:['Generated local fixture'],layer:'unit',rationale:'Pure deterministic function',alternatives:['E2E adds no observable behavior'],side_effects:'None',environment:'Python local process'}],gaps:[]}),
 artifact('inventory','inventory',{scope_ref:scopeRef,cases:['test_positive','test_negative'].map((name,index)=>({id:'C'+(index+1),scenario_id:'S'+(index+1),classification:'required',proposition_ids:['P1'],oracle_ids:['O1'],risk_ids:['K1'],preconditions:['Integer fixture available'],expected_observations:['Exact result matches O1'],layer:'unit',layer_reason:'Pure function sufficient',implementation:{path:'tests/runner.py',digest:null},test_selector:'fixture.'+name,negative:index===1})),requirement_scope:['R1'],risk_scope:['K1']}),
 artifact('review','semantic_review',{reviewer:'deterministic-fixture reviewer',source_refs:['SRC1'],artifact_digests:{brief:null,testability:null,inventory:null},conclusion:'accepted',limitations:['Synthetic contract review; not live model quality evidence'],reasoning:'Every case compares an exact source-defined value, including the explicitly requested negative boundary.',scope_digest:scopeRef?.digest??null,scope_decisions:['S1','S2'].map(scenario_id=>({scenario_id,necessity:'accepted',reasoning:'The direct user request and SRC1 both state this integer branch; exact return assertion proves it without adding categories.',source_refs:['SRC1']})),change_authorization:'not_applicable'})
];}
/** Real DSH model/tool loop; only the LLM boundary is deterministic. */
export async function authorAndExecute(h,agent,task,{execute=false,exerciseScope=false,productFailure=false}={}){
 await mkdir(join(task,'.bugate/drafts'),{recursive:true});await mkdir(join(task,'tests'),{recursive:true});
 const records=new Map(),observations=[];let status,scopeState,scopeRef;let counter=0;
 h.ctx.on('tools/result',(exec,result)=>{observations.push({name:exec.name,result});if(result.isError)return;const value=result.value;if(value?.invocation!=='completed')return;if(exec.name==='bugate_status')status=value.data;if(exec.name==='bugate_scope')scopeState=value.data;if(exec.name==='bugate_record_scope')scopeRef={id:value.data.id,revision:value.data.revision,digest:value.data.digest};if(exec.name==='bugate_record_artifact')records.set(value.data?.id,value.data);});
 h.ctx.tools.register({name:'read_requirement',description:'Read the bound synthetic requirement',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'string'},render:(_a,v)=>[{type:'text',text:v}]},execute:async()=>new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,'../../requirements.md'))))});
 h.ctx.tools.register({name:'write',description:'Write a local task file through the configured provider',parameters:{type:'object',properties:{file_path:{type:'string'},content:{type:'string'}},required:['file_path','content'],additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async args=>{await h.ctx.fs.writeText(await h.ctx.fs.resolve(args.file_path),args.content);return true;}});
 function step(name,args){h.model.script.push(()=>toolResponse(name,typeof args==='function'?args():args,'workflow-'+(++counter)));}
 function record(value,update=false){step('write',{file_path:join(task,'.bugate/drafts',value.id+'.json'),content:JSON.stringify(value)});step('bugate_record_artifact',()=>({input:'.bugate/drafts/'+value.id+'.json',...(update?{expected_digest:records.get(value.id).digest}:{})}));}
 function assess(id,type,submit=false){step('bugate_status',{});step('write',()=>({file_path:join(task,'claims',id+'.json'),content:JSON.stringify({apiVersion:'bugate.io/v2',kind:'Claim',id,claim_type:type,task_id:status.task_id,workspace_id:status.workspace_id,statement:'Current generated contract facts support this scoped claim',requirement_ids:['R1'],artifact_ids:[...records.keys()],evidence_ids:observations.filter(x=>x.name==='bugate_run_test'&&!x.result.isError).map(x=>x.result.value.data.id),workspace_snapshot:status.workspace_snapshot,scope_ref:status.scope_ref,plan_ref:status.plan_ref,known_gaps:[],open_questions:[]})}));step('bugate_assess',{claim:'claims/'+id+'.json',submit});}
 step('read_requirement',{});step('bugate_scope',{});step('write',()=>({file_path:join(task,'.bugate/drafts/scope.json'),content:JSON.stringify(scopeTemplate(scopeState.requests.find(r=>r.message.includes(requirement.trim()))))}));step('bugate_record_scope',{input:'.bugate/drafts/scope.json'});h.model.script.push(textResponse('Finite source-backed acceptance scope recorded'));
 await send(agent,requirement+'Accept only these explicitly requested positive and negative branches. Record unrelated risks as suggestions.');
 assert.ok(scopeRef?.digest,JSON.stringify(observations));
 for(const value of templates(scopeRef))record(value);assess('pre-code-design','test_design_complete');h.model.script.push(textResponse('Source-backed design recorded and assessed'));
 await send(agent,'Read the bound source, establish business Oracles, record and assess pre-code design.');
 const precode=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;assert.equal(precode?.data?.verdict,'satisfied',JSON.stringify(precode??observations));
 assert.equal(observations.some(x=>x.name==='bugate_run_test'),false);
 if(execute){
   step('write',{file_path:join(task,'tests/runner.py'),content:runner});
   for(const value of templates(scopeRef).slice(2))record(value,true);
   assess('implemented-design','test_design_complete');
   if(exerciseScope){
     const extension=templates(scopeRef)[2];extension.revision='2';extension.content.cases.push({...extension.content.cases[0],id:'C-security',scenario_id:'S-security',test_selector:'fixture.unrelated_security'});
     step('write',{file_path:join(task,'.bugate/drafts/unaccepted-inventory.json'),content:JSON.stringify(extension)});
     step('bugate_record_artifact',()=>({input:'.bugate/drafts/unaccepted-inventory.json',expected_digest:records.get('inventory').digest}));
     step('bugate_run_test',{command_id:'outside'});
     step('write',{file_path:join(task,'.bugate/drafts/security-suggestion.json'),content:JSON.stringify({id:'security-suggestion',description:'A separate security review could examine additional threat categories',rationale:'This is outside the requested integer branches and has no current necessity source',source_refs:['SRC1']})});
     step('bugate_record_suggestion',{input:'.bugate/drafts/security-suggestion.json'});
   }
   step('bugate_run_test',{command_id:'normal'});h.model.script.push(textResponse('Managed real execution captured'));await send(agent,'Implement the accepted cases and execute only the configured in-scope tests.');
   const execution=observations.find(x=>x.name==='bugate_run_test'&&!x.result.isError)?.result.value;
   assert.equal(execution?.data?.source_type,'host_observed',JSON.stringify(execution??observations));assert.equal(execution.data.execution.status,productFailure?'failed':'passed');assert.equal(execution.data.execution.cases.length,2);
   assert.deepEqual(JSON.parse(await readFile(join(task,'evidence',execution.data.id+'.json'))),execution.data,'tool result returns only after durable evidence persistence');
   if(exerciseScope&&productFailure){
     for(const id of ['diagnose1','diagnose2','diagnose3'])step('bugate_run_test',{command_id:id});
     h.model.script.push(textResponse('The failure remains recorded. Two bounded diagnostics were observed; the third was denied.'));
     await send(agent,'Diagnose only the observed in-scope failure within the declared two-run budget, then preserve the conclusion.');
     const diagnostics=observations.filter(x=>x.name==='bugate_run_test'&&!x.result.isError&&x.result.value.data.execution.activity_id==='diagnose');
     assert.equal(diagnostics.length,2);assert.ok(diagnostics.every(x=>x.result.value.data.execution.cases.length===0),'Auxiliary diagnostics must not become acceptance cases');
     await assert.rejects(readFile(join(task,'logs/diagnose3-executed')),error=>error.code==='ENOENT');
   }
   assess('verified-requirement','requirement_verified');h.model.script.push(textResponse('Current requirement verification assessed'));
   await send(agent,'Assess the executed requirement against this exact current snapshot and observed case results.');
   const verified=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;
   assert.equal(verified?.data?.verdict,productFailure?'unsatisfied':'satisfied',JSON.stringify(verified??observations));
   if(exerciseScope){
     const denied=observations.filter(x=>x.name==='bugate_run_test'&&x.result.isError);
     assert.equal(denied.length,productFailure?2:1);assert.match(denied[0].result.error.message,/SCENARIO_OUT_OF_SCOPE|SCOPE_ACTIVITY_DENIED/);if(productFailure)assert.match(denied[1].result.error.message,/DIAGNOSTIC_LIMIT_REACHED/);
     const rejectedPlan=observations.find(x=>x.name==='bugate_record_artifact'&&x.result.value?.invocation==='invalid_input');assert.equal(rejectedPlan?.result.value.errors[0].code,'SCENARIO_OUT_OF_SCOPE');
     assert.equal(JSON.parse(await readFile(join(task,'artifacts/inventory.json'))).digest,records.get('inventory').digest,'Rejected expansion must not replace the accepted plan');
     await assert.rejects(readFile(join(task,'outside-executed')),error=>error.code==='ENOENT');
     assess('complete-execution','execution_complete',true);h.model.script.push(textResponse('Acceptance execution complete; preserve the actual functional verdict'));await send(agent,'Assess whether the agreed acceptance execution is complete.');
     const complete=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;assert.equal(complete?.data?.verdict,'satisfied',JSON.stringify(complete??observations));
     const before=await readFile(join(task,'claims/complete-execution.json'),'utf8');
     for(const id of ['performance-suggestion','compatibility-suggestion']){
       step('write',{file_path:join(task,'.bugate/drafts',id+'.json'),content:JSON.stringify({id,description:'Possible separate '+id.replace('-suggestion','')+' review',rationale:'Not required by the finite current user objective',source_refs:['SRC1']})});
       step('bugate_record_suggestion',{input:'.bugate/drafts/'+id+'.json'});
     }
     step('bugate_assess',{claim:'claims/complete-execution.json',submit:true});step('bugate_scope',{});h.model.script.push(textResponse('This conclusion covers only the agreed acceptance scope; suggestions remain advisory.'));
     await send(agent,'Record these out-of-scope ideas as suggestions and finish the same agreed acceptance.');
     assert.equal(await readFile(join(task,'claims/complete-execution.json'),'utf8'),before,'suggestions must not require rewriting the completion claim');
     const after=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;assert.equal(after?.data?.verdict,'satisfied',JSON.stringify(after??observations));
     assert.equal(scopeState.suggestions.length,4);
     assert.equal(agent.session.snapshotEvents().filter(e=>e.type==='user/message'&&String(e.data.source.kind)==='bugate-rework').length,0,'suggestions and actual product failures must not trigger completion rework');
   }
 }
 for(const item of observations){if(exerciseScope&&item.name==='bugate_run_test'&&item.result.isError)continue;if(exerciseScope&&item.name==='bugate_record_artifact'&&item.result.value?.invocation==='invalid_input'&&item.result.value.errors[0]?.code==='SCENARIO_OUT_OF_SCOPE')continue;assert.equal(item.result.isError,false,JSON.stringify(item));if(item.name.startsWith('bugate_'))assert.equal(item.result.value.invocation,'completed',JSON.stringify(item));}
 assert.ok(h.model.requests.some(r=>requestText(r).includes(requirement.trim())));return{requests:h.model.requests.length,scope_ref:scopeRef,scope_exercise:exerciseScope,product_failure:productFailure,requirement_assessment:observations.find(x=>x.name==='bugate_assess'&&x.result.value?.data?.claim_id==='verified-requirement')?.result.value.data,completion_assessment:observations.filter(x=>x.name==='bugate_assess'&&x.result.value?.data?.claim_id==='complete-execution').at(-1)?.result.value.data,suggestions:scopeState?.suggestions,observations:observations.map(x=>({tool:x.name,isError:x.result.isError,error:x.result.error,invocation:x.result.value?.invocation,kind:x.result.value?.data?.kind,verdict:x.result.value?.data?.verdict})),evidence:observations.find(x=>x.name==='bugate_run_test'&&!x.result.isError)?.result.value.data};
}
