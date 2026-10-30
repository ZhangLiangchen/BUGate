#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { listFiles, sha256, unpackBytes, verifyFiles } from '../packaging/archive.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
const resources=JSON.parse(await readFile(resolve(root,'packaging/resources.json'),'utf8'));
const actual=Object.keys(await listFiles(resolve(root,'protocol/v2'))).map(n=>'protocol/v2/'+n).sort();
if(JSON.stringify(actual)!==JSON.stringify(resources.protocol_files.slice().sort()))throw Error('Protocol resource inventory differs');
const index=process.argv.indexOf('--bundle');
if(index>=0){const bytes=await readFile(resolve(process.argv[index+1])),files=unpackBytes(bytes),checked=verifyFiles(files);for(const name of resources.protocol_files){const relative=name.slice('protocol/v2/'.length),expected=await readFile(resolve(root,name));if(!files['resources/protocol/'+relative]?.equals(expected)||!files['node_modules/@bugate/core/resources/protocol/v2/'+relative]?.equals(expected))throw Error(`Canonical resource mismatch: ${name}`);}console.log(JSON.stringify({status:'PASS',bundle_sha256:sha256(bytes),members:Object.keys(files).length,version:checked.manifest.version}));}
else console.log(JSON.stringify({status:'PASS',protocol_files:actual.length}));
