import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import Ajv2020 from 'ajv/dist/2020.js';
import { API_VERSION, VERSION, PROTOCOL_VERSION, BugateError } from './errors.js';
const here = path.dirname(fileURLToPath(import.meta.url));
export function resourceRoot(): string {
    for (const p of [path.resolve(here, '../resources/protocol/v2'), path.resolve(here, '../../../protocol/v2')])
        if (fs.existsSync(path.join(p, 'manifest.yaml')))
            return p;
    throw new BugateError('RESOURCE_MISSING', 'Canonical protocol resources are unavailable', 1);
}
export function codePointCompare(a: string, b: string): number {
    const x = Array.from(a, c => c.codePointAt(0)!), y = Array.from(b, c => c.codePointAt(0)!);
    for (let i = 0; i < Math.min(x.length, y.length); i++)
        if (x[i] !== y[i])
            return x[i] - y[i];
    return x.length - y.length;
}
export function canonical(value: any): Buffer {
    const seen = new Set<any>();
    function encode(v: any): string {
        if (v === null)
            return 'null';
        if (typeof v === 'string') {
            if (!v.isWellFormed())
                throw new BugateError('STRUCTURE_INVALID', 'Unpaired Unicode surrogate');
            return JSON.stringify(v);
        }
        if (typeof v === 'boolean')
            return String(v);
        if (typeof v === 'number') {
            if (!Number.isSafeInteger(v))
                throw new BugateError('STRUCTURE_INVALID', 'Canonical JSON permits only safe integers');
            return String(v === 0 ? 0 : v);
        }
        if (typeof v !== 'object' || seen.has(v))
            throw new BugateError('STRUCTURE_INVALID', 'Expected acyclic JSON data');
        seen.add(v);
        let result: string;
        if (Array.isArray(v)) {
            for (let i = 0; i < v.length; i++)
                if (!Object.hasOwn(v, i))
                    throw new BugateError('STRUCTURE_INVALID', 'Sparse arrays are not JSON data');
            result = '[' + v.map(encode).join(',') + ']';
        }
        else {
            if (Object.getPrototypeOf(v) !== Object.prototype && Object.getPrototypeOf(v) !== null)
                throw new BugateError('STRUCTURE_INVALID', 'Expected plain JSON object');
            result = '{' + Object.keys(v).sort(codePointCompare).map(k => encode(k) + ':' + encode(v[k])).join(',') + '}';
        }
        seen.delete(v);
        return result;
    }
    return Buffer.from(encode(value), 'utf8');
}
export const contentDigest = (data: Uint8Array | string): string => 'sha256:' + createHash('sha256').update(data).digest('hex');
export const digest = (value: any, domain = 'object/v1'): string => contentDigest(Buffer.concat([Buffer.from(domain + '\0'), canonical(value)]));
export const objectDigest = (value: any): string => digest(Object.fromEntries(Object.entries(value).filter(([k]) => !['digest', 'request_digest'].includes(k))), (value.kind ?? 'object') + '/v1');
/** Strict parser retains the canonical integer contract and rejects duplicate keys before JSON.parse can erase them. */
export function parseJson(text: string): any {
    let i = 0;
    const ws = () => { while (/[ \t\r\n]/u.test(text[i] ?? '') && i < text.length)
        i++; };
    const fail = () => { throw new BugateError('STRUCTURE_INVALID', `Invalid canonical JSON at offset ${i}`); };
    function string(): string { const start = i++; while (i < text.length) {
        const c = text[i++];
        if (c === '"') {
            try {
                return JSON.parse(text.slice(start, i));
            }
            catch {
                return fail();
            }
        }
        if (c === '\\')
            i++;
    } return fail(); }
    function value(): any {
        ws();
        const c = text[i];
        if (c === '"')
            return string();
        if (c === '{') {
            i++;
            const out: any = {}, keys = new Set();
            ws();
            if (text[i] === '}') {
                i++;
                return out;
            }
            while (true) {
                ws();
                if (text[i] !== '"')
                    return fail();
                const k = string();
                if (keys.has(k))
                    throw new BugateError('STRUCTURE_INVALID', 'Duplicate JSON key ' + k);
                keys.add(k);
                ws();
                if (text[i++] !== ':')
                    return fail();
                Object.defineProperty(out, k, { value: value(), writable: true, enumerable: true, configurable: true });
                ws();
                if (text[i] === '}') {
                    i++;
                    return out;
                }
                if (text[i++] !== ',')
                    return fail();
            }
        }
        if (c === '[') {
            i++;
            const out = [];
            ws();
            if (text[i] === ']') {
                i++;
                return out;
            }
            while (true) {
                out.push(value());
                ws();
                if (text[i] === ']') {
                    i++;
                    return out;
                }
                if (text[i++] !== ',')
                    return fail();
            }
        }
        for (const [token, result] of [['true', true], ['false', false], ['null', null]] as const)
            if (text.startsWith(token, i)) {
                i += token.length;
                return result;
            }
        const m = /^-?(?:0|[1-9]\d*)/.exec(text.slice(i));
        if (m) {
            i += m[0].length;
            if (/[.eE]/.test(text[i] ?? ''))
                return fail();
            const n = Number(m[0]);
            if (!Number.isSafeInteger(n))
                return fail();
            return n;
        }
        return fail();
    }
    const result = value();
    ws();
    if (i !== text.length)
        fail();
    canonical(result);
    return result;
}
export function loadJson(p: string): any { try {
    if (fs.statSync(p).size > 8 * 1024 * 1024)
        throw new BugateError('OUTPUT_LIMIT', 'JSON input exceeds 8 MiB: ' + path.basename(p));
    const bytes = fs.readFileSync(p);
    return parseJson(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
}
catch (e) {
    if (e instanceof BugateError)
        throw e;
    throw new BugateError('STRUCTURE_INVALID', 'Cannot read valid JSON: ' + path.basename(p), 2, String(e));
} }
const schemaNames: Record<string, string> = Object.assign(Object.create(null), Object.fromEntries(['ProtocolBinding', 'TestTask', 'Artifact', 'Evidence', 'Claim', 'AssessmentRequest', 'AssessmentResult', 'Profile', 'PreparedProtocolContext', 'MethodSpec', 'CommandResult', 'ExecutionRecord', 'ScopeContract', 'ScopeRequest', 'Suggestion', 'HostObservation', 'HostSettlement', 'ReviewReceipt'].map(x => [x, x.replace(/[A-Z]/g, c => '_' + c.toLowerCase()).slice(1)])));
export function schema(name: string): any { if (!Object.hasOwn(schemaNames, name) && (typeof name !== 'string' || !/^[a-z][a-z0-9_]*$/.test(name))) throw new BugateError('SCHEMA_UNKNOWN', 'Unknown schema: ' + name); const p = path.join(resourceRoot(), 'schemas', (schemaNames[name] ?? name) + '.schema.json'); if (!fs.existsSync(p))
    throw new BugateError('SCHEMA_UNKNOWN', 'Unknown schema: ' + name); return loadJson(p); }
let validator: any;
let validatorKey = '';
export function validateSchema(value: any, kind?: string): any {
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new BugateError('STRUCTURE_INVALID', 'Expected a JSON object');
    const root = resourceRoot();
    const files = fs.readdirSync(path.join(root, 'schemas')).filter(x => x.endsWith('.json')).sort();
    const key = files.map(x => { const s = fs.statSync(path.join(root, 'schemas', x)); return x + ':' + s.mtimeMs + ':' + s.size; }).join('|');
    if (!validator || validatorKey !== key) {
        const Ctor: any = Ajv2020;
        validator = new Ctor({ allErrors: true, strict: false, validateFormats: false });
        for (const f of files)
            validator.addSchema(loadJson(path.join(root, 'schemas', f)));
        validatorKey = key;
    }
    const selected = schema(kind ?? value.kind ?? 'unknown');
    const validate = validator.getSchema(selected.$id);
    if (!validate(value))
        throw new BugateError('STRUCTURE_INVALID', 'Schema validation failed', 2, validate.errors?.slice(0, 30).map((e: any) => ({ path: e.instancePath || '/', message: e.message })));
    canonical(value);
    if (value.kind === 'PreparedProtocolContext' && value.render.bytes !== Buffer.byteLength(value.render.content))
        throw new BugateError('STRUCTURE_INVALID', 'render.bytes differs from UTF-8 content length');
    return value;
}
export function walk(root: string): string[] { const out: string[] = []; if (!fs.existsSync(root))
    return out; for (const e of fs.readdirSync(root, { withFileTypes: true }).sort((a, b) => codePointCompare(a.name, b.name))) {
    const p = path.join(root, e.name);
    out.push(p);
    if (e.isDirectory())
        out.push(...walk(p));
} return out; }
export function protocolIdentity(): any { const root = resourceRoot(), files: any = Object.create(null); for (const p of walk(root))
    if (fs.lstatSync(p).isFile() && !p.includes('__pycache__'))
        files[path.relative(root, p).split(path.sep).join('/')] = contentDigest(fs.readFileSync(p)); const manifest = loadJson(path.join(root, 'manifest.yaml')); return { id: 'bugate', version: manifest.metadata.version, digest: digest(files, 'protocol-bundle/v1') }; }
export function evaluatorIdentity(): string { const files: any = Object.create(null); for (const file of fs.readdirSync(here).sort())
    if (file.endsWith('.js') || file.endsWith('.ts') && !file.endsWith('.d.ts'))
        files[file] = contentDigest(fs.readFileSync(path.join(here, file))); return digest({ version: VERSION, files, protocol: protocolIdentity() }, 'evaluator/v2'); }
export const qualityRules = () => loadJson(path.join(resourceRoot(), 'rules/quality.json'));
export function methods(): any[] { const result = fs.readdirSync(path.join(resourceRoot(), 'methods')).filter(x => x.endsWith('.json')).sort().map(x => validateSchema(loadJson(path.join(resourceRoot(), 'methods', x)), 'MethodSpec')); if (!result.length)
    throw new BugateError('RESOURCE_MISSING', 'No MethodSpec resources', 1); return result; }
