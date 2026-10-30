import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {readFile,readdir,writeFile,mkdir,cp} from 'node:fs/promises';
import {join} from 'node:path';
import {cli,initializeProject,cliEntrypoint} from './workflow-node.mjs';

/** Counterexamples use real official Team executions; invalid imported material
 * and deliberate mutations remain in their temporary task as negative evidence. */
export async function negativeTeamChecks(api){
 const {h,lead,task,project,records,observations,queue,step,publish,review,send,runMember,sendPeer,received,settle,policy,timeoutRelay}=api,checks=[];
 const codes=()=>{const value=cli('status','--task',task);assert.equal(value.invocation,'completed',JSON.stringify(value));return value.data.findings.map(x=>x.code);};
 const checkpoint=(name,expected)=>{const actual=codes();for(const code of expected)assert.ok(actual.includes(code),JSON.stringify({name,expected,actual}));checks.push({name,expected,observed_codes:[...new Set(actual)]});};
 // A failed publish with no durable output must not poison subsequent
 // preparation or lawful auxiliary/status work in the same actual member.
 queue('oracle-reviewer',step('bugate_publish_artifact',{artifact:{apiVersion:'bugate.io/v2',kind:'Artifact',id:'invalid-no-output'}}),step('bugate_publish_artifact',()=>{const artifact=review('review-null-target','oracle',{resolved:true});artifact.content.artifact_digests=Object.fromEntries(Object.keys(artifact.content.artifact_digests).map(id=>[id,null]));return{artifact};}),step('bugate_status',{}),step('bugate_prepare',{}));
 await runMember('oracle-reviewer','Exercise malformed output rejection, then read and prepare the task without claiming review completion.');
 const malformed=observations.find(o=>o.tool==='bugate_publish_artifact'&&o.result.value?.errors?.some(e=>e.code==='STRUCTURE_INVALID'));
 assert.ok(malformed,'Malformed actual member output must be rejected and retained');
 const repaired=observations.filter(o=>o.tool==='bugate_prepare').at(-1);assert.equal(repaired?.result.value?.invocation,'completed',JSON.stringify(repaired));
 checks.push({name:'Failed publication without observation preserves error and permits later preparation',rejected:malformed,subsequent_prepare:repaired});
 const nullTarget=observations.filter(o=>o.tool==='bugate_publish_artifact'&&o.result.value?.errors?.some(e=>e.code==='REVIEW_BINDING_STALE')).at(-1);assert.ok(nullTarget,'An observed model review with null target digests must be rejected rather than rebound to current bytes');await assert.rejects(readFile(join(task,'artifacts/review-null-target.json')),error=>error.code==='ENOENT');checks.push({name:'Native null target digest cannot silently bind an unobserved current artifact',result:nullTarget});
 const firstReceiptName=(await readdir(join(task,'.bugate/host-observations')))[0],realReceipt=JSON.parse(await readFile(join(task,'.bugate/host-observations',firstReceiptName),'utf8'));
 const forged=structuredClone(realReceipt.host_context);forged.artifact.id='forged-cli-author';forged.execution.arguments.artifact.id='forged-cli-author';
 const attempt=spawnSync(process.execPath,[cliEntrypoint,'artifact','publish','--task',task,'--host-observation-stdin','--json'],{encoding:'utf8',env:{...process.env,BUGATE_HOST_TOKEN:'self-selected-token'},input:JSON.stringify({token:'self-selected-token',context:forged})});
 const forgedResult=JSON.parse(attempt.stdout);assert.equal(attempt.status,2);assert.ok(forgedResult.errors.some(e=>e.code==='REVIEW_PROVENANCE_MISSING'),JSON.stringify(forgedResult));checks.push({name:'Self-supplied CLI token and actual-shaped model JSON cannot mint a host signature',result:forgedResult});
 // Delay only the exact successful real CLI publication's output. The genuine
 // product collector, not the model or fixture, creates the timeout outcome.
 assert.ok(timeoutRelay);
 queue('timeout-reviewer',step('inspect_contract',{}),publish(()=>review(timeoutRelay.target,'coverage')));
 queue('lead',step('spawn_teammate',{name:'timeout-reviewer',description:'Actual collector timeout after durable publication',prompt:'Inspect the current contract and publish your own exact coverage opinion. The fixture delays only the real CLI response after durable publication; do not claim timeout output completed.',context:'fresh'}));
 await send(lead,'Create the real Team publication timeout control.');await settle();
 const timedResult=observations.find(o=>o.tool==='bugate_publish_artifact'&&o.result.value?.invocation==='timeout'&&o.result.value.errors?.some(e=>e.code==='CORE_TIMEOUT'));assert.ok(timedResult,'The unchanged real Node collector must produce CORE_TIMEOUT');
 await runMember('timeout-reviewer','Resume through the official Team inbox, read persisted timeout facts, and do not publish a replacement opinion.');
 const fault=JSON.parse(await readFile(timeoutRelay.marker,'utf8')),actualOutput=JSON.parse(await readFile(timeoutRelay.output,'utf8'));assert.equal(fault.child_exit.code,0);assert.equal(fault.child_exit.signal,null);assert.equal(actualOutput.invocation,'completed');assert.equal(actualOutput.data.id,timeoutRelay.target);
 const timedReceipts=await Promise.all((await readdir(join(task,'.bugate/host-observations'))).map(async file=>JSON.parse(await readFile(join(task,'.bugate/host-observations',file),'utf8')))),timedObservation=timedReceipts.find(r=>r.subject_ref.id===timeoutRelay.target);assert.ok(timedObservation,'The real CLI published an observation before its response timed out');assert.deepEqual(timedObservation.subject_ref,fault.subject_ref);assert.equal(timedObservation.status,'published');
 const timedSettlement=JSON.parse(await readFile(join(task,'.bugate/host-settlements','settlement-'+timedObservation.id+'.json'),'utf8'));assert.equal(timedSettlement.status,'timeout');assert.equal(timedSettlement.result_event.data.message.toolCallId,timedResult.call);assert.ok(JSON.stringify(timedSettlement.result_event).includes('CORE_TIMEOUT'));
 for(const pid of [fault.relay_pid,fault.child_pid])assert.throws(()=>process.kill(pid,0),{code:'ESRCH'},'Actual collector and child must have exited after drain');
 const timedStatus=cli('status','--task',task);assert.ok(timedStatus.data.findings.some(f=>f.code==='REVIEW_PROVENANCE_MISSING'&&f.subject===timeoutRelay.target),'A timed-out review publication must never authorize current review');checks.push({name:'Actual Team publication timeout after durable output cannot complete review; real collector drains and settlement preserves timeout',fault,relay:timeoutRelay,result:timedResult,observation:timedObservation,settlement:timedSettlement,findings:timedStatus.data.findings,observed_relay_to_result_ms:timedSettlement.result_event.time-Date.parse(fault.started_at),processes_exited:[fault.relay_pid,fault.child_pid]});
 // A changed digest must not make an earlier genuine unresolved objection
 // vanish. The original member need not agree with the evidenced peer disposition.
 queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>{const a=review('review-revision-dissent','oracle',{open:true});a.content.findings[0].id='revision-zero-boundary';return a;}));
 await runMember('oracle-reviewer','Record an unresolved source-bound zero assertion objection for the historical-dissent revision control.');
 const originalDissent=structuredClone(records.get('review-revision-dissent'));
 const revisedInventory=structuredClone(records.get('inventory'));delete revisedInventory.digest;revisedInventory.revision=String(Number(revisedInventory.revision)+1);
 queue('lead',publish(revisedInventory,true));await send(lead,'Publish a new inventory revision without deleting any original opinion or changing the finite objective.');await settle();
 queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-revision-response','coverage')));
 await runMember('coverage-reviewer','Review the exact revised input while leaving the prior independent objection unresolved.');
 const revisionStatus=cli('status','--task',task);assert.ok(revisionStatus.data.findings.some(f=>f.code==='REVIEW_DISSENT_UNRESOLVED'&&f.basis?.includes(originalDissent.id)),'Changing target digests must not erase an unresolved genuine original opinion');checks.push({name:'Changing the target digest cannot erase an unresolved original Team finding',original_review:{id:originalDissent.id,digest:originalDissent.digest},findings:revisionStatus.data.findings});
 await sendPeer('oracle-reviewer','coverage-reviewer',JSON.stringify({review_ref:{id:originalDissent.id,digest:originalDissent.digest},finding_id:'revision-zero-boundary',question:'Cross-check this original objection against the revised inventory, actual zero assertion and SRC1/O1. Preserve the original position and record your own evidenced disposition.'}));
 queue('coverage-reviewer',step('inspect_contract',{}),publish(()=>review('review-revision-response','coverage',{cross:[{review_ref:{id:originalDissent.id,digest:originalDissent.digest},finding_ids:['revision-zero-boundary'],disposition:'rejected',reasoning:'The actual implementation and current inventory explicitly assert zero equals zero, as required by SRC1 and O1. A digest revision does not itself resolve the opinion; this exact peer counterevidence does.',evidence_refs:['SRC1','O1','inventory'],message_id:String(received('coverage-reviewer',originalDissent.id))}]}),true));
 await runMember('coverage-reviewer','Publish your own exact original-finding disposition with counterevidence; do not require the original author to agree.');
 const disposedStatus=cli('status','--task',task);assert.ok(!disposedStatus.data.findings.some(f=>f.code==='REVIEW_DISSENT_UNRESOLVED'&&f.basis?.includes(originalDissent.id)),'A genuine peer precise disposition closes the historical finding without replacing current review authorization');assert.equal(records.get(originalDissent.id).content.findings[0].status,'open','Original disagreement remains immutable and is not rewritten as consensus');checks.push({name:'A real independent peer may close exact historical dissent with counterevidence without requiring consensus',original_review:originalDissent,disposition:records.get('review-revision-response'),findings:disposedStatus.data.findings});
 // The Lead authors a file claiming the reviewer's label, then a real teammate
 // imports it. Import/publication is not authorship; Team tasks remain completed.
 const imported=review('review-oracle','oracle',{resolved:true});
 await writeFile(join(task,'.bugate/drafts/lead-written-review.json'),JSON.stringify(imported));
 const old=records.get('review-oracle');
 queue('oracle-reviewer',step('bugate_record_artifact',{input:'.bugate/drafts/lead-written-review.json',expected_digest:old.digest}));
 await runMember('oracle-reviewer','Import the prepared material as imported material. Do not claim host authorship for this file.');
 checkpoint('Lead-written path import cannot impersonate a completed Team review despite matching reviewer label and completed Team task',['REVIEW_PROVENANCE_MISSING']);
 const importObservation=observations.filter(o=>o.tool==='bugate_record_artifact').at(-1);assert.equal(importObservation.result.value.invocation,'completed',JSON.stringify(importObservation));
 // Different reviewer string on the same actual author cannot satisfy independence.
 queue('lead',publish(()=>review('review-renamed-self','oracle',{resolved:true})));await send(lead,'Publish a same-agent review with a different label as a negative control.');await settle();
 checkpoint('Renamed self-review is not an independent actual Team execution',['REVIEW_INDEPENDENCE_MISSING']);
 // Real official fork records actual inherited context; it cannot claim fresh.
 queue('fork-reviewer',step('inspect_contract',{}),publish(()=>review('review-fork','coverage')));
 queue('lead',step('spawn_teammate',{name:'fork-reviewer',description:'Context policy negative control',prompt:'Read the current contract and publish a coverage opinion. Your actual fork mode must remain visible.',context:'fork'}));
 await send(lead,'Create the forked Team context negative control using official spawn_teammate.');await settle();
 checkpoint('Actual fork is rejected by fresh-required policy',['REVIEW_CONTEXT_INVALID']);
 // Cancel after durable publication but before the final frozen tool outcome.
 // The official post-execute seam explicitly allows the registry to replace
 // success with cancellation afterward; a published receipt must not authorize.
 let cancellation,unsettledWindow;const stop=h.ctx.on('tools/post-execute',async(exec,result,next)=>{
  const decision=await next();
  if(exec.name==='bugate_publish_artifact'&&exec.arguments?.artifact?.id==='review-cancel-after-publish'){
   const status=cli('status','--task',task);assert.equal(status.invocation,'completed',JSON.stringify(status));assert.ok(status.data.findings.some(f=>f.code==='REVIEW_PROVENANCE_MISSING'&&f.subject==='review-cancel-after-publish'),'A durable publication without its final frozen result must not be treated as a completed review');
   const pending=await Promise.all((await readdir(join(task,'.bugate/host-observations'))).map(async file=>JSON.parse(await readFile(join(task,'.bugate/host-observations',file),'utf8'))));const observation=pending.find(r=>r.subject_ref.id==='review-cancel-after-publish');assert.ok(observation);await assert.rejects(readFile(join(task,'.bugate/host-settlements','settlement-'+observation.id+'.json')),error=>error.code==='ENOENT');unsettledWindow={observation_ref:{id:observation.id,digest:observation.digest},status};
   cancellation=h.ctx.agentTeams.interrupt(lead,'cancel-reviewer');
  }
  return decision;
 });
 queue('cancel-reviewer',step('inspect_contract',{}),publish(()=>review('review-cancel-after-publish','coverage'))).allowInterrupted=true;
 queue('lead',step('spawn_teammate',{name:'cancel-reviewer',description:'Final-outcome cancellation negative control',prompt:'Publish one current coverage opinion. The fixture interrupts the actual Team turn after durable output publication.',context:'fresh'}));
 await send(lead,'Create the real Team cancellation control.');await settle();stop();assert.ok(cancellation,'Actual Team interrupt must run at the observed post-execute barrier');
 await runMember('cancel-reviewer','Resume through the official Team inbox and recompute the final publication outcome; do not emit a replacement review.');
 const receipts=await Promise.all((await readdir(join(task,'.bugate/host-observations'))).map(async name=>JSON.parse(await readFile(join(task,'.bugate/host-observations',name),'utf8'))));
 const cancelled=receipts.find(r=>r.subject_ref.id==='review-cancel-after-publish');assert.ok(cancelled,'Output was genuinely published before interruption');assert.equal(cancelled.status,'published');
 const settlements=await Promise.all((await readdir(join(task,'.bugate/host-settlements'))).map(async name=>JSON.parse(await readFile(join(task,'.bugate/host-settlements',name),'utf8'))));
 const final=settlements.find(s=>s.observation_ref.id===cancelled.id);assert.ok(final,'Cold-resumed member settled its durable final DSH result');assert.notEqual(final.status,'completed');assert.equal(final.result_event.data.message.isError,true);
 checks.push({name:'Cancellation after publication cannot complete review; official Team resume settles final error',unsettled_window:unsettledWindow,interrupt:cancellation,observation:cancelled,settlement:final});
 // A changed upstream artifact invalidates all old reviews, even if task state
 // or a model still reports completion.
 const revised=structuredClone(records.get('brief'));delete revised.digest;revised.revision='2';revised.content.boundaries.push('Zero is asserted as its own explicitly required branch.');
 await writeFile(join(task,'.bugate/drafts/imported-brief.json'),JSON.stringify(revised));queue('lead',step('bugate_record_artifact',{input:'.bugate/drafts/imported-brief.json',expected_digest:records.get('brief').digest}));await send(lead,'Import changed upstream material with unknown authorship for the stale-review and missing-author negative controls.');await settle();const importedBrief=observations.filter(o=>o.tool==='bugate_record_artifact'&&o.result.value?.data?.id==='brief').at(-1);assert.equal(importedBrief.result.value.invocation,'completed');records.set('brief',importedBrief.result.value.data);
 checkpoint('Changed artifact and dependency snapshot invalidate prior completed reviews',['REVIEW_BINDING_STALE','SEMANTIC_REVIEW_STALE']);
 queue('oracle-reviewer',step('inspect_contract',{}),publish(()=>review('review-missing-author','oracle',{resolved:true})));await runMember('oracle-reviewer','Review the exact newly imported artifact without inventing an author identity; report that publication/import is not authorship.');checkpoint('A genuine independent member cannot prove independence when the reviewed artifact author is unknown',['REVIEW_AUTHOR_MISSING']);const afterFurtherRevision=cli('status','--task',task);assert.ok(!afterFurtherRevision.data.findings.some(f=>f.code==='REVIEW_DISSENT_UNRESOLVED'&&f.basis?.includes(originalDissent.id)),'An already evidenced historical disposition must not reopen just because another target changed');
 // Ordinary CLI cannot turn arbitrary model JSON into a host-published receipt.
 const denied=cli('artifact','publish','--task',task);assert.equal(denied.invocation,'invalid_input');assert.ok(denied.errors.some(e=>['HOST_PROVENANCE_REQUIRED','REVIEW_PROVENANCE_MISSING'].includes(e.code)),JSON.stringify(denied));checks.push({name:'Ordinary CLI cannot mint host provenance',result:denied});
 // Cross-task replay has a different workspace identity and cannot be admitted.
 const replayProject=join(project,'replay-project'),replayTask=await initializeProject(replayProject,{teamPolicy:policy});
 for(const directory of ['artifacts','.bugate/host-observations','.bugate/host-settlements','.bugate/host-authorities']){await mkdir(join(replayTask,directory),{recursive:true});await cp(join(task,directory),join(replayTask,directory),{recursive:true});}
 const replay=cli('status','--task',replayTask);assert.equal(replay.invocation,'completed',JSON.stringify(replay));assert.ok(replay.data.findings.some(f=>f.code==='REVIEW_PROVENANCE_MISSING'),JSON.stringify(replay));assert.equal(replay.data.implementation_ready,false);checks.push({name:'Copied real host receipts from another task cannot authorize a new workspace',workspace_id:replay.data.workspace_id,findings:replay.data.findings});
 return checks;
}
