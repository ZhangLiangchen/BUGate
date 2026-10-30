#!/usr/bin/env node
import { readFile, writeFile, mkdir, rm, cp, readdir, realpath, lstat, mkdtemp } from 'node:fs/promises';
import { resolve, join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { listFiles, sha256, packBytes, verifyFiles, safeName } from './archive.mjs';
const ROOT=fileURLToPath(new URL('../',import.meta.url)),PLUGIN=join(ROOT,'adapters/deepseek-harness/plugin');
const json=async p=>JSON.parse(await readFile(p,'utf8'));
const stable=object=>Object.fromEntries(Object.entries(object).sort(([a],[b])=>a<b?-1:1));
async function selected(root,names){const files={};for(const name of names){const p=join(root,name);const s=await lstat(p);if(s.isDirectory()){for(const [child,b]of Object.entries(await listFiles(p)))files[name+'/'+child]=b;}else if(s.isFile())files[name]=await readFile(p);else throw Error(`Unexpected source link: ${p}`);}return files;}
export async function sourceInventory(){const result={};for(const name of ['packages/core/src','packages/cli/src','adapters/deepseek-harness/plugin/src','protocol/v2']){for(const [child,b]of Object.entries(await listFiles(join(ROOT,name))))result[name+'/'+child]=sha256(b);}for(const name of ['package.json','package-lock.json','tsconfig.json','packages/core/package.json','packages/core/tsconfig.json','packages/cli/package.json','packages/cli/tsconfig.json','packaging/package.json','packaging/build.mjs','packaging/archive.mjs','packaging/lifecycle.mjs','packaging/install.mjs','packaging/resources.json','adapters/deepseek-harness/plugin/package.json','adapters/deepseek-harness/plugin/build.mjs','adapters/deepseek-harness/plugin/tsconfig.json','adapters/deepseek-harness/plugin/cordis.patch.yml','adapters/deepseek-harness/plugin/README.md','adapters/deepseek-harness/plugin/README.zh-CN.md','views/skills/bugate/SKILL.md','LICENSE'])result[name]=sha256(await readFile(join(ROOT,name)));return stable(result);}
export async function compile(){
 const actual=Object.keys(await listFiles(join(ROOT,'protocol/v2'))).map(n=>'protocol/v2/'+n).sort();const declared=(await json(join(ROOT,'packaging/resources.json'))).protocol_files.slice().sort();if(JSON.stringify(actual)!==JSON.stringify(declared))throw Error('Protocol finite resource inventory differs from source');
 const resources=join(ROOT,'packages/core/resources/protocol/v2');await rm(resources,{recursive:true,force:true});await mkdir(dirname(resources),{recursive:true});await cp(join(ROOT,'protocol/v2'),resources,{recursive:true});
 for(const location of ['packages/core/dist','packages/cli/dist','adapters/deepseek-harness/plugin/lib'])await rm(join(ROOT,location),{recursive:true,force:true});
 for(const location of ['packages/core','packages/cli'])execFileSync(process.execPath,[join(ROOT,'node_modules/typescript/bin/tsc'),'-p',join(ROOT,location,'tsconfig.json')],{cwd:ROOT,stdio:'inherit'});
 execFileSync(process.execPath,[join(PLUGIN,'build.mjs')],{cwd:PLUGIN,stdio:'inherit'});
}
export async function build(out){
 const before=await sourceInventory();await compile();if(JSON.stringify(before)!==JSON.stringify(await sourceInventory()))throw Error('Build inputs changed during compilation; no artifact published');const files={};const plugin=await json(join(PLUGIN,'package.json'));const version=(await json(join(ROOT,'package.json'))).version;
 if(plugin.version!==version)throw Error('Root and native package versions differ');
 Object.assign(files,await selected(PLUGIN,['lib','resources','cordis.patch.yml','README.md','README.zh-CN.md']));files.LICENSE=await readFile(join(ROOT,'LICENSE'));
 const workspaces={'@bugate/core':join(ROOT,'packages/core'),'@bugate/cli':join(ROOT,'packages/cli'),'@bugate/install':join(ROOT,'packaging')};const queue=[...Object.keys(workspaces),'@deepseek-ai/dsh-experimental-agent-team-profile'],seen=new Set(),dependencies={};
 while(queue.length){const name=queue.shift();if(seen.has(name))continue;seen.add(name);let dir=workspaces[name];if(!dir){const req=createRequire(join(name.startsWith('@deepseek-ai/')?PLUGIN:ROOT,'package.json'));let found;try{found=req.resolve(name+'/package.json');}catch{found=req.resolve(name);while(true){const candidate=join(dirname(found),'package.json');try{const meta=await json(candidate);if(meta.name===name){found=candidate;break;}}catch{}const parent=dirname(dirname(found));if(parent===dirname(found))throw Error(`Cannot locate ${name}`);found=join(parent,'x');}}dir=dirname(found);}dir=await realpath(dir);const meta=await json(join(dir,'package.json'));dependencies[name]=meta.version;
 const inventory=(await json(join(ROOT,'packaging/resources.json'))).node_package_files;
 if(!workspaces[name]&&!inventory[name])throw Error(`Undeclared runtime dependency: ${name}`);
 const members=workspaces[name]?await selected(dir,['package.json',...(meta.files??[])]):await selected(dir,inventory[name]);for(const [child,b]of Object.entries(members)){if(child.endsWith('.map')||child.endsWith('.tsbuildinfo'))continue;const target='node_modules/'+name+'/'+child;safeName(target);files[target]=b;}
 if(!name.startsWith('@deepseek-ai/'))for(const dependency of Object.keys(meta.dependencies??{}))queue.push(dependency);
 else if(meta.version!=='0.2.2-alpha.2')throw Error('Official Team profile version differs');
 }
 const metadata={...plugin,version,bin:{bugate:'node_modules/@bugate/cli/dist/index.js'},dependencies:stable({...plugin.dependencies,...Object.fromEntries(Object.keys(workspaces).map(n=>[n,version]))}),bundledDependencies:Object.keys(dependencies).sort(),files:['lib','resources','node_modules','cordis.patch.yml','README.md','README.zh-CN.md','LICENSE','inventory.json']};delete metadata.devDependencies;delete metadata.scripts;delete metadata.private;
 for(const name of Object.keys(workspaces))if(metadata.peerDependencies)delete metadata.peerDependencies[name];
 files['package.json']=Buffer.from(JSON.stringify(metadata,null,2)+'\n');
 const canonical=await listFiles(join(ROOT,'protocol/v2'));for(const [name,b]of Object.entries(canonical)){for(const target of ['resources/protocol/'+name,'node_modules/@bugate/core/resources/protocol/v2/'+name])if(!files[target]||!files[target].equals(b))throw Error(`Protocol projection differs: ${target}`);}
 for(const [name,b]of Object.entries(files)){if(/\.(?:js|mjs|json|yml|yaml)$/.test(name)){const text=b.toString('utf8');if(text.includes(ROOT)||text.includes('workspace:*'))throw Error(`Development reference leaked: ${name}`);}}
 const inventory={apiVersion:'bugate.node-bundle/v1',files:stable(Object.fromEntries(Object.entries(files).map(([n,b])=>[n,sha256(b)])))};files['inventory.json']=Buffer.from(JSON.stringify(inventory,null,2)+'\n');verifyFiles(files);const archive=packBytes(files);
 if(JSON.stringify(before)!==JSON.stringify(await sourceInventory()))throw Error('Build inputs changed during packaging; retry stable inputs');
 await mkdir(out,{recursive:true});const filename=`bugate-deepseek-harness-${version}.tgz`;await writeFile(join(out,filename),archive);
 for(const name of ['install.mjs','lifecycle.mjs','archive.mjs']){await cp(join(ROOT,'packaging',name),join(out,name));if(sha256(await readFile(join(out,name)))!==before['packaging/'+name])throw Error(`Standalone asset changed during publication: ${name}`);}
 await writeFile(join(out,'package.json'),JSON.stringify({private:true,type:'module'})+'\n');
 await writeFile(join(out,'source-manifest.json'),JSON.stringify({apiVersion:'bugate.build-source/v2',files:before},null,2)+'\n');
 const manifest={apiVersion:'bugate.node-build/v1',package:filename,version,node:process.version,platform:process.platform,arch:process.arch,dsh:'0.2.2-alpha.2',runtime_dependencies:stable(dependencies),files:{}};
 for(const name of [filename,'install.mjs','lifecycle.mjs','archive.mjs','package.json','source-manifest.json'])manifest.files[name]=sha256(await readFile(join(out,name)));
 await writeFile(join(out,'build-manifest.json'),JSON.stringify(manifest,null,2)+'\n');const sums=stable({...manifest.files,'build-manifest.json':sha256(await readFile(join(out,'build-manifest.json')))});await writeFile(join(out,'SHA256SUMS'),Object.entries(sums).map(([n,d])=>`${d}  ${n}\n`).join(''));if(JSON.stringify(before)!==JSON.stringify(await sourceInventory())){await rm(join(out,'SHA256SUMS'),{force:true});throw Error('Build inputs changed during output publication; checksum completion marker removed');}return manifest;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){const args=process.argv.slice(2);if(args.includes('--compile-only'))await compile();else{const index=args.indexOf('--out-dir');if(index<0||!args[index+1])throw Error('--out-dir required');process.stdout.write(JSON.stringify(await build(resolve(args[index+1])),null,2)+'\n');}}
