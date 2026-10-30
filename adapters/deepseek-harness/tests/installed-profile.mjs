import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import {join,dirname} from 'node:path';
import {load} from './host.mjs';
/** Read the installed profile through the same official composition and package
 * resolver as dsh. Headless acceptance mounts the selected actual backend rows. */
export async function installedProfileMount(profileDir,modules){
 const {loadProfileDirectory,createRuntimeResolution,composeEntries,PluginPackages}=await load('@deepseek-ai/dsh-app-boot');
 const installAnchor=join(modules,'@deepseek-ai/dsh/package.json');
 const profile=loadProfileDirectory('dsh',profileDir,installAnchor);
 assert.deepEqual(profile.skippedBundles,[],'Installed profile bundle must be compatible and resolvable');
 const entries=composeEntries([...profile.layers.map(layer=>layer.patches),profile.patches]);
 const flat=[];function visit(items){for(const entry of items){flat.push(entry);if(entry.group&&Array.isArray(entry.config))visit(entry.config);}}visit(entries);
 const selected=['agent-team','tool-agent-team','bugate'].map(id=>{const rows=flat.filter(e=>e.id===id&&!e.disabled);assert.equal(rows.length,1,`Exactly one enabled ${id} row is required`);return rows[0];});
 for(const id of ['tool-subagent','tool-subagent-control','tool-subagent-fork']){const rows=flat.filter(e=>e.id===id);assert.ok(rows.length>0&&rows.every(e=>e.disabled===true),`Official Team profile disables direct ${id}`);}
 const resolution=await createRuntimeResolution({installAnchor,profile,home:dirname(dirname(profileDir))});
 const identities=[];
 return {profile,entries:selected,identities,async mount(h,{executionCommands,maxContextBytes,coreEntrypoint}={}){
  await h.ctx.plugin(PluginPackages,{resolution});
  for(const entry of selected){const req=createRequire(join(profileDir,'package.json'));const resolved=req.resolve(entry.name);const imported=await import(pathToFileURL(resolved).href);const config={...entry.config,...(entry.id==='bugate'&&executionCommands?{executionCommands}:{}),...(entry.id==='bugate'&&maxContextBytes?{maxContextBytes}:{}),...(entry.id==='bugate'&&coreEntrypoint?{coreEntrypoint}:{})};identities.push({id:entry.id,name:entry.name,module:resolved,config});await h.ctx.plugin(imported.default??imported,config);}
  assert.ok(h.ctx.agentTeams,'Composed profile actually activated official Team service');
 }};
}

/** A real dependent Cordis plugin consumes the installed named service. */
export async function observeBugateService(h,task){
 let result,active=false;
 const fiber=await h.ctx.plugin({name:'bugate-service-acceptance-consumer',inject:['bugate'],async apply(ctx){
  const service=ctx.bugate;
  assert.equal(service.name,'bugate');
  for(const name of ['status','prepare','assess'])assert.equal(typeof service[name],'function');
  for(const name of ['call','publish','sign','hostContext'])assert.equal(service[name],undefined,'Native signing and arbitrary execution must not be exposed on the quality service');
  const status=await service.status(task),prepared=await service.prepare(task);
  assert.equal(status.invocation,'completed',JSON.stringify(status));assert.equal(status.operation,'status');
  assert.equal(prepared.invocation,'completed',JSON.stringify(prepared));assert.equal(prepared.operation,'protocol.prepare');assert.equal(prepared.data.workspace.workspace_digest,status.data.workspace_id);
  result={service,status,prepared};active=true;ctx.effect(()=>()=>{active=false;});
 }});
 assert.ok(result,'The real injected consumer must activate');return{fiber,...result,active:()=>active};
}
