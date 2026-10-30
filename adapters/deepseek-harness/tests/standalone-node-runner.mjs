import assert from 'node:assert/strict';
import {join,dirname} from 'node:path';
import {createRequire} from 'node:module';
import {execFileSync,spawnSync} from 'node:child_process';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {host,SessionId,requestText} from './host.mjs';
import {initializeProject,authorAndExecute,cli} from './workflow-node.mjs';
import {installedProfileMount} from './installed-profile.mjs';
import {teamAcceptance} from './team-fixture.mjs';
import {negativeTeamChecks} from './team-negative.mjs';

const installed=process.env.BUGATE_INSTALLED_PLUGIN,modules=process.env.BUGATE_DSH_MODULES,profileDir=process.env.BUGATE_PROFILE_DIR,projectRoot=process.env.BUGATE_PROJECT;
assert.ok(installed&&modules&&profileDir&&projectRoot,'Explicit installed package, host modules, profile and project are required');
assert.ok(process.env.BUGATE_CLI_ENTRYPOINT?.startsWith(installed+'/'),'CLI entrypoint must belong to the installed product');
const isolation=[];
if(process.env.BUGATE_DENY_SOURCE_ROOT){await assert.rejects(readFile(join(process.env.BUGATE_DENY_SOURCE_ROOT,'package.json')),e=>e.code==='EPERM'||e.code==='EACCES');isolation.push('OS sandbox denied source checkout read');}
for(const executable of ['/usr/bin/python3','/opt/homebrew/bin/python3','/usr/local/bin/python3']){const attempt=spawnSync(executable,['--version'],{encoding:'utf8'});assert.ok(attempt.error&&['EPERM','EACCES','ENOENT'].includes(attempt.error.code),`Python must not execute in isolated runtime: ${executable}`);isolation.push({executable,denied:attempt.error.code});}
const installRequire=createRequire(join(installed,'package.json')),corePath=installRequire.resolve('@bugate/core');assert.ok(corePath.startsWith(installed+'/'),'Core must resolve inside standalone installation');
const version=cli('version');assert.equal(version.invocation,'completed');assert.equal(version.data.core_version,'2.2.0-dev.1');
const composition=await installedProfileMount(profileDir,modules),results=[];
for(const productFailure of [false,true]){
 const project=join(projectRoot,productFailure?'runtime-failure':'runtime-success'),task=await initializeProject(project);
 const h=await host({persistence:join(project,'sessions'),team:'infrastructure'});
 try{
  const commands={...Object.fromEntries([1,2,3].map(i=>['diagnose'+i,{argv:[process.execPath,'-e',`const f=require('node:fs');console.log(f.readFileSync('logs/installed.xml','utf8'));f.writeFileSync('logs/diagnose${i}.xml','<testsuite/>');f.writeFileSync('logs/diagnose${i}-executed','observed')`],caseIds:[],report:`logs/diagnose${i}.xml`,purpose:'diagnostic',activityId:'diagnose',environmentId:'local-fixture'}])),normal:{argv:[process.execPath,'tests/runner.mjs','logs/installed.xml',productFailure?'broken':'normal'],caseIds:['C1','C2','C3'],report:'logs/installed.xml',purpose:'test',environmentId:'local-fixture'},outside:{argv:[process.execPath,'-e',"require('node:fs').writeFileSync('outside-executed','must never run')"],caseIds:['C-security'],report:'logs/outside.xml',purpose:'test',environmentId:'local-fixture'}};
  await composition.mount(h,{executionCommands:commands});
  const agent=await h.ctx.agentLoop.create(SessionId(productFailure?'installed-failure':'installed-success'),{provider:'fixture',model:'deterministic'},{cwd:task});
  const result=await authorAndExecute(h,agent,task,{execute:true,exerciseScope:true,productFailure});
  assert.ok(h.model.requests.every(r=>requestText(r).includes('BUGate')));assert.equal(result.completion_assessment.verdict,'satisfied');assert.equal(result.requirement_assessment.verdict,productFailure?'unsatisfied':'satisfied');results.push(result);
 }finally{await h.ctx.fiber.dispose();}
}
const team=await teamAcceptance(join(projectRoot,'runtime-team'),null,{mount:composition.mount,negativeChecks:negativeTeamChecks});
const report={status:'PASS',verification:'installed Node package / official composed DSH profile / real Team runtime',live_model:'NOT_RUN',platform:process.platform,arch:process.arch,node:process.version,source_isolation:isolation,installed,core_path:corePath,version:version.data,profile_directory:profileDir,composed_backend_entries:composition.entries,module_identities:composition.identities,workflows:results,team:team.report};
await writeFile(join(process.cwd(),'result.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({status:report.status,verification:report.verification,workflow_verdicts:results.map(r=>({execution:r.evidence.execution.status,requirement:r.requirement_assessment.verdict,completion:r.completion_assessment.verdict})),team:team.report.status,negative_controls:team.report.negative_controls.length,source_isolation:isolation}));
