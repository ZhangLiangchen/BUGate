import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Context,FsLocal,SubprocessLocal} from './host.mjs';
import {PythonBridge} from '../plugin/lib/bridge.js';
async function rig(t,code,settings={}){
 const root=await mkdtemp(join(tmpdir(),'bugate-bridge-'));
 const executable=join(root,'core.py');
 await writeFile(executable,'#!/usr/bin/env python3\n'+code,{mode:0o755});
 const ctx=new Context();await ctx.plugin(FsLocal);await ctx.plugin(SubprocessLocal);
 const bridge=new PythonBridge(ctx,{pythonExecutable:executable,timeoutMs:10000,maxOutputBytes:2048,graceMs:200,...settings});
 t.after(async()=>{await bridge.dispose();await ctx.fiber.dispose();});
 return {bridge,ctx,root,executable};
}
const result=invocation=>JSON.stringify({apiVersion:'bugate.io/v2',kind:'CommandResult',operation:'status',request_id:'fixture',invocation,data:{},errors:[]});
test('AC-15 actual provider rejects missing Python, malformed JSON and oversized output',async t=>{
 const a=await rig(t,'print("not json")');await assert.rejects(a.bridge.call(['status'],a.root),{code:'PYTHON_INVALID_RESPONSE'});
 const b=await rig(t,'print("x"*8192)');await assert.rejects(b.bridge.call(['status'],b.root),{code:'PYTHON_OUTPUT_LIMIT'});
 const combined=await rig(t,`import sys\nprint(${JSON.stringify(JSON.stringify({apiVersion:'bugate.io/v2',kind:'CommandResult',operation:'status',request_id:'combined',invocation:'completed',data:{payload:'x'.repeat(1200)},errors:[]}))})\nprint('e'*1200,file=sys.stderr)`);await assert.rejects(combined.bridge.call(['status'],combined.root),{code:'PYTHON_OUTPUT_LIMIT'});
 const c=await rig(t,'print("unused")',{pythonExecutable:'/nonexistent/bugate-python'});await assert.rejects(c.bridge.call(['status'],c.root),{code:'DEPENDENCY_MISSING'});
});
test('AC-15 envelope operation and exit semantics cannot be forged',async t=>{
 const a=await rig(t,`print(${JSON.stringify(result('completed').replace('status','assess'))})`);await assert.rejects(a.bridge.call(['status'],a.root),{code:'PYTHON_INVALID_RESPONSE'});
 const b=await rig(t,`import sys\nprint(${JSON.stringify(result('completed'))})\nsys.exit(3)`);assert.equal((await b.bridge.call(['status'],b.root)).invocation,'completed');
 const c=await rig(t,`import sys\nprint(${JSON.stringify(result('completed'))})\nsys.exit(2)`);await assert.rejects(c.bridge.call(['status'],c.root),{code:'PYTHON_INVALID_RESPONSE'});
});
test('AC-16 timeout and cancellation terminate real managed processes',async t=>{
 const a=await rig(t,'import os,time\nopen("pid", "w").write(str(os.getpid()))\ntime.sleep(60)',{timeoutMs:3000});
 await assert.rejects(a.bridge.call(['status'],a.root),{code:'PYTHON_TIMEOUT'});
 const pid=Number(await readFile(join(a.root,'pid'),'utf8'));assert.throws(()=>process.kill(pid,0),{code:'ESRCH'});
 const b=await rig(t,'import os,time\nopen("pid", "w").write(str(os.getpid()))\ntime.sleep(60)');
 const abort=new AbortController();const pending=b.bridge.call(['status'],b.root,abort.signal);for(let i=0;i<100;i++){try{await readFile(join(b.root,'pid'));break;}catch{await new Promise(r=>setTimeout(r,50));}}abort.abort();
 await assert.rejects(pending,{code:'PYTHON_CANCELLED'});
 const pid2=Number(await readFile(join(b.root,'pid'),'utf8'));assert.throws(()=>process.kill(pid2,0),{code:'ESRCH'});
});
test('AC-30 provider has opaque targets and rejects unmapped installation',async t=>{
 const a=await rig(t,`print(${JSON.stringify(result('completed'))})`);
 class Opaque extends FsLocal {
  targets=new Map();
  async resolve(path,opts){const original=await super.resolve(path,opts);const target={...original,targetKey:'opaque-target-'+this.targets.size};this.targets.set(target.targetKey,original);return target;}
  processPath(target){return super.processPath(this.targets.get(target.targetKey));}
 }
 const opaqueCtx=new Context();await opaqueCtx.plugin(Opaque);await opaqueCtx.plugin(SubprocessLocal);
 const opaqueBridge=new PythonBridge(opaqueCtx,{pythonExecutable:a.executable,timeoutMs:10000,maxOutputBytes:2048,graceMs:200});
 t.after(async()=>{await opaqueBridge.dispose();await opaqueCtx.fiber.dispose();});
 const target=await opaqueCtx.fs.resolve(a.root);assert.notEqual(String(target.targetKey),opaqueCtx.fs.processPath(target));
 assert.equal((await opaqueBridge.call(['status'],a.root)).invocation,'completed');
 class Unmapped extends FsLocal {processPathFromHostPath(){return undefined;}}
 const ctx=new Context();await ctx.plugin(Unmapped);await ctx.plugin(SubprocessLocal);
 const bridge=new PythonBridge(ctx,{pythonExecutable:a.executable,timeoutMs:1000,maxOutputBytes:2048,graceMs:200});
 t.after(async()=>{await bridge.dispose();await ctx.fiber.dispose();});
 await assert.rejects(bridge.call(['status'],a.root),{code:'RUNTIME_UNSUPPORTED'});
});
