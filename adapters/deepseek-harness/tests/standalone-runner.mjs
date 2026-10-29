import assert from 'node:assert/strict';
import {join,dirname} from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import {readFile,writeFile} from 'node:fs/promises';
import {host,SessionId,requestText,load} from './host.mjs';
import {initializeProject,authorAndExecute} from './workflow-fixture.mjs';
assert.equal(process.env.PYTHONPATH,undefined);
let sourceIsolation='environment-clean; installed module paths verified';
if(process.env.BUGATE_DENY_SOURCE_ROOT){
 await assert.rejects(readFile(join(process.env.BUGATE_DENY_SOURCE_ROOT,'pyproject.toml')),error=>error.code==='EPERM'||error.code==='EACCES');
 sourceIsolation='OS sandbox denied checkout reads, verified before runtime loading';
}
const python=process.env.BUGATE_INSTALLED_PYTHON;
const installed=process.env.BUGATE_INSTALLED_PLUGIN;
const pythonIdentity=JSON.parse(execFileSync(python,['-c',"import bugate,sys,json;print(json.dumps({'module':bugate.__file__,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'version':bugate.__version__}))"],{encoding:'utf8'}));
assert.notEqual(pythonIdentity.prefix,pythonIdentity.base_prefix,'installed core must use an isolated venv');
assert.ok(pythonIdentity.module.startsWith(pythonIdentity.prefix+'/'),'core must load from installed venv, not an editable checkout');
const officialRequire=createRequire(join(process.env.BUGATE_DSH_MODULES,'__runtime__.js'));
const installAnchor=join(process.env.BUGATE_DSH_MODULES,'@deepseek-ai/dsh/package.json');
const profileDir=dirname(dirname(dirname(installed)));
const {loadProfileDirectory,createRuntimeResolution,PluginPackages}=await load('@deepseek-ai/dsh-app-boot');
const profile=loadProfileDirectory('dsh',profileDir,installAnchor);
const resolution=await createRuntimeResolution({installAnchor,profile,home:dirname(dirname(profileDir))});
const compatibility=JSON.parse(await readFile(join(installed,'resources/compatibility.json')));
const results=[];let identity;
for(const productFailure of [false,true]){
 const project=join(process.cwd(),productFailure?'generated-failure-project':'generated-project');
 const task=await initializeProject(project,python);
 const h=await host({persistence:join(process.cwd(),productFailure?'failure-sessions':'sessions')});
 try{
  await h.ctx.plugin(PluginPackages,{resolution});
  const pluginRequire=createRequire(join(installed,'package.json'));
  identity={host:officialRequire.resolve('@deepseek-ai/dsh-llm'),plugin:pluginRequire.resolve('@deepseek-ai/dsh-llm')};
  assert.equal(identity.host,identity.plugin,'real profile runtime resolution must share DSH module identity');
  const plugin=await import(pathToFileURL(join(installed,'lib/index.js')).href);
  await h.ctx.plugin(plugin,{pythonExecutable:python,projectRoot:project,executionCommands:{
   ...Object.fromEntries([1,2,3].map(i=>['diagnose'+i,{argv:[python,'-c',`from pathlib import Path;print(Path('logs/installed.xml').read_text());Path('logs/diagnose${i}.xml').write_text('<testsuite/>');Path('logs/diagnose${i}-executed').write_text('observed')`],caseIds:[],report:`logs/diagnose${i}.xml`,purpose:'diagnostic',activityId:'diagnose',environmentId:'local-fixture'}])),
   normal:{argv:[python,'tests/runner.py','logs/installed.xml',productFailure?'broken':'normal'],caseIds:['C1','C2'],report:'logs/installed.xml',purpose:'test',environmentId:'local-fixture'},
   outside:{argv:[python,'-c',"from pathlib import Path;Path('outside-executed').write_text('must never run')"],caseIds:['C-security'],report:'logs/outside.xml',purpose:'test',environmentId:'local-fixture'},
  }});
  const agent=await h.ctx.agentLoop.create(SessionId(productFailure?'installed-failure':'installed-runtime'),{provider:'fixture',model:'deterministic'},{cwd:task});
  const result=await authorAndExecute(h,agent,task,{execute:true,exerciseScope:true,productFailure});
  assert.ok(h.model.requests.every(r=>requestText(r).includes('BUGate')));
  assert.equal(result.completion_assessment.verdict,'satisfied');
  assert.equal(result.requirement_assessment.verdict,productFailure?'unsatisfied':'satisfied');
  results.push({task,...result});
 }finally{await h.ctx.fiber.dispose();}
}
const result={verification:'real-dsh-runtime-deterministic-model-installed-package',formal_requirement_ids:['F-01','F-02','F-04','F-05','F-07'],verified_invariants:['scope-bound execution','non-executable suggestions','suggestions do not block completion','functional failure preserved','bounded diagnosis','installed runtime independent of source'],source_isolation:sourceIsolation,module_identity:identity,python_identity:pythonIdentity,profile_directory:profileDir,compatibility,installed,python,...results[0],failure_result:results[1]};
await writeFile(join(process.cwd(),'result.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({verification:result.verification,requests:results.map(r=>r.requests),evidence_ids:results.map(r=>r.evidence.id),execution:results.map(r=>r.evidence.execution.status),completion:results.map(r=>r.completion_assessment.verdict),compatibility}));
