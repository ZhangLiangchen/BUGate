import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {host,SessionId,send,toolResponse,textResponse} from './host.mjs';
import {initializeProject} from './workflow-node.mjs';
import {observeBugateService} from './installed-profile.mjs';
const plugin=await import(process.env.BUGATE_SERVICE_TEST_PLUGIN?pathToFileURL(process.env.BUGATE_SERVICE_TEST_PLUGIN).href:new URL('../plugin/lib/index.js',import.meta.url).href);

test('Cordis service: actual injected consumer shares native tool instance, assessment, disposal and reload',async t=>{
 const project=await mkdtemp(join(tmpdir(),'bugate-cordis-quality-')),task=await initializeProject(project),h=await host({team:true,persistence:join(project,'sessions')});t.after(()=>h.ctx.fiber.dispose());
 let owner=await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath});
 const consumer=await observeBugateService(h,task),status=consumer.status.data;
 assert.ok(h.ctx.get('bugate'));assert.ok(consumer.service instanceof plugin.BugateService);
 await mkdir(join(task,'claims'),{recursive:true});const claimPath=join(task,'claims/service-probe.json');
 await writeFile(claimPath,JSON.stringify({apiVersion:'bugate.io/v2',kind:'Claim',id:'service-probe',claim_type:'test_design_complete',task_id:status.task_id,workspace_id:status.workspace_id,statement:'The consumer asks the same evaluator for the current incomplete design; no pass is invented.',requirement_ids:['R1'],artifact_ids:[],evidence_ids:[],workspace_snapshot:status.workspace_snapshot,scope_ref:status.scope_ref,plan_ref:status.plan_ref,known_gaps:[],open_questions:[]}));
 const assessed=await consumer.service.assess(task,claimPath);assert.equal(assessed.invocation,'completed',JSON.stringify(assessed));assert.equal(assessed.operation,'assess');assert.equal(assessed.data.verdict,'unsatisfied');
 // Instrument only this owned test instance. The native plugin must invoke it,
 // not a separately constructed facade with duplicate rule behavior.
 const calls=[],registered=h.ctx.get('bugate');
 for(const name of ['status','prepare','assess']){const original=registered[name];registered[name]=(...args)=>{calls.push(name);return original(...args);};}
 const results=[];h.ctx.on('tools/result',(exec,result)=>results.push({tool:exec.name,result}));
 const agent=await h.ctx.agentLoop.create(SessionId('native-service-consumer'),{provider:'fixture',model:'deterministic'},{cwd:task});calls.length=0;
 h.model.script.push(toolResponse('bugate_status',{},'service-status'),toolResponse('bugate_prepare',{},'service-prepare'),toolResponse('bugate_assess',{claim:'claims/service-probe.json'},'service-assess'),textResponse('The incomplete result is retained; this is a service lifecycle probe.'));
 await send(agent,'Read and assess only the existing current task through the native quality tools.');
 for(const name of ['status','prepare','assess'])assert.ok(calls.includes(name),'Native tool path must consume the registered service method '+name);
 for(const result of results){assert.equal(result.result.isError,false,JSON.stringify(result));assert.equal(result.result.value.invocation,'completed',JSON.stringify(result));}
 assert.equal(results.find(r=>r.tool==='bugate_assess').result.value.data.verdict,'unsatisfied');
 const old=consumer.service;await owner.dispose();assert.equal(h.ctx.get('bugate'),undefined);assert.equal(consumer.active(),false,'Dependent plugin unloads with the actual provider service');await assert.rejects(old.status(task),e=>e.code==='CORE_CANCELLED');
 owner=await h.ctx.plugin(plugin,{projectRoot:project,nodeExecutable:process.execPath});const reloaded=await observeBugateService(h,task);assert.notEqual(reloaded.service,old);assert.equal(reloaded.status.data.workspace_id,status.workspace_id);assert.equal(reloaded.active(),true);
});
