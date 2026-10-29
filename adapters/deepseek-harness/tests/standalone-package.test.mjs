import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,copyFile,writeFile,readFile,realpath} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawn} from 'node:child_process';

test('AC-19/20/21 installed tarball and isolated Python run real DSH authoring and execution outside the checkout',async()=>{
 for(const name of ['BUGATE_INSTALLED_PLUGIN','BUGATE_INSTALLED_PYTHON','BUGATE_DSH_MODULES'])assert.ok(process.env[name],`${name} is required; standalone acceptance must not silently skip`);
 const directory=await realpath(await mkdtemp(join(tmpdir(),'bugate-installed-dsh-')));
 for(const name of ['standalone-runner.mjs','host.mjs','workflow-fixture.mjs'])await copyFile(new URL(name,import.meta.url),join(directory,name));
 const env={...process.env};delete env.PYTHONPATH;delete env.BUGATE_TEST_PYTHON;delete env.NODE_PATH;
 const child=spawn(process.execPath,[join(directory,'standalone-runner.mjs')],{cwd:directory,env,stdio:['ignore','pipe','pipe']});let output='';child.stdout.on('data',x=>output+=x);child.stderr.on('data',x=>output+=x);
 const code=await new Promise(resolve=>child.on('exit',resolve));await writeFile(join(directory,'runtime.log'),output);assert.equal(code,0,output+`\nRetained evidence: ${directory}`);
 const result=JSON.parse(await readFile(join(directory,'result.json')));assert.equal(result.evidence.source_type,'host_observed');assert.equal(result.evidence.execution.status,'passed');
 console.log(JSON.stringify({evidence:join(directory,'result.json'),log:join(directory,'runtime.log'),requests:result.requests,installed:result.installed,python:result.python}));
});
