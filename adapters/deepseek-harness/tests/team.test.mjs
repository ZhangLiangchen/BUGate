import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import * as plugin from '../plugin/lib/index.js';
import {teamAcceptance,TeamModel,createTeamPhaseDriver,peerProcessingBoundary} from './team-fixture.mjs';
import {negativeTeamChecks} from './team-negative.mjs';
import {host,SessionId,requestText,FsLocal} from './host.mjs';
import {initializeProject,templates} from './workflow-node.mjs';

test('Official DSH Team: phase-specific completion survives an active Lead notice without consuming an unactivated phase',async()=>{
 const project=await mkdtemp(join(tmpdir(),'bugate-team-phase-')),task=await initializeProject(project),model=new TeamModel(),h=await host({model,persistence:join(project,'sessions'),team:true});let releaseChild,releaseNotice,noticeTurn,noticeEntered=false,probes=0,recovered;
 const childGate=new Promise(r=>{releaseChild=r;}),noticeGate=new Promise(r=>{releaseNotice=r;});
 try{
  await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath});
  h.ctx.tools.register({name:'phase_pause',description:'Hold this neutral fixture child until its Lead spawn phase ends.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async()=>{await childGate;return true;}});
  h.ctx.tools.register({name:'phase_probe',description:'Observe one activated neutral fixture action.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async()=>{probes++;return true;}});
  const lead=await h.ctx.agentLoop.create(SessionId('phase-lead'),{provider:'fixture',model:'deterministic-team-fixture'},{cwd:task}),driver=createTeamPhaseDriver(h,lead,{task});
  const child=driver.queue('phase-author',driver.step('phase_pause',{}),driver.step('bugate_publish_artifact',{artifact:templates(null)[0]}));
  driver.queue('lead',driver.step('spawn_teammate',{name:'phase-author',description:'Actual inline author for phase durability',prompt:'Publish the neutral source-backed brief as your own actual inline output.',context:'fresh'}));
  await driver.send(lead,'Create the actual official Team author.');
  h.ctx.on('agent/pre-step',async(payload,next)=>{if(payload.agent===lead&&!noticeEntered&&payload.messages.some(m=>m.source.kind==='subagent-settled')){noticeEntered=true;noticeTurn=payload.turn;await noticeGate;}return next();});
  releaseChild();const completed=await driver.waitPhase(child);
  const bounded=async(predicate,description)=>{const start=Date.now();while(Date.now()-start<300000){if(predicate())return;await new Promise(r=>setTimeout(r,25));}throw Error(description);};
  await bounded(()=>noticeEntered,'Actual Lead notice did not enter the controlled pre-step');
  const roster=h.ctx.agentTeams.listMembers(lead);assert.equal(roster.find(m=>m.name==='lead').status,'running','The former global-idle condition is demonstrably false');assert.equal(completed.publications[0].status,'completed','Exact artifact, observation, frozen result and settlement already persisted');
  const future=driver.queue('lead',driver.step('phase_probe',{})),requestStart=model.requests.length;releaseNotice();
  await bounded(()=>lead.session.snapshotEvents().some(e=>e.type==='turn/end'&&e.data.turn===noticeTurn),'The controlled notification turn did not finish');assert.equal(await h.ctx.sessions.flush(lead.session),true);
  const noticeRequests=model.requests.slice(requestStart);assert.ok(noticeRequests.length>0,'The notification really reached the model');assert.ok(noticeRequests.every(r=>!requestText(r).includes(future.token)));assert.equal(future.calls.length,0,'An unrelated notification must not consume an unactivated queued phase');assert.equal(probes,0);
  await driver.send(lead,'Execute the explicitly activated next Lead phase.');assert.equal(probes,1);assert.equal(future.finished,true);
  const peerText='Precise phase input watermark: retain this same content in history.',peer=await driver.sendPeer('lead','phase-author',peerText),childRow=h.ctx.agentTeams.listMembers(lead).find(m=>m.name==='phase-author'),peerLog=await h.ctx.sessionQuery.readSession(SessionId(String(childRow.id)));
  const peerOptions={watermark:peer.after_seq,requestWatermark:peer.request_watermark,senderSessionId:String(lead.session.id),target:'phase-author',message:peerText};assert.ok(peerProcessingBoundary(peerLog.events,model,peerOptions));assert.equal(peerProcessingBoundary(peerLog.events,model,{...peerOptions,requestWatermark:model.requests.length}),undefined,'Historical identical model input cannot satisfy a later request watermark');
  const interrupted=driver.queue('phase-author',driver.step('phase_probe',{}));interrupted.allowInterrupted=true;let interruption;
  const stopInterrupt=h.ctx.on('tools/post-execute',async(exec,result,next)=>{const decision=await next();if(exec.name==='phase_probe'&&String(exec.agent?.session.id)===String(childRow.id))interruption=h.ctx.agentTeams.interrupt(lead,'phase-author');return decision;});
  await assert.rejects(driver.sendPeer('lead','phase-author','Process the real interrupted-turn negative control.'),/Peer input processing requires its actual completed turn/);stopInterrupt();assert.ok(interruption);await driver.waitPhase(interrupted);assert.equal(driver.waits.at(-1).status,'interrupted_not_authorized');
  const oldCallIds=driver.phases.flatMap(p=>p.calls.map(c=>c.id));await h.ctx.fiber.dispose();
  const recoveredModel=new TeamModel();recovered=await host({model:recoveredModel,persistence:join(project,'sessions'),team:true});await recovered.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath});
  const resumed=(await recovered.ctx.agents.resume({resumeSessionId:SessionId('phase-lead'),agentOptions:{provider:'fixture',model:'deterministic-team-fixture'}})).agent,recoveredDriver=createTeamPhaseDriver(recovered,resumed,{task}),restartPhase=recoveredDriver.queue('lead',recoveredDriver.step('bugate_status',{}));
  await recoveredDriver.send(resumed,'Read current facts after recreating the driver and host around the same durable Lead session.');assert.ok(restartPhase.calls.every(c=>!oldCallIds.includes(c.id)),'New driver calls never reuse historical IDs in the restored Lead session');assert.equal(restartPhase.finished,true);
  const report={status:'PASS',mode:'deterministic-model-real-official-team',project,task,old_global_idle:false,roster_during_notice:roster,completed_publication_phase:completed,notification_turn:noticeTurn,future_phase:{id:future.id,calls:future.calls,finished:future.finished},peer_input:peer,historical_request_rejected:true,actual_interrupted_peer_rejected:true,interruption,waits:driver.waits,restart:{session_id:String(resumed.session.id),calls:restartPhase.calls,waits:recoveredDriver.waits,old_call_ids:oldCallIds}};if(process.env.BUGATE_PHASE_EVIDENCE)await writeFile(process.env.BUGATE_PHASE_EVIDENCE,JSON.stringify(report,null,2)+'\n');
 }finally{releaseChild();releaseNotice();await recovered?.ctx.fiber.dispose();await h.ctx.fiber.dispose();}
});

