import { createRequire } from 'node:module';
import { cp, mkdir, readFile, writeFile, readdir, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('../../../', import.meta.url));
execFileSync(process.execPath, [createRequire(import.meta.url).resolve('typescript/bin/tsc')], { cwd: fileURLToPath(new URL('.', import.meta.url)), stdio: 'inherit' });
await mkdir(new URL('resources', import.meta.url), { recursive: true });
await rm(new URL('resources/protocol', import.meta.url), { recursive: true, force: true });
await cp(resolve(root, 'protocol/v2'), new URL('resources/protocol', import.meta.url), { recursive: true });
const sha = bytes => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;
const files = {};
async function index(directory, prefix = '') {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) await index(resolve(directory, entry.name), name + '/');
    else if (entry.isFile()) files[name] = sha(await readFile(resolve(directory, entry.name)));
  }
}
await index(resolve(root, 'protocol/v2'));
const protocolDigest = sha(Buffer.concat([Buffer.from('protocol-bundle/v1\0'), Buffer.from(JSON.stringify(Object.fromEntries(Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))))]));
const methodFiles = Object.keys(files).filter(name => name.startsWith('methods/') && name.endsWith('.json')).sort();
if (!methodFiles.length) throw new Error('Canonical MethodSpec resources are required before building');
const canonicalFiles = [...methodFiles, 'rules/quality.json'];
const canonical = await Promise.all(canonicalFiles.map(async file => `\n## Canonical ${file}\n\n\`\`\`json\n${(await readFile(resolve(root, 'protocol/v2', file), 'utf8')).trim()}\n\`\`\`\n`));
const entry = await readFile(resolve(root, 'views/skills/bugate/SKILL.md'), 'utf8');
await writeFile(new URL('resources/SKILL.md', import.meta.url), entry + '\nProtocol digest: `' + protocolDigest + '`\n' + canonical.join(''));
await writeFile(new URL('resources/projection.json', import.meta.url), JSON.stringify({ protocol_digest: protocolDigest, method_files: methodFiles, skill_digest: sha(entry + '\nProtocol digest: `' + protocolDigest + '`\n' + canonical.join('')) }, null, 2) + '\n');
const coreVersion = JSON.parse(await readFile(resolve(root, 'packages/core/package.json'), 'utf8')).version;
if (!coreVersion) throw new Error('Canonical TypeScript package version is missing');
const manifest = JSON.parse(await readFile(new URL('package.json', import.meta.url), 'utf8'));
const protocolVersion = JSON.parse(await readFile(resolve(root, 'protocol/v2/manifest.yaml'), 'utf8')).metadata.version;
await writeFile(new URL('resources/compatibility.json', import.meta.url), JSON.stringify({
  adapter_version: manifest.version, core_version: coreVersion, protocol_version: protocolVersion,
  dsh_version: '0.2.2-alpha.2', dsh_commit: 'f3803c96974ad4a94195fb97e5c637e9c2a3afe3',
}, null, 2) + '\n');
