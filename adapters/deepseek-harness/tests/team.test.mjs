import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import * as plugin from '../plugin/lib/index.js';
import {teamAcceptance,TeamModel,createTeamPhaseDriver,peerProcessingBoundary} from './team-fixture.mjs';
import {negativeTeamChecks} from './team-negative.mjs';
import {host,SessionId,requestText} from './host.mjs';
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
