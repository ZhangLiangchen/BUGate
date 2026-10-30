import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {host,FixtureModel,SessionId,toolResponse,textResponse,send,requestText} from './host.mjs';
import {initializeProject,cli,requirement,scopeTemplate,templates,runner} from './workflow-node.mjs';

/** Only model bytes are deterministic. Team membership, tool execution, peer inbox,
 * persistence, provider IO, CAS and subprocesses are the installed official runtime. */
export class TeamModel extends FixtureModel {
 queues=new Map();
 route(options){return requestText(options).match(/You are teammate "([^"]+)"/)?.[1]??'lead';}
 enqueue(member,...steps){const q=this.queues.get(member)??[];q.push(...steps);this.queues.set(member,q);}
 async *stream(options){this.requests.push(options);const next=this.queues.get(this.route(options))?.shift();const response=next?await next(options):textResponse('This member has no pending fixture action. No completion claim.');for(const frame of response)yield frame;}
}
const observed=(value)=>{assert.equal(value.isError,false,JSON.stringify(value));if(value.value?.invocation)assert.equal(value.value.invocation,'completed',JSON.stringify(value));return value.value?.data??value.value;};
export async function teamAcceptance(project,plugin,{evidencePath,mount,negativeChecks}={}){
 const model=new TeamModel(),policy={perspectives:[{id:'oracle',proposition_ids:['P1'],necessity:'Check the source-defined exact result and the disputed zero boundary.',source_refs:['SRC1']},{id:'coverage',proposition_ids:['P1'],necessity:'Check that required positive, zero and negative branches exercise the declared Oracle.',source_refs:['SRC1']}],context:'fresh_required',max_rounds:6,cross_check_required:true};
 const task=await initializeProject(project,{teamPolicy:policy}),persistence=join(project,'sessions'),h=await host({model,persistence,team:mount?'infrastructure':true});
 let lead;const observations=[],records=new Map();let scopeState,scopeRef,sequence=0;
 h.ctx.on('tools/result',(exec,result)=>{observations.push({agent:String(exec.agent?.session.id),tool:exec.name,call:String(exec.callId),result});if(result.isError||result.value?.invocation!=='completed')return;const data=result.value.data;if(exec.name==='bugate_scope')scopeState=data;if(exec.name==='bugate_record_scope')scopeRef={id:data.id,revision:data.revision,digest:data.digest};if(exec.name==='bugate_publish_artifact')records.set(data.id,data);});
 h.ctx.tools.register({name:'inspect_contract',description:'Read the generated Profile-declared source and exact current review artifacts through the configured filesystem provider.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'object'},render:(_a,v)=>[{type:'text',text:JSON.stringify(v)}]},execute:async()=>{const source=new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(project,'requirements.md'))));const artifacts={};for(const name of ['brief','testability','inventory']){try{artifacts[name]=JSON.parse(new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,'artifacts',name+'.json')))));}catch{}}const implementations={};for(const c of artifacts.inventory?.content?.cases??[]){const path=c.implementation?.path;if(!path||implementations[path])continue;try{const bytes=await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,path)));implementations[path]={content:new TextDecoder('utf-8',{fatal:true}).decode(bytes),digest:'sha256:'+createHash('sha256').update(bytes).digest('hex')};}catch(error){if(!['ENOENT','FS_NOT_FOUND'].includes(error?.code))throw error;}}return{source,artifacts,implementations};}});
 h.ctx.tools.register({name:'write',description:'Write a generated draft through configured filesystem provider.',parameters:{type:'object',properties:{file_path:{type:'string'},content:{type:'string'}},required:['file_path','content'],additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async args=>{await h.ctx.fs.writeText(await h.ctx.fs.resolve(args.file_path),args.content);return true;}});
 const executionCommands={normal:{argv:[process.execPath,'tests/runner.mjs','logs/team.xml'],caseIds:['C1','C2','C3'],report:'logs/team.xml',purpose:'test',environmentId:'local-fixture'}};
 if(mount)await mount(h,{project,task,executionCommands,maxContextBytes:16000});else await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath,executionCommands,maxContextBytes:16000});
 lead=await h.ctx.agentLoop.create(SessionId('team-lead'),{provider:'fixture',model:'deterministic-team-fixture'},{cwd:task});
 const step=(name,args)=>()=>toolResponse(name,typeof args==='function'?args():args,'team-call-'+(++sequence));
 const queue=(member,...steps)=>model.enqueue(member,...steps,()=>textResponse('The bounded fixture step is complete; quality remains subject to BUGate assessment.'));
 const flush=async()=>{await h.ctx.sessions.flush(lead.session);};
 async function settle(){for(let i=0;i<4800;i++){const members=h.ctx.agentTeams.listMembers(lead);const running=members.filter(m=>m.status==='running'||m.status==='provisioning');if(!running.length){await flush();return;}await new Promise(r=>setTimeout(r,25));}throw Error('Actual Team failed to settle within fixture bound');}
 const memberAgent=name=>{const row=h.ctx.agentTeams.listMembers(lead).find(m=>m.name===name);return row?h.ctx.agents.get(row.id):undefined;};
 const runMember=async(name,message)=>{queue('lead',step('send_message',{target:name,message}));await send(lead,'Resume the named member through the official Team inbox.');await settle();};
 const sendPeer=async(sender,target,message)=>{queue(sender,step('send_message',{target,message}));if(sender==='lead'){await send(lead,'Send the bounded Team evidence exchange.');await settle();}else await runMember(sender,'Send the prepared peer evidence exchange.');};
 function review(id,perspective,{open=false,cross=[],resolved=false}={}){const value=templates(scopeRef)[3];value.id=id;value.revision=String(Number(records.get(id)?.revision??0)+1);value.content.reviewer='A label is never identity';value.content.perspective_id=perspective;value.content.artifact_digests=Object.fromEntries(['brief','testability','inventory'].map(id=>[id,records.get(id).digest]));value.content.findings=perspective==='oracle'?[{id:'zero-boundary',proposition_id:'P1',statement:'SRC1 states zero and negative inputs produce zero; the required zero branch must have an explicit exact assertion; cross-check its presence in the inventory.',blocking:true,status:open?'open':'resolved',evidence_refs:['SRC1','O1'],counterevidence:resolved?['inventory']:[]}]:[];value.content.conclusion=open?'uncertain':'accepted';value.content.cross_checks=cross;return value;}
 function publish(value,update=false){return step('bugate_publish_artifact',()=>({artifact:typeof value==='function'?value():value,...(update?{expected_digest:records.get((typeof value==='function'?value():value).id).digest}:{})}));}
 function received(member,reviewId){const agent=memberAgent(member),ref=records.get(reviewId);assert.ok(agent);const event=agent.session.snapshotEvents().findLast(e=>e.type==='user/message'&&e.data.source.kind==='agent-message'&&JSON.stringify(e.data.content).includes(ref.digest));assert.ok(event,'The official Team message must have entered the actual target session');return event.data.id;}
 function cross(member,peer){return[{review_ref:{id:peer,digest:records.get(peer).digest},disposition:'accepted',finding_ids:['zero-boundary'],reasoning:'Compared the peer opinion with SRC1 and exact O1. The inventory includes an explicit test_zero case with input zero and expected zero, alongside positive and negative cases; the apparent omission is refuted by current evidence.',evidence_refs:['SRC1','O1','inventory'],message_id:String(received(member,peer))}];}
 try{
  await mkdir(join(task,'.bugate/drafts'),{recursive:true});
  queue('lead',step('inspect_contract',{}),step('bugate_scope',{}),step('write',()=>({file_path:join(task,'.bugate/drafts/scope.json'),content:JSON.stringify(scopeTemplate(scopeState.requests.find(r=>r.message.includes(requirement.trim()))))})),step('bugate_record_scope',{input:'.bugate/drafts/scope.json'}));
  await send(lead,requirement+'Review only the accepted finite branches using the Profile-required oracle and coverage perspectives.');
  assert.ok(scopeRef?.digest,JSON.stringify(observations));
  queue('lead',...templates(scopeRef).slice(0,3).map(value=>publish(value)));await send(lead,'Publish source-backed business, testability and plan artifacts as your own actual inline outputs.');
  for(const id of ['brief','testability','inventory'])assert.ok(records.has(id),JSON.stringify(observations));
  // Review A records genuine dissent, which is deliberately retained before rebuttal.
  queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>review('review-oracle','oracle',{open:true})));
  queue('lead',step('team_task_create',{subject:'Review exact bounded result',description:'Finite Profile oracle perspective; preserve disagreement.',write_scopes:['artifacts/review-oracle.json']}),step('spawn_teammate',{name:'oracle-reviewer',description:'Check source Oracle and counterevidence',prompt:'Inspect the Profile-declared requirement and actual artifacts. Publish your own oracle perspective; preserve the disputed boundary and limitations.',context:'fresh'}));
  await send(lead,'Create the finite official Team review task and the oracle member.');await settle();assert.ok(records.has('review-oracle'),JSON.stringify(observations));
  const firstOpinion=structuredClone(records.get('review-oracle'));
  queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-coverage','coverage')));
  queue('lead',step('team_task_create',{subject:'Cross-check required branch coverage',description:'Finite Profile coverage perspective; use actual peer evidence.',write_scopes:['artifacts/review-coverage.json']}),step('spawn_teammate',{name:'coverage-reviewer',description:'Check required case coverage',prompt:'Inspect the actual requirement and plan. Publish your own coverage perspective; use Team messages to cross-check the oracle objection.',context:'fresh'}));
  await send(lead,'Create the independent fresh coverage member through official Team tools.');await settle();assert.ok(records.has('review-coverage'),JSON.stringify(observations));
  await sendPeer('oracle-reviewer','coverage-reviewer',JSON.stringify({review_ref:{id:firstOpinion.id,digest:firstOpinion.digest},finding_id:'zero-boundary',question:'Please cross-check this source interpretation against the required scenarios and O1.'}));
  queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-coverage','coverage',{cross:cross('coverage-reviewer','review-oracle')}),true));
  await runMember('coverage-reviewer','Publish your evidenced cross-check of the received original opinion.');
  const coverageOpinion=structuredClone(records.get('review-coverage'));
  await sendPeer('coverage-reviewer','oracle-reviewer',JSON.stringify({review_ref:{id:coverageOpinion.id,digest:coverageOpinion.digest},finding_id:'zero-boundary',counterevidence:['SRC1','O1','inventory'],position:'The scope requires positive, zero and negative scenarios. The current inventory has a dedicated zero case bound to O1; the alleged missing branch is refuted.'}));
  queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>review('review-oracle','oracle',{resolved:true,cross:cross('oracle-reviewer','review-coverage')}),true));
  await runMember('oracle-reviewer','Recheck the evidence and record your own disposition, preserving the original dissent in history.');
  // Each member marks only its official Team task complete. Assessment remains separate.
  for(const [name,id]of[['oracle-reviewer','task-1'],['coverage-reviewer','task-2']]){queue(name,step('team_task_update',{task_id:id,expected_revision:1,action:'claim'}),step('team_task_update',{task_id:id,expected_revision:2,action:'complete'}));await runMember(name,'Close your actual Team task after publishing evidence; this does not authorize BUGate quality.');}
  const status=cli('status','--task',task);assert.equal(status.invocation,'completed',JSON.stringify(status));
  const claim={apiVersion:'bugate.io/v2',kind:'Claim',id:'team-design',claim_type:'test_design_complete',task_id:status.data.task_id,workspace_id:status.data.workspace_id,statement:'The exact source-backed design has both required real Team perspectives and evidenced disagreement disposition.',requirement_ids:['R1'],artifact_ids:[...records.keys()],evidence_ids:[],workspace_snapshot:status.data.workspace_snapshot,scope_ref:status.data.scope_ref,plan_ref:status.data.plan_ref,known_gaps:[],open_questions:[]};
  await writeFile(join(task,'claims/team-design.json'),JSON.stringify(claim));queue('lead',step('bugate_assess',{claim:'claims/team-design.json'}));await send(lead,'Assess the current exact design after durable cross-review; do not infer pass from Team completion.');
  const assessment=observations.filter(x=>x.tool==='bugate_assess').at(-1)?.result.value;assert.equal(assessment?.data?.verdict,'satisfied',JSON.stringify(assessment??observations));
  // Only after the pre-code design is accepted may the Lead implement the
  // requested exact assertions. This changes upstream bytes and requires fresh
  // review of the revised inventory; old opinions remain historical.
  await mkdir(join(task,'tests'),{recursive:true});
  const inventory=templates(scopeRef)[2];inventory.revision='2';
  queue('lead',step('write',{file_path:join(task,'tests/runner.mjs'),content:runner}),publish(inventory,true));
  await send(lead,'Implement the accepted positive, zero and negative assertions and publish the revised inventory with actual implementation bytes.');await settle();
  const invalidated=cli('status','--task',task);assert.ok(invalidated.data.findings.some(f=>f.code==='REVIEW_BINDING_STALE'||f.code==='SEMANTIC_REVIEW_STALE'),'Implementation changes must invalidate prior review inputs');
  queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>review('review-oracle','oracle',{resolved:true}),true));
  await runMember('oracle-reviewer','Inspect the revised actual implementation/inventory and publish a fresh source-backed opinion for this changed snapshot.');
  queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-coverage','coverage'),true));
  await runMember('coverage-reviewer','Inspect the revised implementation and publish the current branch-coverage opinion.');
  await sendPeer('oracle-reviewer','coverage-reviewer',JSON.stringify({review_ref:{id:'review-oracle',digest:records.get('review-oracle').digest},finding_id:'zero-boundary',position:'The revised implementation has a real zero assertion; independently verify it against O1 and the source.'}));
  queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-coverage','coverage',{cross:cross('coverage-reviewer','review-oracle')}),true));
  await runMember('coverage-reviewer','Publish your evidenced cross-check on the revised current input.');
  await sendPeer('coverage-reviewer','oracle-reviewer',JSON.stringify({review_ref:{id:'review-coverage',digest:records.get('review-coverage').digest},finding_id:'zero-boundary',counterevidence:['SRC1','O1','inventory'],position:'All three required branches have exact assertion code; preserve this finite scope.'}));
  queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>review('review-oracle','oracle',{resolved:true,cross:cross('oracle-reviewer','review-coverage')}),true));
  await runMember('oracle-reviewer','Record the bounded final cross-check for the actual revised implementation.');
  async function assessCurrent(id,type,evidenceIds=[]){const current=cli('status','--task',task);assert.equal(current.invocation,'completed',JSON.stringify(current));const claim={apiVersion:'bugate.io/v2',kind:'Claim',id,claim_type:type,task_id:current.data.task_id,workspace_id:current.data.workspace_id,statement:'Current exact source, Team review and actual observations support only this scoped claim.',requirement_ids:['R1'],artifact_ids:[...records.keys()],evidence_ids:evidenceIds,workspace_snapshot:current.data.workspace_snapshot,scope_ref:current.data.scope_ref,plan_ref:current.data.plan_ref,known_gaps:[],open_questions:[]};await writeFile(join(task,'claims',id+'.json'),JSON.stringify(claim));queue('lead',step('bugate_assess',{claim:'claims/'+id+'.json'}));await send(lead,'Assess '+id+' against the exact current observed facts.');const result=observations.filter(o=>o.tool==='bugate_assess').at(-1)?.result.value;assert.equal(result?.data?.verdict,'satisfied',JSON.stringify(result));return result.data;}
  const implementedDesign=await assessCurrent('team-implemented-design','test_design_complete');
  queue('lead',step('bugate_run_test',{command_id:'normal'}));await send(lead,'Execute the configured three exact assertions only after current implemented design and Team reviews are accepted.');await settle();
  const execution=observations.filter(o=>o.tool==='bugate_run_test').at(-1)?.result.value;assert.equal(execution?.invocation,'completed',JSON.stringify(execution));assert.equal(execution.data.execution.status,'passed');assert.equal(execution.data.execution.cases.length,3);assert.deepEqual(JSON.parse(await readFile(join(task,'evidence',execution.data.id+'.json'),'utf8')),execution.data,'Actual execution evidence is durable before tool success');
  const verified=await assessCurrent('team-requirement-verified','requirement_verified',[execution.data.id]);
  for(const name of ['oracle-reviewer','coverage-reviewer']){const member=h.ctx.agentTeams.listMembers(lead).find(m=>m.name===name);const reads=observations.filter(o=>o.agent===String(member.id)&&o.tool==='inspect_contract');assert.ok(reads.some(o=>o.result.value?.implementations?.['tests/runner.mjs']?.content===runner),'The actual '+name+' tool input must expose the implementation bytes before its code opinion');}
  const receipts=await Promise.all((await readdir(join(task,'.bugate/host-observations'))).map(async file=>JSON.parse(await readFile(join(task,'.bugate/host-observations',file),'utf8'))));
  assert.ok(receipts.some(r=>r.execution.member_name==='oracle-reviewer'&&r.execution.context_mode==='fresh'&&r.execution.parent_session_id===String(lead.session.id)));
  assert.ok(receipts.some(r=>r.messages.length>0));assert.ok(receipts.some(r=>r.subject_ref.digest===firstOpinion.digest),'Original dissent observation retained');
  assert.deepEqual(JSON.parse(await readFile(join(task,'artifacts/review-oracle.json'),'utf8')),records.get('review-oracle'),'Completed tool output has already been published');
  const report={status:'PASS',fixture_runtime_config:{maxContextBytes:16000},mode:'deterministic-model-real-runtime',live_model:'NOT_RUN',task,policy,versions:{node:process.version,dsh:'0.2.2-alpha.2',team:'0.2.2-alpha.2',cordis:'4.0.6-alpha.1'},members:h.ctx.agentTeams.listMembers(lead),tasks:h.ctx.agentTeams.listTasks(lead),original_opinion:firstOpinion,current_opinions:[records.get('review-oracle'),records.get('review-coverage')],assessment:assessment.data,implemented_design:implementedDesign,execution:execution.data,requirement_verification:verified,receipts,model_requests:model.requests.length,observations};
  if(negativeChecks)report.negative_controls=await negativeChecks({h,lead,task,project,records,observations,queue,step,publish,review,runMember,sendPeer,received,settle,policy});
  // Restart the entire Cordis host, not only a Team member. Historical signed
  // outputs are recomputed from persisted session results under a new host key.
  const beforeRestart=cli('status','--task',task);assert.equal(beforeRestart.invocation,'completed');
  await h.ctx.fiber.dispose();
  const recoveredModel=new TeamModel(),recovered=await host({model:recoveredModel,persistence,team:mount?'infrastructure':true}),recoveredResults=[];
  try{
   if(mount)await mount(recovered,{project,task,executionCommands,maxContextBytes:16000});else await recovered.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath,executionCommands,maxContextBytes:16000});
   recovered.ctx.on('tools/result',(exec,result)=>recoveredResults.push({tool:exec.name,result}));
   const resumed=(await recovered.ctx.agents.resume({resumeSessionId:SessionId('team-lead'),agentOptions:{provider:'fixture',model:'deterministic-team-fixture'}})).agent;
   recoveredModel.enqueue('lead',()=>toolResponse('bugate_prepare',{},'cold-host-prepare'),()=>toolResponse('bugate_status',{},'cold-host-status'),()=>textResponse('Recomputed persisted facts without replacing historical provenance.'));
   await send(resumed,'Recover the existing task and read the persisted exact facts after host restart.');await recovered.ctx.sessions.flush(resumed.session);
   for(const entry of recoveredResults){assert.equal(entry.result.isError,false,JSON.stringify(entry));assert.equal(entry.result.value.invocation,'completed',JSON.stringify(entry));}
   const afterRestart=cli('status','--task',task);assert.equal(afterRestart.invocation,'completed',JSON.stringify(afterRestart));assert.equal(afterRestart.data.workspace_snapshot,beforeRestart.data.workspace_snapshot,'A new runtime authority must not replace old immutable settlements or change the authorization snapshot');
   report.host_restart={before_snapshot:beforeRestart.data.workspace_snapshot,after_snapshot:afterRestart.data.workspace_snapshot,observations:recoveredResults,members:recovered.ctx.agentTeams.listMembers(resumed)};
  }finally{await recovered.ctx.fiber.dispose();}
  if(evidencePath)await writeFile(evidencePath,JSON.stringify(report,null,2)+'\n');return{report,task,persistence,records,cli,project};
 }catch(error){await writeFile(join(project,'team-failure.json'),JSON.stringify({message:String(error),observations,requests:model.requests.map(r=>({sessionId:r.sessionId,route:model.route(r)})),members:lead?h.ctx.agentTeams.listMembers(lead):[]},null,2));console.error('TEAM_FAILURE_ARTIFACT',join(project,'team-failure.json'));throw error;}finally{await h.ctx.fiber.dispose();}
}
