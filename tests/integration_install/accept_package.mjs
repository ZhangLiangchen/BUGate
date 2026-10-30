#!/usr/bin/env node
// Outer launcher stages only artifact and acceptance code. The full child tree
// runs under one OS policy; a passed probe never grants an unsandboxed run.
import { readFile, writeFile, mkdir, cp, realpath, chmod } from 'node:fs/promises';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const options={};for(let i=2;i<process.argv.length;i+=2){if(!process.argv[i].startsWith('--')||!process.argv[i+1])throw Error('Expected --key value');options[process.argv[i].slice(2)]=process.argv[i+1];}
for(const key of ['package','dsh','dsh-modules','out-dir','deny-source-root','pnpm-module'])if(!options[key])throw Error(`--${key} required`);
if(process.platform!=='darwin')throw Error('This acceptance launcher requires macOS OS sandbox. No PATH-only replacement is accepted.');
await mkdir(resolve(options['out-dir']),{recursive:true});const out=await realpath(resolve(options['out-dir']));const source=await realpath(resolve(options['deny-source-root'])),node=await realpath(process.execPath),dsh=await realpath(resolve(options.dsh));const bin=join(out,'bin');await mkdir(bin,{recursive:true});
for(const name of ['archive.mjs','lifecycle.mjs','install.mjs','package.json'])await cp(join(dirname(resolve(options.package)),name),join(out,name));
const archive=join(out,'bundle.tgz');await cp(resolve(options.package),archive);await cp(new URL('installed-acceptance.mjs',import.meta.url),join(out,'installed-acceptance.mjs'));
const pnpm=join(bin,'pnpm');await writeFile(pnpm,`#!${node}\nawait import(${JSON.stringify('file://'+await realpath(resolve(options['pnpm-module'])))});\n`);await chmod(pnpm,0o755);
// Explicit executable allowlist also rejects copied/renamed Python interpreters.
const executablePaths=[node,dsh,pnpm,'/usr/bin/env','/bin/sh','/bin/bash'];const sandbox=`(version 1)\n(allow default)\n(deny file-read* (subpath ${JSON.stringify(source)}))\n(deny process-exec)\n(allow process-exec ${executablePaths.map(p=>`(literal ${JSON.stringify(p)})`).join(' ')})\n`;
await writeFile(join(out,'isolation.sb'),sandbox);const config={...options,package:archive,source,out,node,dsh,modules:await realpath(resolve(options['dsh-modules'])),sandbox,executables:executablePaths,platform:process.platform,arch:process.arch};
if(options['runtime-runner']){const runner=await realpath(resolve(options['runtime-runner']));const destination=join(out,'runtime');await mkdir(destination,{recursive:true});for(const name of options['runtime-files']?.split(',')??[runner.split('/').at(-1)])await cp(join(dirname(runner),name),join(destination,name));config.runtimeRunner=join(destination,runner.split('/').at(-1));}
await writeFile(join(out,'acceptance-config.json'),JSON.stringify(config,null,2)+'\n');
const result=spawnSync('/usr/bin/sandbox-exec',['-f',join(out,'isolation.sb'),node,join(out,'installed-acceptance.mjs'),join(out,'acceptance-config.json')],{cwd:out,env:{...process.env,PATH:bin+':'+dirname(node)+':/usr/bin:/bin',BUGATE_DSH_MODULES:config.modules},encoding:'utf8',timeout:7200000,maxBuffer:32*1024*1024});await writeFile(join(out,'acceptance.stdout'),result.stdout??'');await writeFile(join(out,'acceptance.stderr'),result.stderr??'');process.stdout.write(result.stdout??'');process.stderr.write(result.stderr??'');if(result.error)throw result.error;process.exitCode=result.status??1;
