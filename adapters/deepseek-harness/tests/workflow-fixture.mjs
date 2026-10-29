import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {toolResponse,textResponse,send,requestText} from './host.mjs';
const requirement='R1: Positive integers retain their value; zero and negative integers produce zero. K1: Negative boundary.\n';
export async function initializeProject(project,python){
 await mkdir(project,{recursive:true});await writeFile(join(project,'requirements.md'),requirement);
 const profile={apiVersion:'bugate.io/v2',kind:'Profile',id:'neutral-profile',version:'1',sources:[{id:'SRC1',path:'requirements.md',digest:'sha256:'+createHash('sha256').update(requirement).digest('hex'),description:'Generated requirement contract'}],requirements:['R1'],risks:['K1'],goal:'Verify bounded integers',delivery_criteria:['business_understanding','test_design','implementation','execution','report','knowledge'],resource_boundaries:{network:false,destructive:false,notes:'Generated local fixture'},unknowns:[]};
 await writeFile(join(project,'profile.json'),JSON.stringify(profile));
 const result=JSON.parse(execFileSync(python,['-m','bugate','init','--project',project,'--task','same-task','--sut-profile',join(project,'profile.json'),'--json'],{encoding:'utf8',env:{...process.env,PYTHONPATH:''}}));
 assert.equal(result.invocation,'completed');return result.data.task_root;
}
export const runner=`import sys, xml.etree.ElementTree as E
from pathlib import Path
def bounded(value): return max(0,value)
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
const artifact=(id,type,content)=>({apiVersion:'bugate.io/v2',kind:'Artifact',id,artifact_type:type,schema_version:'2.0.0',revision:'1',source_refs:['SRC1'],relations:[],content});
export function templates(){return[
 artifact('brief','business_brief',{propositions:[{id:'P1',statement:'Integer input preserves positive values and floors negatives to zero',source_refs:['SRC1'],requirement_ids:['R1'],classification:'fact'}],oracles:[{id:'O1',proposition_ids:['P1'],expected:'Exact integer result equals max(0,input)',observation:'Return value assertion',deadline:'Synchronous function return',preconditions:['Integer input'],source_refs:['SRC1']}],states:['integer input','bounded output'],boundaries:['zero separates positive and negative domains'],unknowns:[]}),
 artifact('testability','testability',{decisions:[{proposition_id:'P1',oracle_id:'O1',observation:'Exact integer return',deadline:'Synchronous',preconditions:['Generated local fixture'],layer:'unit',rationale:'Pure deterministic function',alternatives:['E2E adds no observable behavior'],side_effects:'None',environment:'Python local process'}],gaps:[]}),
 artifact('inventory','inventory',{cases:['test_positive','test_negative'].map((name,index)=>({id:'C'+(index+1),proposition_ids:['P1'],oracle_ids:['O1'],risk_ids:['K1'],preconditions:['Integer fixture available'],expected_observations:['Exact result matches O1'],layer:'unit',layer_reason:'Pure function sufficient',implementation:{path:'tests/runner.py',digest:null},test_selector:'fixture.'+name,negative:index===1})),requirement_scope:['R1'],risk_scope:['K1']}),
 artifact('review','semantic_review',{reviewer:'deterministic-fixture reviewer',source_refs:['SRC1'],artifact_digests:{brief:null,testability:null,inventory:null},conclusion:'accepted',limitations:['Synthetic contract review; not live model quality evidence'],reasoning:'Every case compares an exact source-defined value, including the negative boundary.'})
];}
/** Real DSH model/tool loop; only the LLM boundary is deterministic. */
export async function authorAndExecute(h,agent,task,{execute=false}={}){
 await mkdir(join(task,'.bugate/drafts'),{recursive:true});await mkdir(join(task,'tests'),{recursive:true});
 const records=new Map(),observations=[];let status;let counter=0;
 h.ctx.on('tools/result',(exec,result)=>{observations.push({name:exec.name,result});if(result.isError)return;const value=result.value;if(exec.name==='bugate_status')status=value.data;if(exec.name==='bugate_record_artifact')records.set(value.data?.id,value.data);});
 h.ctx.tools.register({name:'read_requirement',description:'Read the bound synthetic requirement',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'string'},render:(_a,v)=>[{type:'text',text:v}]},execute:async()=>new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,'../../requirements.md'))))});
 h.ctx.tools.register({name:'write',description:'Write a local task file through the configured provider',parameters:{type:'object',properties:{file_path:{type:'string'},content:{type:'string'}},required:['file_path','content'],additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async args=>{await h.ctx.fs.writeText(await h.ctx.fs.resolve(args.file_path),args.content);return true;}});
 function step(name,args){h.model.script.push(()=>toolResponse(name,typeof args==='function'?args():args,'workflow-'+(++counter)));}
 function record(value,update=false){step('write',{file_path:join(task,'.bugate/drafts',value.id+'.json'),content:JSON.stringify(value)});step('bugate_record_artifact',()=>({input:'.bugate/drafts/'+value.id+'.json',...(update?{expected_digest:records.get(value.id).digest}:{})}));}
 function assess(id,type){step('bugate_status',{});step('write',()=>({file_path:join(task,'claims',id+'.json'),content:JSON.stringify({apiVersion:'bugate.io/v2',kind:'Claim',id,claim_type:type,task_id:status.task_id,workspace_id:status.workspace_id,statement:'Current generated contract facts support this scoped claim',requirement_ids:['R1'],artifact_ids:[...records.keys()],evidence_ids:observations.filter(x=>x.name==='bugate_run_test').map(x=>x.result.value.data.id),workspace_snapshot:status.workspace_snapshot,known_gaps:[],open_questions:[]})}));step('bugate_assess',{claim:'claims/'+id+'.json'});}
 step('read_requirement',{});for(const value of templates())record(value);assess('pre-code-design','test_design_complete');h.model.script.push(textResponse('Source-backed design recorded and assessed'));
 await send(agent,'Read the bound source, establish business Oracles, record and assess pre-code design.');
 const precode=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;assert.equal(precode?.data?.verdict,'satisfied',JSON.stringify(precode));
 assert.equal(observations.some(x=>x.name==='bugate_run_test'),false);
 if(execute){
   step('write',{file_path:join(task,'tests/runner.py'),content:runner});
   for(const value of templates().slice(2))record(value,true);
   assess('implemented-design','test_design_complete');step('bugate_run_test',{command_id:'normal'});h.model.script.push(textResponse('Managed real execution captured'));await send(agent,'Implement the accepted cases and execute the configured tests.');
   const execution=observations.find(x=>x.name==='bugate_run_test')?.result.value;
   assert.equal(execution?.data?.source_type,'host_observed',JSON.stringify(execution));assert.equal(execution.data.execution.status,'passed');assert.equal(execution.data.execution.cases.length,2);
   assert.deepEqual(JSON.parse(await readFile(join(task,'evidence',execution.data.id+'.json'))),execution.data,'tool result returns only after durable evidence persistence');
   assess('verified-requirement','requirement_verified');h.model.script.push(textResponse('Current requirement verification assessed'));
   await send(agent,'Assess the executed requirement against this exact current snapshot and observed case results.');
   const verified=observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value;
   assert.equal(verified?.data?.verdict,'satisfied',JSON.stringify(verified));
 }
 for(const item of observations){assert.equal(item.result.isError,false,JSON.stringify(item));if(item.name.startsWith('bugate_'))assert.equal(item.result.value.invocation,'completed',JSON.stringify(item));}
 assert.ok(h.model.requests.some(r=>requestText(r).includes(requirement.trim())));return{requests:h.model.requests.length,requirement_assessment:execute?observations.filter(x=>x.name==='bugate_assess').at(-1)?.result.value.data:undefined,observations:observations.map(x=>({tool:x.name,invocation:x.result.value?.invocation,kind:x.result.value?.data?.kind,verdict:x.result.value?.data?.verdict})),evidence:observations.find(x=>x.name==='bugate_run_test')?.result.value.data};
}
