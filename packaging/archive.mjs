import { createHash } from 'node:crypto';
import { gzipSync, gunzipSync } from 'node:zlib';
import { lstat, readdir, readFile, mkdir, writeFile } from 'node:fs/promises';
import { join, resolve, sep } from 'node:path';
export const sha256 = data => createHash('sha256').update(data).digest('hex');
export class PackageError extends Error { constructor(code, message) { super(message); this.code=code; this.exitCode=2; } }
export function safeName(name) {
  if (typeof name !== 'string' || !name || name.startsWith('/') || name.includes('\\') || name.includes('\0') || name.split('/').some(x=>!x || x==='.' || x==='..')) throw new PackageError('PACKAGE_INVALID', `Unsafe package member: ${name}`);
  const parts=name.split('/');
  if (parts.some(x=>['.git','.env','.DS_Store','__pycache__','.claude','.codex','sut','fixtures','wheelhouse','python','hooks','legacy'].includes(x) || x.startsWith('.env.')) || /(?:^|\/)(?:memory_bus|role_governance|role_lineage|sdtd_orchestrator|bugate_update|bugate_init)(?:\.|\/|$)/.test(name) || /\.(?:py|pyc|pyo|whl|pem|key)$/i.test(name)) throw new PackageError('PACKAGE_INVALID', `Forbidden product payload: ${name}`);
  return name;
}
export async function listFiles(root, prefix='') {
  const result={};
  for(const e of (await readdir(join(root,prefix),{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name,'en'))) {
    const name=prefix+e.name; safeName(name);
    if(e.isDirectory()) Object.assign(result,await listFiles(root,name+'/'));
    else if(e.isFile()) result[name]=await readFile(join(root,name));
    else throw new PackageError('PACKAGE_INVALID',`Links and special files forbidden: ${name}`);
  }
  return result;
}
const field=(b,start,len)=>b.subarray(start,start+len).toString('utf8').replace(/\0.*$/s,'');
export function unpackBytes(bytes,{maxBytes=300*1024*1024,maxMembers=20000}={}) {
  let raw; try {raw=gunzipSync(bytes,{maxOutputLength:maxBytes+16*1024*1024});} catch(e){throw new PackageError('PACKAGE_INVALID',`Invalid or oversized gzip: ${e.message}`);}
  const files={},seen=new Set();let total=0,count=0,offset=0,terminated=false;
  while(offset+512<=raw.length) {
    const h=raw.subarray(offset,offset+512);offset+=512;
    if(h.every(x=>x===0)) { terminated=true; if(raw.subarray(offset).some(x=>x!==0)) throw new PackageError('PACKAGE_INVALID','Trailing nonzero archive bytes');break; }
    const sum=Array.from(h,(v,i)=>i>=148&&i<156?32:v).reduce((a,b)=>a+b,0);
    if(parseInt(field(h,148,8).trim(),8)!==sum) throw new PackageError('PACKAGE_INVALID','Tar header checksum mismatch');
    const prefix=field(h,345,155),name=(prefix?prefix+'/':'')+field(h,0,100),type=field(h,156,1);
    const sizeText=field(h,124,12).trim();
    if(!/^[0-7]+$/.test(sizeText)) throw new PackageError('PACKAGE_INVALID','Invalid archive size');
    const size=parseInt(sizeText,8);total+=size;
    if(++count>maxMembers || size>100*1024*1024 || total>maxBytes || offset+size>raw.length) throw new PackageError('PACKAGE_INVALID','Archive exceeds extraction budget or is truncated');
    const normalized=name.replace(/\/$/,'');
    if(normalized!=='package'&&!normalized.startsWith('package/'))throw new PackageError('PACKAGE_INVALID','Expected package/ archive root');
    if(seen.has(normalized))throw new PackageError('PACKAGE_INVALID',`Duplicate archive member: ${normalized}`);seen.add(normalized);
    const relative=normalized.slice(8);
    if(relative) safeName(relative);
    if(type==='5'){ if(size!==0) throw new PackageError('PACKAGE_INVALID','Directory has payload'); }
    else {
      if(type!==''&&type!=='0')throw new PackageError('PACKAGE_INVALID',`Unsupported archive member type ${type}`);
      if(!relative||Object.hasOwn(files,relative))throw new PackageError('PACKAGE_INVALID',`Duplicate archive member: ${relative}`);
      Object.defineProperty(files,relative,{value:Buffer.from(raw.subarray(offset,offset+size)),enumerable:true,writable:true,configurable:true});
    }
    offset+=Math.ceil(size/512)*512;
  }
  if(!terminated||offset>raw.length||!Object.keys(files).length)throw new PackageError('PACKAGE_INVALID','Truncated or empty archive');
  return files;
}
export function packBytes(files) {
  const parts=[];
  for(const [relative,content] of Object.entries(files).sort(([a],[b])=>a<b?-1:1)) {
    safeName(relative);const name='package/'+relative,b=Buffer.from(content),h=Buffer.alloc(512);
    let short=name,prefix='';
    if(Buffer.byteLength(name)>100){const indexes=[...name.matchAll(/\//g)].map(m=>m.index).reverse();const at=indexes.find(i=>Buffer.byteLength(name.slice(0,i))<=155&&Buffer.byteLength(name.slice(i+1))<=100);if(at===undefined)throw new PackageError('PACKAGE_INVALID','Path too long for ustar');prefix=name.slice(0,at);short=name.slice(at+1);}
    h.write(short,0,100,'utf8');h.write('0000644\0',100);h.write('0000000\0',108);h.write('0000000\0',116);h.write(b.length.toString(8).padStart(11,'0')+'\0',124);h.write('00000000000\0',136);h.fill(32,148,156);h[156]=48;h.write('ustar\0',257);h.write('00',263);h.write(prefix,345,155,'utf8');
    const sum=Array.from(h).reduce((a,v)=>a+v,0);h.write(sum.toString(8).padStart(6,'0')+'\0 ',148);parts.push(h,b,Buffer.alloc((512-b.length%512)%512));
  }
  parts.push(Buffer.alloc(1024));return gzipSync(Buffer.concat(parts),{level:9});
}
export function verifyFiles(files) {
  let manifest,inventory;try {manifest=JSON.parse(files['package.json']);inventory=JSON.parse(files['inventory.json']);}catch{throw new PackageError('PACKAGE_INVALID','Package manifest or inventory missing/invalid');}
  if(manifest.name!=='@bugate/deepseek-harness'||manifest.engines?.dsh!=='0.2.2-alpha.2'||JSON.stringify(manifest.dsh?.bundle?.patch)!==JSON.stringify(['./node_modules/@deepseek-ai/dsh-experimental-agent-team-profile/cordis.patch.yml','./cordis.patch.yml']))throw new PackageError('PACKAGE_INVALID','Unsupported product/host identity');
  if(!/^2\.2\.0-dev\.1(?:\.[A-Za-z0-9-]+)*$/.test(manifest.version??'')||manifest.type!=='module'||manifest.main!=='lib/index.js'||JSON.stringify(manifest.bin)!==JSON.stringify({bugate:'node_modules/@bugate/cli/dist/index.js'}))throw new PackageError('PACKAGE_INVALID','Product version or unified CLI entry identity differs');
  if(inventory.apiVersion!=='bugate.node-bundle/v1'||!inventory.files||!Object.keys(inventory.files).length)throw new PackageError('PACKAGE_INVALID','Invalid inventory');
  const actual=Object.keys(files).filter(n=>n!=='inventory.json').sort(),expected=Object.keys(inventory.files).sort();
  if(JSON.stringify(actual)!==JSON.stringify(expected))throw new PackageError('RESOURCE_DIGEST_MISMATCH','Bundle membership differs from inventory');
  for(const name of expected){safeName(name);if(sha256(files[name])!==inventory.files[name])throw new PackageError('RESOURCE_DIGEST_MISMATCH',`Bundle checksum mismatch: ${name}`);}
  for(const name of ['lib/index.js','node_modules/@bugate/core/dist/index.js','node_modules/@bugate/cli/dist/index.js','node_modules/@bugate/install/lifecycle.mjs','resources/compatibility.json'])if(!files[name])throw new PackageError('RESOURCES_MISSING',`Required file absent: ${name}`);
  const compatibility=JSON.parse(files['resources/compatibility.json']);
  if(compatibility.core_version!=='2.2.0-dev.1'||compatibility.protocol_version!=='2.2.0-dev.1'||compatibility.dsh_version!=='0.2.2-alpha.2')throw new PackageError('VERSION_MISMATCH','Unsupported core/protocol/host binding');
  if(files['lib/bridge.js']||files['lib/bridge.d.ts'])throw new PackageError('PACKAGE_INVALID','Retired Python bridge is forbidden');
  const official=files['node_modules/@deepseek-ai/dsh-experimental-agent-team-profile/package.json'];if(!official||JSON.parse(official).version!=='0.2.2-alpha.2'||!files['node_modules/@deepseek-ai/dsh-experimental-agent-team-profile/cordis.patch.yml'])throw new PackageError('RESOURCES_MISSING','Official Team profile missing or mismatched');
  return {manifest,inventory,compatibility};
}
export async function extractPackage(bytes,directory){const files=unpackBytes(bytes);const checked=verifyFiles(files);await mkdir(directory,{recursive:true});for(const [name,data]of Object.entries(files)){const target=resolve(directory,name);if(!target.startsWith(resolve(directory)+sep))throw new PackageError('PATH_ESCAPE','Extraction escape');let cursor=resolve(directory);for(const part of name.split('/').slice(0,-1)){cursor=join(cursor,part);try{if((await lstat(cursor)).isSymbolicLink())throw new PackageError('PATH_ESCAPE','Symlink in extraction');}catch(e){if(e.code!=='ENOENT')throw e;}await mkdir(cursor,{recursive:true});}try{if((await lstat(target)).isSymbolicLink())throw new PackageError('PATH_ESCAPE','Symlink target');}catch(e){if(e.code!=='ENOENT')throw e;}await writeFile(target,data,{flag:'wx'});}return checked;}