test('Official DSH Team: real member provenance, bounded perspectives, disagreement, cross-checks and durable exact artifact binding',async()=>{
 const root=await mkdtemp(join(tmpdir(),'bugate-team-acceptance-'));
 await teamAcceptance(join(root,'project'),plugin,{evidencePath:process.env.BUGATE_TEAM_EVIDENCE,negativeChecks:negativeTeamChecks});
});

test('Official DSH Team: serialized native writes recover from a failed notice without replaying its unadmitted phase',async()=>{
 const project=await mkdtemp(join(tmpdir(),'bugate-team-write-phase-')),task=await initializeProject(project),model=new TeamModel(),entered=Promise.withResolvers(),release=Promise.withResolvers(),childRelease=Promise.withResolvers();let armed=false,noticeTurn,lead,probes=0;
 // This is the one deliberate external-writer negative control. All ordinary
 // task writes below use the actual configured provider in distinct tool steps.
 class PausedLocalFs extends FsLocal{async listDir(target,...args){if(armed&&this.processPath(target)===task){armed=false;entered.resolve();await release.promise;}return super.listDir(target,...args);}}
 const h=await host({model,persistence:join(project,'sessions'),team:true,fs:PausedLocalFs}),observations=[];
 try{
  h.ctx.on('agent/pre-step',async(payload,next)=>{if(payload.agent===lead&&noticeTurn===undefined&&payload.messages.some(m=>m.source.kind==='subagent-settled')){noticeTurn=payload.turn;armed=true;}return next();});
  await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath});
  h.ctx.on('tools/result',(exec,result)=>observations.push({agent:String(exec.agent?.session.id),tool:exec.name,call:String(exec.callId),result}));
  h.ctx.tools.register({name:'phase_pause',description:'Finish this child only after its real Lead spawn phase ends.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async()=>{await childRelease.promise;return true;}});
  h.ctx.tools.register({name:'phase_probe',description:'Count a real activated Lead action.',parameters:{type:'object',properties:{},additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async()=>{probes++;return true;}});
  h.ctx.tools.register({name:'write',description:'Write the neutral claim through the actual configured filesystem provider.',parameters:{type:'object',properties:{file_path:{type:'string'},content:{type:'string'}},required:['file_path','content'],additionalProperties:false},output:{schema:{type:'boolean'},render:()=>[]},execute:async args=>{await h.ctx.fs.writeText(await h.ctx.fs.resolve(args.file_path),args.content);return true;}});
  lead=await h.ctx.agentLoop.create(SessionId('write-phase-lead'),{provider:'fixture',model:'deterministic-team-fixture'},{cwd:task});
  const driver=createTeamPhaseDriver(h,lead,{task});driver.queue('notice-source',driver.step('phase_pause',{}));driver.queue('lead',driver.step('spawn_teammate',{name:'notice-source',description:'Actual official Team notice source',prompt:'Finish this bounded fixture turn without any quality claim.',context:'fresh'}));
  await driver.send(lead,'Create the actual Team member whose completion will wake the Lead.');
  childRelease.resolve();
  await Promise.race([entered.promise,new Promise((_,reject)=>setTimeout(()=>reject(Error('The actual Lead notice did not reach filesystem verification')),30000).unref())]);
  const rejectedPhase=driver.queue('lead',driver.step('phase_probe',{})),rejectWait=assert.rejects(driver.send(lead,'This queued phase must remain unexecuted when the in-flight notice fails.'),/cannot advance:.*Filesystem has a file absent from the subprocess witness: claims\/deliberate-external-race\.txt/s);
  const racePath=join(task,'claims/deliberate-external-race.txt');await writeFile(racePath,'Intentional negative control: inserted after the real core witness and before filesystem enumeration.\n');release.resolve();await rejectWait;
  const failure=driver.waits.at(-1);assert.equal(failure.status,'failed');assert.equal(failure.unexecuted,true);assert.equal(failure.activation.status,'pending','The real input is still pending; no cleanup cancellation has happened');assert.equal(failure.terminal_event.data.turn,noticeTurn);assert.equal(failure.terminal_event.data.reason.kind,'error');assert.ok(failure.elapsed_ms<30000,'A terminal inactive error must end the wait before its 300-second deadline');assert.equal(rejectedPhase.calls.length,0);assert.equal(rejectedPhase.finished,false);assert.equal(rejectedPhase.failed,true);assert.equal(probes,0);
  const failureEvents=lead.session.snapshotEvents();assert.ok(!failureEvents.some(e=>e.type==='agent/inbox/spliced'&&e.data.outcome==='canceled'),'Disposal cancellation is not the observed cause');
  const claimPath=join(task,'claims/serialized-native.json');await assert.rejects(readFile(claimPath),error=>error.code==='ENOENT');
  const phase=driver.queue('lead',driver.step('bugate_status',{}),driver.step('write',()=>{
   const statusCall=phase.calls.find(c=>c.name==='bugate_status'),result=observations.find(o=>o.call===statusCall.id).result;assert.equal(result.isError,false);assert.equal(result.value.invocation,'completed');const state=result.value.data;
   return{file_path:claimPath,content:JSON.stringify({apiVersion:'bugate.io/v2',kind:'Claim',id:'serialized-native',claim_type:'test_design_complete',task_id:state.task_id,workspace_id:state.workspace_id,statement:'A deliberately incomplete neutral design must be assessed against actual current facts.',requirement_ids:['R1'],artifact_ids:[],evidence_ids:[],workspace_snapshot:state.workspace_snapshot,scope_ref:state.scope_ref,plan_ref:state.plan_ref,known_gaps:[],open_questions:[]})};
  }),driver.step('bugate_assess',{claim:'claims/serialized-native.json'}),driver.step('phase_probe',{}));
  await driver.send(lead,'Read current status, write this claim, assess it and observe one action through distinct native steps.');
  assert.equal(probes,1,'The failed phase was never replayed; only the new explicitly activated probe ran');assert.equal(rejectedPhase.calls.length,0);assert.equal(phase.finished,true);assert.deepEqual(phase.calls.map(c=>c.name),['bugate_status','write','bugate_assess','phase_probe']);
  const completed=driver.waits.at(-1),events=lead.session.snapshotEvents();for(let i=1;i<completed.calls.length;i++)assert.ok(completed.calls[i].call_seq>completed.calls[i-1].result_seq,'Every write-dependent action starts after the prior actual final result');
  const results=phase.calls.map(c=>observations.find(o=>o.call===c.id));for(const observed of results){assert.equal(observed.agent,String(lead.session.id));assert.equal(observed.result.isError,false,JSON.stringify(observed));}assert.equal(results[2].result.value.invocation,'completed');assert.notEqual(results[2].result.value.data.verdict,'satisfied','A working fixture must not turn an incomplete design into a quality pass');
  assert.equal(JSON.parse(await readFile(claimPath,'utf8')).workspace_snapshot,results[0].result.value.data.workspace_snapshot);assert.equal(await h.ctx.sessions.flush(lead.session),true);
  const report={status:'PASS',mode:'deterministic-model-real-official-team',project,task,deliberate_external_race:{path:racePath,notice_turn:noticeTurn,terminal_event:failure.terminal_event},failed_unadmitted_phase:failure,failed_phase_not_replayed:true,older_error_did_not_poison_new_phase:true,sequential_native_phase:completed,tool_results:results,events_at_failure:failureEvents,final_event_seq:events.at(-1).seq,waits:driver.waits};if(process.env.BUGATE_PHASE_RACE_EVIDENCE)await writeFile(process.env.BUGATE_PHASE_RACE_EVIDENCE,JSON.stringify(report,null,2)+'\n');
 }finally{release.resolve();childRelease.resolve();await h.ctx.fiber.dispose();}
});
