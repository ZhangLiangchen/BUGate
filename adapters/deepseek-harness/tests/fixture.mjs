import {mkdtemp,writeFile,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
export const repository=fileURLToPath(new URL('../../../',import.meta.url));
export async function fixture({artifacts=true,design=true}={}){
 const temporary=await realpath(await mkdtemp(join(tmpdir(),'bugate-dsh-task-')));
 const python=process.env.BUGATE_TEST_PYTHON ?? 'python3';
 const wrapper=join(temporary,'python-core');
 // Test-only source loader. Packaged-install tests use an independently installed venv.
 const quote=s=>"'"+s.replaceAll("'","'\\''")+"'";
 await writeFile(wrapper,`#!/bin/sh\nPYTHONPATH=${quote(join(repository,'src')+':'+join(repository,'tests/v2'))} exec ${quote(python)} "$@"\n`,{mode:0o755});
 const project=join(temporary,'project');
 const code=`from pathlib import Path\nfrom helpers import fixture,claim\nfrom bugate.assessment import assess\nimport json,sys\nw=fixture(Path(sys.argv[1]),with_artifacts=${artifacts?'True':'False'})\n${design&&artifacts?"claim(w,'test_design_complete','design');assess(w.root,'claims/design.json')\n":''}print(json.dumps({'task':str(w.root),'project':str(w.project)}))`;
 const result=JSON.parse(execFileSync(wrapper,['-c',code,project],{encoding:'utf8'}));
 function py(code,...args){return execFileSync(wrapper,['-c',code,...args],{encoding:'utf8'});}
 function cli(...args){try{return JSON.parse(execFileSync(wrapper,['-m','bugate',...args,'--json'],{encoding:'utf8'}));}catch(error){if(error.stdout)return JSON.parse(error.stdout);throw error;}}
 return {...result,wrapper,python,temporary,py,cli};
}
