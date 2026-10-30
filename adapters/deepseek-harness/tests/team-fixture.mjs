import assert from 'node:assert/strict';
import {readFile,writeFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {host,FixtureModel,SessionId,toolResponse,textResponse,requestText,createUserMessage} from './host.mjs';
import {initializeProject,cli,cliEntrypoint,requirement,scopeTemplate,templates,runner} from './workflow-node.mjs';

/** Transparent test relay: the actual CLI produces every byte and exit status.
 * Only one exact successful review publication has its real output withheld so
 * the unchanged product collector reaches its real default 30-second timeout. */
export async function createTimeoutRelay(project){
 const entry=join(project,'team-timeout-relay.mjs'),marker=join(project,'team-timeout-publication.json'),output=join(project,'team-timeout-publication.stdout'),target='review-timeout-after-publish';
 const source=`import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const actual=${JSON.stringify(cliEntrypoint)},target=${JSON.stringify(target)},marker=${JSON.stringify(marker)},output=${JSON.stringify(output)};
const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);const input=Buffer.concat(chunks),argv=process.argv.slice(2);
let fault=false;try{fault=argv[0]==='artifact'&&argv[1]==='publish'&&JSON.parse(input.toString()).context.artifact.id===target;}catch{}
const started=Date.now(),child=spawn(process.execPath,[actual,...argv],{stdio:['pipe','pipe','pipe']}),stdout=[],stderr=[];
child.stdin.on('error',()=>{});child.stdin.end(input);
child.stdout.on('data',bytes=>{if(fault)stdout.push(bytes);else process.stdout.write(bytes);});
child.stderr.on('data',bytes=>{if(fault)stderr.push(bytes);else process.stderr.write(bytes);});
const outcome=await new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',(code,signal)=>resolve({code,signal}));});
if(fault&&outcome.code===0){
 const bytes=Buffer.concat(stdout),value=JSON.parse(bytes.toString()),task=argv[argv.indexOf('--task')+1];
 if(value.invocation!=='completed'||value.data?.id!==target)throw Error('Fault injection requires actual successful target publication');
 const artifact=JSON.parse(await readFile(task+'/artifacts/'+target+'.json','utf8'));
 if(artifact.digest!==value.data.digest)throw Error('Actual target publication was not durable');
 await writeFile(output,bytes);
 await writeFile(marker,JSON.stringify({target,actual_cli:actual,relay_pid:process.pid,child_pid:child.pid,child_exit:outcome,started_at:new Date(started).toISOString(),published_at:new Date().toISOString(),stdout_sha256:'sha256:'+createHash('sha256').update(bytes).digest('hex'),subject_ref:{id:artifact.id,digest:artifact.digest},withhold_ms:45000,product_default_timeout_ms:30000},null,2)+'\\n');
 await new Promise(resolve=>setTimeout(resolve,45000));
}
if(fault){process.stdout.write(Buffer.concat(stdout));process.stderr.write(Buffer.concat(stderr));}
if(outcome.signal)process.kill(process.pid,outcome.signal);else process.exitCode=outcome.code;
`;
 await writeFile(entry,source);return{entry,marker,output,target,actual_cli:cliEntrypoint,source_digest:'sha256:'+createHash('sha256').update(source).digest('hex'),withhold_ms:45000,product_default_timeout_ms:30000};
}

/** Only model bytes are deterministic. Team membership, tool execution, peer inbox,
 * persistence, provider IO, CAS and subprocesses are the installed official runtime. */
export class TeamModel extends FixtureModel {
 queues=new Map();
 phases=[];
 route(options){return requestText(options).match(/You are teammate "([^"]+)"/)?.[1]??'lead';}
 enqueue(member,...steps){const q=this.queues.get(member)??[];q.push(...steps);this.queues.set(member,q);}
 async *stream(options){
  this.requests.push(options);const member=this.route(options),phase=this.phases.find(p=>p.member===member&&!p.finished&&!p.failed&&!p.emittedDone);
  let response;
  if(phase&&requestText(options).includes(phase.token)){
   phase.request_indexes.push(this.requests.length-1);const next=phase.steps.shift();response=await next(options);
   for(const frame of response)if(frame.type==='block-end'&&frame.block.type==='tool-call')phase.calls.push({id:String(frame.block.id),name:frame.block.name,arguments:frame.block.arguments});
   if(!phase.steps.length)phase.emittedDone=true;
  }else{const next=this.queues.get(member)?.shift();response=next?await next(options):textResponse('This member has no activated fixture phase. No completion claim.');}
  for(const frame of response)yield frame;
 }
}

/** Test-only correlation over real host events. Tokens never establish authorship
 * or review completion; actual call/result/turn and durable signed facts do. */
export function createTeamPhaseDriver(h,lead,{task,budgetMs=300000}={}){
 const phases=[],waits=[],driverId=randomUUID();let sequence=0;
 const pending=name=>phases.find(p=>p.member===name&&!p.finished&&!p.failed&&!p.emittedDone);
 const activate=(name,text)=>{const p=pending(name);return p?text+'\n'+p.token:text;};
 const step=(name,args)=>()=>{const value=structuredClone(typeof args==='function'?args():args);if(name==='spawn_teammate')value.prompt=activate(value.name,value.prompt);if(name==='send_message')value.message=activate(value.target,value.message);return toolResponse(name,value,'team-call-'+driverId+'-'+(++sequence));};
 function queue(member,...steps){const token='BUGATE_FIXTURE_PHASE_'+randomUUID(),phase={id:phases.length+1,member,token,marker:token+' FINISHED',steps:[],calls:[],request_indexes:[],emittedDone:false,finished:false,failed:false,allowInterrupted:false};phase.steps=[...steps,()=>textResponse(phase.marker+'; quality remains subject to persisted BUGate facts.')];phases.push(phase);h.model.phases.push(phase);return phase;}
 const row=name=>h.ctx.agentTeams.listMembers(lead).find(m=>m.name===name);
 async function log(name,durable=false){const member=row(name);if(!member)return;const id=SessionId(String(member.id)),session=h.ctx.sessions.get(id);if(session){if(durable)assert.equal(await h.ctx.sessions.flush(session),true,'The exact phase session must flush before advancing');return{member,events:session.snapshotEvents(),source:durable?'live_flushed':'live'};}const value=await h.ctx.sessionQuery.readSession(id);return{member,events:value.events,source:'persisted_read'};}
 const textOf=message=>(message?.content??[]).filter(c=>c.type==='text').map(c=>c.text).join('\n');
 // Reconstruct the actual official inbox. A canceled splice without this exact
 // message is unrelated; a disposal cancellation is never substituted for the
 // earlier turn error which left this phase pending and unable to advance.
 function activation(phase,events){
  const inbox={'next-turn':[],'next-step':[]};let state;
  for(const event of events){
   if(event.type!=='agent/inbox/spliced')continue;
   const splice=event.data,removed=inbox[splice.target].splice(splice.start,splice.removedCount??0,...splice.inserted);
   for(const message of splice.inserted)if(textOf(message).includes(phase.token)&&(!phase.activationMessageId||String(message.id)===phase.activationMessageId))state={message_id:String(message.id),insert_seq:event.seq,insert_time:event.time,status:'pending'};
   if(state&&removed.some(message=>String(message.id)===state.message_id))Object.assign(state,{status:splice.outcome==='canceled'?'canceled':'claimed',remove_seq:event.seq,remove_time:event.time});
  }
  if(state){const admitted=events.find(e=>e.type==='user/message'&&String(e.data.id)===state.message_id);if(admitted)state.admitted_seq=admitted.seq;}
  return state;
 }
 async function rejectStoppedPhase(phase,current,audit){
  const input=activation(phase,current.events);if(!input)return;
  audit.activation=input;
  const terminal=current.events.findLast(e=>e.type==='turn/end'&&e.seq>input.insert_seq&&['error','aborted'].includes(e.data.reason.kind));
  // A running member may still be draining an expected cancellation. Evaluate
  // its real completed/interrupted boundary first; do not replace it with idle.
  if(input.status!=='canceled'&&(!terminal||current.member.status!=='inactive'))return;
  const durable=await log(phase.member,true);audit.activation=activation(phase,durable.events);audit.persistence=durable.source;
  audit.terminal_event=terminal??null;audit.calls=phase.calls;audit.unexecuted=phase.calls.length===0;
  throw Error('Actual phase '+phase.id+' for '+phase.member+' cannot advance: '+JSON.stringify({activation:audit.activation,terminal_event:audit.terminal_event}));
 }
 function phaseBoundary(phase,events){
  const outputs=phase.calls.map(call=>({call,invocation:events.find(e=>e.type==='tool/call'&&String(e.data.callId)===call.id),result:events.find(e=>e.type==='tool/result'&&String(e.data.message.toolCallId)===call.id)}));
  if(outputs.some(x=>!x.invocation||!x.result))return;
  for(const output of outputs){assert.equal(output.invocation.data.name,output.call.name);assert.equal(output.invocation.data.arguments,output.call.arguments);}
  const marker=events.find(e=>e.type==='assistant/message'&&textOf(e.data.message).includes(phase.marker));
  if(marker){const end=events.find(e=>e.type==='turn/end'&&e.seq>marker.seq&&e.data.turn===marker.data.turn);if(end){assert.equal(end.data.reason.kind,'completed','Normal phases require the actual completed turn, not crash balancing');return{outputs,end,marker};}}
  if(phase.allowInterrupted&&phase.steps.length===1&&outputs.length){const last=outputs.at(-1),end=events.find(e=>e.type==='turn/end'&&e.seq>last.result.seq&&e.data.turn===last.result.data.turn);if(end&&last.result.data.message.isError){assert.equal(end.data.reason.kind,'aborted');return{outputs,end,interrupted:true};}}
 }
 async function publications(boundary,sessionId){
  const verified=[];for(const output of boundary.outputs.filter(x=>x.call.name==='bugate_publish_artifact')){
   const directory=join(task,'.bugate/host-observations');let observations=[];try{observations=await Promise.all((await readdir(directory)).map(async file=>JSON.parse(await readFile(join(directory,file),'utf8'))));}catch(error){if(error.code!=='ENOENT')throw error;}
   const observation=observations.find(r=>r.execution.session_id===sessionId&&r.execution.call_id===output.call.id);
   if(boundary.interrupted){assert.ok(observation,'Interrupted-after-publication phase must retain its actual output');verified.push({call_id:output.call.id,observation_ref:observation.subject_ref,status:'interrupted_not_authorized'});continue;}
   const actual=JSON.parse(textOf(output.result.data.message));
   if(actual.invocation==='invalid_input'&&!observation){verified.push({call_id:output.call.id,status:'rejected_without_publication'});continue;}
   assert.ok(observation,'Every successful/timed-out publication phase must have an actual host observation');
   const artifact=JSON.parse(await readFile(join(task,'artifacts',observation.subject_ref.id+'.json'),'utf8'));assert.equal(artifact.digest,observation.subject_ref.digest);
   const settlement=JSON.parse(await readFile(join(task,'.bugate/host-settlements','settlement-'+observation.id+'.json'),'utf8'));assert.deepEqual(settlement.observation_ref,{id:observation.id,digest:observation.digest});assert.equal(settlement.call_id,output.call.id);assert.equal(settlement.result_event.seq,output.result.seq);assert.deepEqual(settlement.result_event.data,output.result.data);
   if(actual.invocation==='completed'){assert.deepEqual(artifact,actual.data,'Completed output is exactly the durably published artifact');assert.equal(settlement.status,'completed');}else if(actual.invocation==='timeout')assert.equal(settlement.status,'timeout');else assert.notEqual(settlement.status,'completed');
   verified.push({call_id:output.call.id,observation_id:observation.id,settlement_id:settlement.id,subject_ref:observation.subject_ref,status:settlement.status});
  }return verified;
 }
 async function waitPhase(phase){
  if(phase.finished)return;assert.equal(phase.failed,false,'A failed phase cannot be retried or replayed');const started=Date.now(),audit={kind:'phase',phase_id:phase.id,member:phase.member,token:phase.token,started_at:new Date(started).toISOString(),budget_ms:budgetMs,status:'waiting'};waits.push(audit);
  try{while(Date.now()-started<budgetMs){const current=await log(phase.member),boundary=current&&phaseBoundary(phase,current.events);if(boundary){const durable=await log(phase.member,true),confirmed=phaseBoundary(phase,durable.events);assert.ok(confirmed,'Flushed session must retain the exact phase boundary');audit.calls=confirmed.outputs.map(x=>({id:x.call.id,name:x.call.name,call_seq:x.invocation.seq,result_seq:x.result.seq}));audit.activation=activation(phase,durable.events);audit.turn_end={seq:confirmed.end.seq,time:confirmed.end.time,turn:confirmed.end.data.turn};audit.publications=await publications(confirmed,String(durable.member.id));audit.persistence=durable.source;audit.other_members=h.ctx.agentTeams.listMembers(lead).filter(m=>m.name!==phase.member);audit.status=confirmed.interrupted?'interrupted_not_authorized':'observed';phase.finished=true;phase.steps=[];return audit;}if(current)await rejectStoppedPhase(phase,current,audit);await new Promise(r=>setTimeout(r,100));}throw Error('Actual phase '+phase.id+' for '+phase.member+' did not complete within '+budgetMs+'ms');}
  catch(error){phase.failed=true;phase.steps=[];audit.status='failed';audit.error=String(error);throw error;}finally{audit.finished_at=new Date().toISOString();audit.elapsed_ms=Date.now()-started;}
 }
 async function send(agent,text){assert.equal(String(agent.session.id),String(lead.session.id),'Only the real Lead receives direct fixture input');const phase=pending('lead');assert.ok(phase,'Lead actions require an explicitly queued phase');const message=createUserMessage({content:[{type:'text',text:activate('lead',text)}],source:{kind:'user'}});phase.activationMessageId=String(message.id);agent.followup(message);await waitPhase(phase);}
 async function settle(){for(const phase of phases.filter(p=>!p.finished&&!p.failed))await waitPhase(phase);}
 const runMember=async(name,message)=>{if(!pending(name))queue(name);queue('lead',step('send_message',{target:name,message}));await send(lead,'Resume the named member through the official Team inbox.');await settle();};
 async function sendPeer(sender,target,message){
  const previous=await log(target),watermark=previous?.events.at(-1)?.seq??-1,requestWatermark=h.model.requests.length;queue(sender,step('send_message',{target,message}));const senderPhase=pending(sender);if(sender==='lead')await send(lead,'Send the bounded Team evidence exchange.');else await runMember(sender,'Send the prepared peer evidence exchange.');
  const sentCall=senderPhase.calls.find(c=>c.name==='send_message'),senderLog=await log(sender,true),sentResult=senderLog.events.find(e=>e.type==='tool/result'&&String(e.data.message.toolCallId)===sentCall?.id);assert.ok(sentResult);assert.equal(sentResult.data.message.isError,false);assert.equal(JSON.parse(textOf(sentResult.data.message)).sent,true,'The actual sender call must succeed; a received historical message alone is insufficient');
  const senderRow=row(sender),started=Date.now(),audit={kind:'peer_input',sender,target,sender_phase:senderPhase.id,sender_call_id:sentCall.id,sender_result_seq:sentResult.seq,after_seq:watermark,request_watermark:requestWatermark,started_at:new Date(started).toISOString(),budget_ms:budgetMs,status:'waiting'};waits.push(audit);
  try{while(Date.now()-started<budgetMs){const current=await log(target),boundary=current&&peerProcessingBoundary(current.events,h.model,{watermark,requestWatermark,senderSessionId:String(senderRow.id),target,message});if(boundary){const{received,response,end}=boundary,durable=await log(target,true);assert.ok(durable.events.some(e=>e.seq===received.seq&&e.data.id===received.data.id));assert.ok(durable.events.some(e=>e.seq===end.seq&&e.type==='turn/end'));Object.assign(audit,{status:'processed_and_flushed',session_id:String(current.member.id),message_id:received.data.id,message_seq:received.seq,model_response_seq:response.seq,turn_end_seq:end.seq,persistence:durable.source});return audit;}await new Promise(r=>setTimeout(r,100));}throw Error('Peer input from '+sender+' to '+target+' was not processed within '+budgetMs+'ms');}catch(error){audit.status='failed';audit.error=String(error);throw error;}finally{audit.finished_at=new Date().toISOString();audit.elapsed_ms=Date.now()-started;}
 }
 return{queue,step,send,settle,runMember,sendPeer,waits,phases,waitPhase};
}

export function peerProcessingBoundary(events,model,{watermark,requestWatermark,senderSessionId,target,message}){
 const received=events.find(e=>e.seq>watermark&&e.type==='user/message'&&e.data.source.kind==='agent-message'&&String(e.data.source.senderSessionId)===senderSessionId&&e.data.content.some(c=>c.type==='text'&&c.text.includes(message)));if(!received)return;
 const response=events.find(e=>e.seq>received.seq&&e.type==='assistant/message'&&e.data.message.source?.kind==='model'),end=response&&events.find(e=>e.type==='turn/end'&&e.seq>response.seq&&e.data.turn===response.data.turn),requested=model.requests.slice(requestWatermark).some(r=>model.route(r)===target&&requestText(r).includes(message));
 if(!end||!requested)return;assert.equal(end.data.reason.kind,'completed','Peer input processing requires its actual completed turn');return{received,response,end};
}
const observed=(value)=>{assert.equal(value.isError,false,JSON.stringify(value));if(value.value?.invocation)assert.equal(value.value.invocation,'completed',JSON.stringify(value));return value.value?.data??value.value;};
export async function teamAcceptance(project,plugin,{evidencePath,mount,negativeChecks}={}){
 const model=new TeamModel(),policy={perspectives:[{id:'oracle',proposition_ids:['P1'],necessity:'Check the source-defined exact result and the disputed zero boundary.',source_refs:['SRC1']},{id:'coverage',proposition_ids:['P1'],necessity:'Check that required positive, zero and negative branches exercise the declared Oracle.',source_refs:['SRC1']}],context:'fresh_required',max_rounds:6,cross_check_required:true};
 const task=await initializeProject(project,{teamPolicy:policy}),persistence=join(project,'sessions'),timeoutRelay=negativeChecks?await createTimeoutRelay(project):undefined,h=await host({model,persistence,team:mount?'infrastructure':true});
 let lead,waitObservations=[];const observations=[],records=new Map();let scopeState,scopeRef;
 h.ctx.on('tools/result',(exec,result)=>{observations.push({agent:String(exec.agent?.session.id),tool:exec.name,call:String(exec.callId),result});if(result.isError||result.value?.invocation!=='completed')return;const data=result.value.data;if(exec.name==='bugate_scope')scopeState=data;if(exec.name==='bugate_record_scope')scopeRef={id:data.id,revision:data.revision,digest:data.digest};if(exec.name==='bugate_publish_artifact')records.set(data.id,data);});
 h.ctx.tools.register({name:'inspect_contract',description:'Read the generated Profile-declared source and exact current review artifacts through the configured filesystem provider.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'object'},render:(_a,v)=>[{type:'text',text:JSON.stringify(v)}]},execute:async()=>{const source=new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(project,'requirements.md'))));const artifacts={};for(const name of ['brief','testability','inventory']){try{artifacts[name]=JSON.parse(new TextDecoder().decode(await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,'artifacts',name+'.json')))));}catch{}}const implementations={};for(const c of artifacts.inventory?.content?.cases??[]){const path=c.implementation?.path;if(!path||implementations[path])continue;try{const bytes=await h.ctx.fs.readBytes(await h.ctx.fs.resolve(join(task,path)));implementations[path]={content:new TextDecoder('utf-8',{fatal:true}).decode(bytes),digest:'sha256:'+createHash('sha256').update(bytes).digest('hex')};}catch(error){if(!['ENOENT','FS_NOT_FOUND'].includes(error?.code))throw error;}}return{source,artifacts,implementations};}});
 h.ctx.tools.register({name:'write',description:'Write a generated draft through configured filesystem provider.',parameters:{type:'object',properties:{file_path:{type:'string'},content:{type:'string'}},required:['file_path','content'],additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async args=>{await h.ctx.fs.writeText(await h.ctx.fs.resolve(args.file_path),args.content);return true;}});
 const executionCommands={normal:{argv:[process.execPath,'tests/runner.mjs','logs/team.xml'],caseIds:['C1','C2','C3'],report:'logs/team.xml',purpose:'test',environmentId:'local-fixture'}};
 const runtimeConfig={executionCommands,maxContextBytes:16000,...(timeoutRelay?{coreEntrypoint:timeoutRelay.entry}:{})};
 if(mount)await mount(h,{project,task,...runtimeConfig});else await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath,...runtimeConfig});
 lead=await h.ctx.agentLoop.create(SessionId('team-lead'),{provider:'fixture',model:'deterministic-team-fixture'},{cwd:task});
 const driver=createTeamPhaseDriver(h,lead,{task}),{step,queue,send,settle,runMember,sendPeer}=driver;waitObservations=driver.waits;
 const memberAgent=name=>{const row=h.ctx.agentTeams.listMembers(lead).find(m=>m.name===name);return row?h.ctx.agents.get(row.id):undefined;};
 function review(id,perspective,{open=false,cross=[],resolved=false}={}){const value=templates(scopeRef)[3];value.id=id;value.revision=String(Number(records.get(id)?.revision??0)+1);value.content.reviewer='A label is never identity';value.content.perspective_id=perspective;value.content.artifact_digests=Object.fromEntries(['brief','testability','inventory'].map(id=>[id,records.get(id).digest]));value.content.findings=perspective==='oracle'?[{id:'zero-boundary',proposition_id:'P1',statement:'SRC1 states zero and negative inputs produce zero; the required zero branch must have an explicit exact assertion; cross-check its presence in the inventory.',blocking:true,status:open?'open':'resolved',evidence_refs:['SRC1','O1'],counterevidence:resolved?['inventory']:[]}]:[];value.content.conclusion=open?'uncertain':'accepted';value.content.cross_checks=cross;return value;}
 function publish(value,update=false){return step('bugate_publish_artifact',()=>({artifact:typeof value==='function'?value():value,...(update?{expected_digest:records.get((typeof value==='function'?value():value).id).digest}:{})}));}
 function received(member,reviewId){const agent=memberAgent(member),ref=records.get(reviewId);assert.ok(agent);const event=agent.session.snapshotEvents().findLast(e=>e.type==='user/message'&&e.data.source.kind==='agent-message'&&JSON.stringify(e.data.content).includes(ref.digest));assert.ok(event,'The official Team message must have entered the actual target session');return event.data.id;}
 function cross(member,peer){return[{review_ref:{id:peer,digest:records.get(peer).digest},disposition:'accepted',finding_ids:['zero-boundary'],reasoning:'Compared the peer opinion with SRC1 and exact O1. The inventory includes an explicit test_zero case with input zero and expected zero, alongside positive and negative cases; the apparent omission is refuted by current evidence.',evidence_refs:['SRC1','O1','inventory'],message_id:String(received(member,peer))}];}
 async function assessCurrent(id,type,evidenceIds=[]){
  // Each is a distinct actual model/tool step. The host owns write ordering and
  // re-prepares after the write; no out-of-band task mutation races a notice.
  const phase=queue('lead',step('bugate_status',{}),step('write',()=>{
   const statusCall=phase.calls.find(c=>c.name==='bugate_status'),result=observations.find(o=>o.call===statusCall.id&&o.agent===String(lead.session.id)).result,current=observed(result);
   const claim={apiVersion:'bugate.io/v2',kind:'Claim',id,claim_type:type,task_id:current.task_id,workspace_id:current.workspace_id,statement:'Current exact source, Team review and actual observations support only this scoped claim.',requirement_ids:['R1'],artifact_ids:[...records.keys()],evidence_ids:evidenceIds,workspace_snapshot:current.workspace_snapshot,scope_ref:current.scope_ref,plan_ref:current.plan_ref,known_gaps:[],open_questions:[]};
   return{file_path:join(task,'claims',id+'.json'),content:JSON.stringify(claim)};
  }),step('bugate_assess',{claim:'claims/'+id+'.json'}));
  await send(lead,'Observe current facts, write the exact claim and assess '+id+' in distinct sequential native steps.');
  const writeCall=phase.calls.find(c=>c.name==='write'),writeResult=observations.find(o=>o.call===writeCall.id&&o.agent===String(lead.session.id)).result;assert.equal(writeResult.isError,false,JSON.stringify(writeResult));assert.equal(writeResult.value,true);
  const assessCall=phase.calls.find(c=>c.name==='bugate_assess'),result=observations.find(o=>o.call===assessCall.id&&o.agent===String(lead.session.id)).result;const data=observed(result);assert.equal(data.verdict,'satisfied',JSON.stringify(result));return data;
 }
 try{
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
  const assessment=await assessCurrent('team-design','test_design_complete');
  // Only after the pre-code design is accepted may the Lead implement the
  // requested exact assertions. This changes upstream bytes and requires fresh
  // review of the revised inventory; old opinions remain historical.
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
  const implementedDesign=await assessCurrent('team-implemented-design','test_design_complete');
  queue('lead',step('bugate_run_test',{command_id:'normal'}));await send(lead,'Execute the configured three exact assertions only after current implemented design and Team reviews are accepted.');await settle();
  const execution=observations.filter(o=>o.tool==='bugate_run_test').at(-1)?.result.value;assert.equal(execution?.invocation,'completed',JSON.stringify(execution));assert.equal(execution.data.execution.status,'passed');assert.equal(execution.data.execution.cases.length,3);assert.deepEqual(JSON.parse(await readFile(join(task,'evidence',execution.data.id+'.json'),'utf8')),execution.data,'Actual execution evidence is durable before tool success');
  const verified=await assessCurrent('team-requirement-verified','requirement_verified',[execution.data.id]);
  for(const name of ['oracle-reviewer','coverage-reviewer']){const member=h.ctx.agentTeams.listMembers(lead).find(m=>m.name===name);const reads=observations.filter(o=>o.agent===String(member.id)&&o.tool==='inspect_contract');assert.ok(reads.some(o=>o.result.value?.implementations?.['tests/runner.mjs']?.content===runner),'The actual '+name+' tool input must expose the implementation bytes before its code opinion');}
  const receipts=await Promise.all((await readdir(join(task,'.bugate/host-observations'))).map(async file=>JSON.parse(await readFile(join(task,'.bugate/host-observations',file),'utf8'))));
  assert.ok(receipts.some(r=>r.execution.member_name==='oracle-reviewer'&&r.execution.context_mode==='fresh'&&r.execution.parent_session_id===String(lead.session.id)));
  assert.ok(receipts.some(r=>r.messages.length>0));assert.ok(receipts.some(r=>r.subject_ref.digest===firstOpinion.digest),'Original dissent observation retained');
  assert.deepEqual(JSON.parse(await readFile(join(task,'artifacts/review-oracle.json'),'utf8')),records.get('review-oracle'),'Completed tool output has already been published');
  const report={status:'PASS',fixture_runtime_config:{maxContextBytes:16000,memberSettleDeadlineMs:300000,timeoutRelay},mode:'deterministic-model-real-runtime',live_model:'NOT_RUN',task,policy,versions:{node:process.version,dsh:'0.2.2-alpha.2',team:'0.2.2-alpha.2',cordis:'4.0.6-alpha.1'},members:h.ctx.agentTeams.listMembers(lead),tasks:h.ctx.agentTeams.listTasks(lead),original_opinion:firstOpinion,current_opinions:[records.get('review-oracle'),records.get('review-coverage')],assessment,implemented_design:implementedDesign,execution:execution.data,requirement_verification:verified,receipts,model_requests:model.requests.length,observations,waits:waitObservations};
  if(negativeChecks)report.negative_controls=await negativeChecks({h,lead,task,project,records,observations,queue,step,publish,review,send,runMember,sendPeer,received,settle,policy,timeoutRelay});
  // Restart the entire Cordis host, not only a Team member. Historical signed
  // outputs are recomputed from persisted session results under a new host key.
  const beforeRestart=cli('status','--task',task);assert.equal(beforeRestart.invocation,'completed');
  await h.ctx.fiber.dispose();
  const recoveredModel=new TeamModel(),recovered=await host({model:recoveredModel,persistence,team:mount?'infrastructure':true}),recoveredResults=[];
  try{
   if(mount)await mount(recovered,{project,task,...runtimeConfig});else await recovered.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath,...runtimeConfig});
   recovered.ctx.on('tools/result',(exec,result)=>recoveredResults.push({tool:exec.name,result}));
   const resumed=(await recovered.ctx.agents.resume({resumeSessionId:SessionId('team-lead'),agentOptions:{provider:'fixture',model:'deterministic-team-fixture'}})).agent;
   const recoveredDriver=createTeamPhaseDriver(recovered,resumed,{task});recoveredDriver.queue('lead',recoveredDriver.step('bugate_prepare',{}),recoveredDriver.step('bugate_status',{}));
   await recoveredDriver.send(resumed,'Recover the existing task and read the persisted exact facts after host restart.');await recovered.ctx.sessions.flush(resumed.session);
   for(const entry of recoveredResults){assert.equal(entry.result.isError,false,JSON.stringify(entry));assert.equal(entry.result.value.invocation,'completed',JSON.stringify(entry));}
   const afterRestart=cli('status','--task',task);assert.equal(afterRestart.invocation,'completed',JSON.stringify(afterRestart));assert.equal(afterRestart.data.workspace_snapshot,beforeRestart.data.workspace_snapshot,'A new runtime authority must not replace old immutable settlements or change the authorization snapshot');
   report.host_restart={before_snapshot:beforeRestart.data.workspace_snapshot,after_snapshot:afterRestart.data.workspace_snapshot,observations:recoveredResults,members:recovered.ctx.agentTeams.listMembers(resumed),waits:recoveredDriver.waits};
  }finally{await recovered.ctx.fiber.dispose();}
  if(evidencePath)await writeFile(evidencePath,JSON.stringify(report,null,2)+'\n');return{report,task,persistence,records,cli,project};
 }catch(error){await writeFile(join(project,'team-failure.json'),JSON.stringify({message:String(error),observations,waits:waitObservations,requests:model.requests.map(r=>({sessionId:r.sessionId,route:model.route(r)})),members:lead?h.ctx.agentTeams.listMembers(lead):[]},null,2));console.error('TEAM_FAILURE_ARTIFACT',join(project,'team-failure.json'));throw error;}finally{await h.ctx.fiber.dispose();}
}
