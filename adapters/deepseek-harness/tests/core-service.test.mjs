import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Context,FsLocal,SubprocessLocal} from './host.mjs';
import {NodeCoreService} from '../plugin/lib/core-service.js';
async function rig(t,code,settings={}){
 const root=await mkdtemp(join(tmpdir(),'bugate-node-service-')),entry=join(root,'core.mjs');
 await writeFile(entry,code);await writeFile(join(root,'task.json'),JSON.stringify({workspace_id:'sha256:'+'a'.repeat(64)}));
 const ctx=new Context();await ctx.plugin(FsLocal);await ctx.plugin(SubprocessLocal);
 const service=new NodeCoreService(ctx,{nodeExecutable:process.execPath,coreEntrypoint:entry,timeoutMs:10000,maxOutputBytes:2048,graceMs:200,...settings});
 t.after(async()=>{await service.dispose();await ctx.fiber.dispose();});return{service,ctx,root,entry};
}
const result=invocation=>JSON.stringify({apiVersion:'bugate.io/v2',kind:'CommandResult',operation:'status',request_id:'fixture',invocation,data:{},errors:[]});
test('Node service: actual provider rejects missing Node, malformed JSON and combined output overflow',async t=>{
 const a=await rig(t,"console.log('not json')");await assert.rejects(a.service.call(['status'],a.root),{code:'CORE_INVALID_RESPONSE'});
 const b=await rig(t,"console.log('x'.repeat(8192))");await assert.rejects(b.service.call(['status'],b.root),{code:'CORE_OUTPUT_LIMIT'});
 const c=await rig(t,`console.log(${JSON.stringify(result('completed'))});console.error('e'.repeat(2040))`);await assert.rejects(c.service.call(['status'],c.root),{code:'CORE_OUTPUT_LIMIT'});
 const d=await rig(t,"console.log('unused')",{nodeExecutable:'/nonexistent/bugate-node'});await assert.rejects(d.service.call(['status'],d.root),{code:'DEPENDENCY_MISSING'});
});
test('Node service: envelope operation, exit semantics and private host stdin are checked',async t=>{
 const a=await rig(t,`console.log(${JSON.stringify(result('completed').replace('status','assess'))})`);await assert.rejects(a.service.call(['status'],a.root),{code:'CORE_INVALID_RESPONSE'});
 const b=await rig(t,`console.log(${JSON.stringify(result('completed'))});process.exitCode=3`);assert.equal((await b.service.call(['status'],b.root)).invocation,'completed');
 const c=await rig(t,`console.log(${JSON.stringify(result('completed'))});process.exitCode=2`);await assert.rejects(c.service.call(['status'],c.root),{code:'CORE_INVALID_RESPONSE'});
 const d=await rig(t,`let input='';for await(const chunk of process.stdin)input+=chunk;const body=JSON.parse(input);if(!body.context.authentication?.signature||body.context.host!=='deepseek-harness'||process.env.BUGATE_HOST_TOKEN)throw Error('signed private envelope');console.log(${JSON.stringify(result('completed'))})`);assert.equal((await d.service.call(['status'],d.root,undefined,{host:'deepseek-harness'})).invocation,'completed');
});
test('Node service: timeout/cancellation/dispose drain actual managed processes',async t=>{
 const script="import{writeFileSync}from'node:fs';writeFileSync('pid',String(process.pid));setInterval(()=>{},1000)";
 const a=await rig(t,script,{timeoutMs:800});await assert.rejects(a.service.call(['status'],a.root),{code:'CORE_TIMEOUT'});const timedPid=Number(await readFile(join(a.root,'pid'),'utf8'));assert.throws(()=>process.kill(timedPid,0),{code:'ESRCH'});
 const b=await rig(t,script);const abort=new AbortController(),pending=b.service.call(['status'],b.root,abort.signal);let pid;
 for(let i=0;i<100;i++){try{pid=Number(await readFile(join(b.root,'pid'),'utf8'));break;}catch{await new Promise(r=>setTimeout(r,10));}}
 assert.ok(pid);abort.abort();await assert.rejects(pending,{code:'CORE_CANCELLED'});assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
});
test('Node service: opaque provider keys are not paths and unmapped installation is rejected',async t=>{
 const a=await rig(t,`console.log(${JSON.stringify(result('completed'))})`);
 class Opaque extends FsLocal {targets=new Map();async resolve(path,opts){const original=await super.resolve(path,opts),target={...original,targetKey:'opaque-'+this.targets.size};this.targets.set(target.targetKey,original);return target;}processPath(target){return super.processPath(this.targets.get(target.targetKey));}}
 const ctx=new Context();await ctx.plugin(Opaque);await ctx.plugin(SubprocessLocal);const service=new NodeCoreService(ctx,{nodeExecutable:process.execPath,coreEntrypoint:a.entry,timeoutMs:10000,maxOutputBytes:2048,graceMs:200});t.after(async()=>{await service.dispose();await ctx.fiber.dispose();});
 const target=await ctx.fs.resolve(a.root);assert.notEqual(target.targetKey,ctx.fs.processPath(target));assert.equal((await service.call(['status'],a.root)).invocation,'completed');
 class Unmapped extends FsLocal{processPathFromHostPath(){return undefined;}}const other=new Context();await other.plugin(Unmapped);await other.plugin(SubprocessLocal);const unsupported=new NodeCoreService(other,{nodeExecutable:process.execPath,coreEntrypoint:a.entry,timeoutMs:1000,maxOutputBytes:2048,graceMs:200});t.after(async()=>{await unsupported.dispose();await other.fiber.dispose();});await assert.rejects(unsupported.call(['status'],a.root),{code:'RUNTIME_UNSUPPORTED'});
});
