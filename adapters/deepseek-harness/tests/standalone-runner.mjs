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
const project=join(process.cwd(),'generated-project');
const task=await initializeProject(project,python);
const h=await host({persistence:join(process.cwd(),'sessions')});
try{
 const officialRequire=createRequire(join(process.env.BUGATE_DSH_MODULES,'__runtime__.js'));
 const installAnchor=join(process.env.BUGATE_DSH_MODULES,'@deepseek-ai/dsh/package.json');
 const profileDir=dirname(dirname(dirname(installed)));
 const {loadProfileDirectory,createRuntimeResolution,PluginPackages}=await load('@deepseek-ai/dsh-app-boot');
 const profile=loadProfileDirectory('dsh',profileDir,installAnchor);
 const resolution=await createRuntimeResolution({installAnchor,profile,home:dirname(dirname(profileDir))});
 await h.ctx.plugin(PluginPackages,{resolution});
 const pluginRequire=createRequire(join(installed,'package.json'));
 const identity={host:officialRequire.resolve('@deepseek-ai/dsh-llm'),plugin:pluginRequire.resolve('@deepseek-ai/dsh-llm')};
 assert.equal(identity.host,identity.plugin,'real profile runtime resolution must share DSH module identity');
 const plugin=await import(pathToFileURL(join(installed,'lib/index.js')).href);
 await h.ctx.plugin(plugin,{pythonExecutable:python,projectRoot:project,executionCommands:{normal:{argv:[python,'tests/runner.py','logs/installed.xml'],caseIds:['C1','C2'],report:'logs/installed.xml',purpose:'test'}}});
 const agent=await h.ctx.agentLoop.create(SessionId('installed-runtime'),{provider:'fixture',model:'deterministic'},{cwd:task});
 const result=await authorAndExecute(h,agent,task,{execute:true});
 assert.ok(h.model.requests.every(r=>requestText(r).includes('BUGate')));
 const compatibility=JSON.parse(await readFile(join(installed,'resources/compatibility.json')));
 await writeFile(join(process.cwd(),'result.json'),JSON.stringify({verification:'real-dsh-runtime-deterministic-model-installed-package',source_isolation:sourceIsolation,module_identity:identity,python_identity:pythonIdentity,profile_directory:profileDir,compatibility,installed,python,task,...result},null,2));
 console.log(JSON.stringify({verification:'real-dsh-runtime-deterministic-model-installed-package',requests:result.requests,evidence_id:result.evidence.id,execution:result.evidence.execution.status,task,compatibility}));
}finally{await h.ctx.fiber.dispose();}
