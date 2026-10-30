import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { randomUUID } from 'node:crypto';
import { BugateError, API_VERSION, eq } from './errors.js';
import { canonical, contentDigest, digest, loadJson, objectDigest, protocolIdentity, validateSchema, methods, evaluatorIdentity, walk } from './protocol.js';
export function safeId(value: any): string { if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value) || ['.', '..'].includes(value))
    throw new BugateError('PATH_OUTSIDE_SCOPE', 'Invalid object identity'); return value; }
export function within(root: string, value: string, mustExist = false): string {
    root = fs.realpathSync(root);
    if (typeof value !== 'string' || value.includes('\0'))
        throw new BugateError('PATH_OUTSIDE_SCOPE', 'Invalid path');
    if (value.split(/[\\/]/).includes('..'))
        throw new BugateError('PATH_OUTSIDE_SCOPE', 'Parent traversal is not allowed');
    const candidate = path.isAbsolute(value) ? value : path.join(root, value), rel = path.relative(root, candidate);
    if (rel === '..' || rel.startsWith('..' + path.sep) || path.isAbsolute(rel))
        throw new BugateError('PATH_OUTSIDE_SCOPE', 'Path is outside authorized root');
    let cur = root;
    for (const part of rel.split(path.sep).filter(Boolean)) {
        cur = path.join(cur, part);
        try {
            if (fs.lstatSync(cur).isSymbolicLink())
                throw new BugateError('PATH_SYMLINK', 'Symbolic links are not authorized in task paths');
        }
        catch (e) {
            if ((e as any).code !== 'ENOENT')
                throw e;
        }
    }
    if (mustExist && (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()))
        throw new BugateError('REFERENCE_MISSING', 'Missing file: ' + rel);
    return candidate;
}
export function atomicWrite(root: string, value: string, data: Uint8Array, options: {
    expected?: string;
    immutable?: boolean;
} = {}): void {
    let p = within(root, value);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    p = within(root, value);
    if (fs.existsSync(p)) {
        const old = fs.readFileSync(p);
        if (options.immutable) {
            if (old.equals(Buffer.from(data)))
                return;
            throw new BugateError('CONCURRENT_CONFLICT', 'Identity already has different immutable content');
        }
        if (options.expected !== undefined && contentDigest(old) !== options.expected)
            throw new BugateError('CONCURRENT_CONFLICT', 'Compare-and-swap content changed');
    }
    else if (options.expected !== undefined)
        throw new BugateError('CONCURRENT_CONFLICT', 'Compare-and-swap target disappeared');
    const tmp = path.join(path.dirname(p), '.pending-' + randomUUID());
    let fd: number | undefined;
    try {
        fd = fs.openSync(tmp, 'wx', 0o600);
        fs.writeFileSync(fd, data);
        fs.fsyncSync(fd);
        fs.closeSync(fd);
        fd = undefined;
        within(root, value);
        fs.renameSync(tmp, p);
        const dfd = fs.openSync(path.dirname(p), 'r');
        try {
            fs.fsyncSync(dfd);
        }
        finally {
            fs.closeSync(dfd);
        }
    }
    finally {
        if (fd !== undefined)
            fs.closeSync(fd);
        if (fs.existsSync(tmp))
            fs.unlinkSync(tmp);
    }
}
export const writeJson = (root: string, value: string, obj: any, options: {
    expected?: string;
    immutable?: boolean;
} = {}) => atomicWrite(root, value, Buffer.concat([canonical(obj), Buffer.from('\n')]), options);
/** Nonblocking interprocess lock. Unknown/stale owners require explicit recovery; never silently steal a lock. */
export function locked<T>(root: string, fn: () => T): T {
    const p = within(root, '.bugate/write.lock');
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const token = randomUUID();
    try {
        fs.mkdirSync(p, { mode: 0o700 });
    }
    catch (e) {
        if ((e as any).code === 'EEXIST')
            throw new BugateError('CONCURRENT_CONFLICT', 'Another writer owns the task transaction', 2, { lock: p, recovery: 'Inspect owner.json; after proving the owner stopped, preserve and rename the lock before retry.' });
        throw e;
    }
    try {
        fs.writeFileSync(path.join(p, 'owner.json'), canonical({ pid: process.pid, host: os.hostname(), token }), { mode: 0o600, flag: 'wx' });
        const result = fn();
        if (result && typeof (result as any).then === 'function')
            throw new BugateError('RUNTIME_FAILURE', 'Workspace locked callback must be synchronous', 1);
        return result;
    }
    finally {
        try {
            if (loadJson(path.join(p, 'owner.json')).token === token)
                fs.rmSync(p, { recursive: true });
        }
        catch { /* retain unknown ownership */ }
    }
}
export class Workspace {
    root: string;
    project: string;
    task: any;
    profile: any;
    binding: any;
    id: string;
    constructor(task: string) { const requested = path.resolve(task); if (!fs.existsSync(requested) || !fs.statSync(requested).isDirectory())
        throw new BugateError('TASK_NOT_FOUND', 'Task directory does not exist'); this.root = fs.realpathSync(requested); this.task = validateSchema(loadJson(within(this.root, 'task.json', true)), 'TestTask'); this.project = fs.realpathSync(this.task.project_root); within(this.project, requested); if (this.root !== this.task.task_root)
        throw new BugateError('WORKSPACE_MISMATCH', 'Task moved or workspace path differs from identity'); this.profile = validateSchema(loadJson(within(this.root, 'profile.json', true)), 'Profile'); const lock = within(this.root, '.bugate/protocol.lock.json'); if (!fs.existsSync(lock))
        throw new BugateError('BINDING_MISSING', 'Exact ProtocolBinding is missing'); this.binding = validateSchema(loadJson(lock), 'ProtocolBinding'); const expected = protocolIdentity(); if (!eq(this.binding.protocol, expected))
        throw new BugateError('PROTOCOL_MISMATCH', 'Protocol version or digest differs from lock', 2, { expected: this.binding.protocol, actual: expected }); const pi = { id: this.profile.id, version: this.profile.version, digest: objectDigest(this.profile) }; if (!eq(this.binding.profile, pi))
        throw new BugateError('PROFILE_MISMATCH', 'Profile content differs from lock'); this.id = digest({ project: this.project, task: this.root, task_id: this.task.id, protocol: expected, profile: pi }, 'workspace-identity/v1'); if (this.binding.workspace_id !== this.id || this.task.workspace_id !== this.id)
        throw new BugateError('WORKSPACE_MISMATCH', 'Task, binding and filesystem identities disagree'); if (!eq(this.binding.methods, methods().map(m => m.id)))
        throw new BugateError('PROTOCOL_MISMATCH', 'Method binding differs from canonical protocol'); for (const field of ['goal', 'requirements', 'risks', 'delivery_criteria', 'unknowns'])
        if (!eq(this.task[field], this.profile[field]))
            throw new BugateError('TASK_SCOPE_MISMATCH', 'Task fields differ from the locked Profile', 2, field); if (!eq(this.task.source_ids, this.profile.sources.map(s => s.id)))
        throw new BugateError('TASK_SCOPE_MISMATCH', 'Task sources differ from locked Profile'); }
    path(relative: string, mustExist = false): string { return within(this.root, relative, mustExist); }
    read(relative: string): any { return loadJson(this.path(relative, true)); }
    objects(folder: string, kind: string): any[] { const p = this.path(folder); if (!fs.existsSync(p))
        return []; const seen = new Set(); return fs.readdirSync(p).filter(x => x.endsWith('.json')).sort().map(file => { const v = validateSchema(loadJson(this.path(path.join(p, file), true)), kind); if (seen.has(v.id))
        throw new BugateError('DUPLICATE_ID', 'Duplicate ' + kind + ' identity: ' + v.id); seen.add(v.id); return v; }); }
    snapshot(): string { const values: any = Object.assign(Object.create(null), { 'binding-file': contentDigest(fs.readFileSync(this.path('.bugate/protocol.lock.json', true))) }); if (!eq(this.read('.bugate/protocol.lock.json'), this.binding) || !eq(this.read('profile.json'), this.profile) || !eq(this.read('task.json'), this.task))
        throw new BugateError('CONCURRENT_CONFLICT', 'Task, Profile or Binding changed after workspace resolution'); for (const p of walk(this.root)) {
        const rel = path.relative(this.root, p), parts = rel.split(path.sep);
        if (['claims', 'assessments', '.bugate', 'suggestions'].includes(parts[0]) || parts.some(x => x.startsWith('.pending-')))
            continue;
        const stat = fs.lstatSync(p);
        if (stat.isSymbolicLink())
            throw new BugateError('PATH_SYMLINK', 'Task contains a symbolic link: ' + rel);
        if (stat.isFile()) {
            if (p.endsWith('.json') && !['artifacts', 'evidence'].includes(parts[0])) {
                try {
                    const v = loadJson(p);
                    if (['Claim', 'AssessmentRequest', 'AssessmentResult', 'PreparedProtocolContext'].includes(v?.kind))
                        continue;
                }
                catch (e) {
                    if (!(e instanceof BugateError))
                        throw e;
                }
            }
            values['task/' + rel.split(path.sep).join('/')] = contentDigest(fs.readFileSync(p));
        }
    } const authorities = new Set<string>(); for (const folder of ['host-observations', 'host-settlements'])
        for (const r of walk(this.path('.bugate/' + folder)))
            if (fs.lstatSync(r).isFile()) {
                within(this.root, r, true);
                values['host-observation:' + path.basename(r)] = contentDigest(fs.readFileSync(r));
                const receipt = loadJson(r);
                if (receipt.authentication?.authority_id)
                    authorities.add(safeId(receipt.authentication.authority_id));
            } for (const id of authorities) {
        const pin = this.path('.bugate/host-authorities/' + id + '.json');
        values['host-authority:' + id] = fs.existsSync(pin) ? contentDigest(fs.readFileSync(pin)) : null;
    } for (const source of this.profile.sources)
        values['source:' + source.id] = contentDigest(fs.readFileSync(within(this.project, source.path, true))); return digest({ files: values, binding: this.binding, evaluator: evaluatorIdentity() }, 'workspace-snapshot/v1'); }
}
export function initialize(project: string, taskId: string, profileFile: string): any { project = fs.realpathSync(project); safeId(taskId); const profile = validateSchema(loadJson(path.resolve(profileFile)), 'Profile'), sourceIds = profile.sources.map(s => s.id); if (profile.team_review_policy) {
    const ps = profile.team_review_policy.perspectives;
    if (new Set(ps.map(p => p.id)).size !== ps.length)
        throw new BugateError('DUPLICATE_ID', 'Duplicate Profile review perspective id');
    for (const p of ps)
        if (p.source_refs.some(id => !sourceIds.includes(id)))
            throw new BugateError('REFERENCE_MISSING', 'Review perspective source is undeclared', 2, p.id);
} if (new Set(sourceIds).size !== sourceIds.length)
    throw new BugateError('DUPLICATE_ID', 'Duplicate Profile source id'); for (const s of profile.sources)
    if (contentDigest(fs.readFileSync(within(project, s.path, true))) !== s.digest)
        throw new BugateError('REFERENCE_DIGEST_MISMATCH', 'Profile source bytes do not match their digest', 2, s.id); const root = within(project, path.join('test-task', taskId)), existed = fs.existsSync(path.join(root, 'task.json')); fs.mkdirSync(root, { recursive: true }); const protocol = protocolIdentity(), pi = { id: profile.id, version: profile.version, digest: objectDigest(profile) }, ident = digest({ project, task: root, task_id: taskId, protocol, profile: pi }, 'workspace-identity/v1'); const binding = { apiVersion: API_VERSION, kind: 'ProtocolBinding', id: 'binding-' + taskId, protocol, profile: pi, methods: methods().map(m => m.id), resolved_from: '.bugate/protocol.lock.json', workspace_id: ident }, task = { apiVersion: API_VERSION, kind: 'TestTask', id: taskId, workspace_id: ident, project_root: project, task_root: root, goal: profile.goal, requirements: profile.requirements, risks: profile.risks, delivery_criteria: profile.delivery_criteria, source_ids: sourceIds, unknowns: profile.unknowns }; locked(root, () => { for (const [file, value] of [['task.json', task], ['profile.json', profile], ['.bugate/protocol.lock.json', binding]] as const) {
    validateSchema(value);
    writeJson(root, file, value, { immutable: true });
} for (const folder of ['artifacts', 'evidence', 'claims', 'assessments', 'logs'])
    fs.mkdirSync(within(root, folder), { recursive: true }); }); return { task_root: root, task, binding, created: !existed }; }
export function discover(project: string): Workspace { project = fs.realpathSync(project); const dir = path.join(project, 'test-task'); const candidates = fs.existsSync(dir) ? fs.readdirSync(dir).sort().filter(x => fs.existsSync(path.join(dir, x, 'task.json')) && !fs.lstatSync(path.join(dir, x)).isSymbolicLink()).map(x => path.join(dir, x)) : []; if (!candidates.length)
    throw new BugateError('TASK_NOT_FOUND', 'No BUGate task in project'); if (candidates.length !== 1)
    throw new BugateError('TASK_AMBIGUOUS', 'Multiple tasks require explicit selection', 2, { candidates }); return new Workspace(candidates[0]); }
export function executionInputManifest(w: Workspace): any { const values: any = Object.assign(Object.create(null), { '@protocol': w.binding.protocol.digest, '@profile': w.binding.profile.digest, '@evaluator': evaluatorIdentity() }); for (const s of w.profile.sources)
    values['@source/' + s.id] = contentDigest(fs.readFileSync(within(w.project, s.path, true))); for (const p of walk(w.root)) {
    const rel = path.relative(w.root, p), parts = rel.split(path.sep);
    if (['evidence', 'claims', 'assessments', 'logs', '.bugate', 'suggestions'].includes(parts[0]) || parts.some(x => ['__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'].includes(x)))
        continue;
    const stat = fs.lstatSync(p);
    if (stat.isSymbolicLink())
        throw new BugateError('PATH_SYMLINK', 'Execution input contains symbolic link');
    if (!stat.isFile())
        continue;
    if (p.endsWith('.json')) {
        try {
            const v = loadJson(p);
            if (['Claim', 'AssessmentRequest', 'AssessmentResult', 'PreparedProtocolContext'].includes(v?.kind) || (parts[0] === 'artifacts' && (['execution_report', 'knowledge', 'diagnosis'].includes(v?.artifact_type) || (v?.artifact_type === 'semantic_review' && Object.keys(v.content?.artifact_digests ?? {}).every(id => { try {
                return ['execution_report', 'knowledge', 'diagnosis'].includes(w.read('artifacts/' + safeId(id) + '.json').artifact_type);
            }
            catch {
                return false;
            } })))))
                continue;
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
        }
    }
    values['task/' + rel.split(path.sep).join('/')] = contentDigest(fs.readFileSync(p));
} return Object.fromEntries(Object.entries(values)); }
export const FILESYSTEM_LIMITS = { max_files: 2048, max_directories: 2048, max_total_bytes: 33554432 };
export const FILESYSTEM_EXCLUSIONS = { exact: ['.bugate/write.lock'], patterns: [] };
export const FILESYSTEM_RUNTIME_DIRECTORIES = ['.bugate/runtime', '.bugate/write.lock'];
export function filesystemManifest(w: Workspace): any { const files: any[] = [], directories: string[] = [], sources: any[] = []; let total = 0; function entry(p: string, label: string) { const stat = fs.statSync(p); if (total + stat.size > FILESYSTEM_LIMITS.max_total_bytes)
    throw new BugateError('OUTPUT_LIMIT', 'Execution-world filesystem witness exceeds its byte bound', 1); const bytes = fs.readFileSync(p); if (bytes.length !== stat.size)
    throw new BugateError('CONCURRENT_CONFLICT', 'Filesystem file size changed during execution-world verification'); total += bytes.length; return { path: label, digest: contentDigest(bytes), bytes: bytes.length }; } for (const p of walk(w.root)) {
    const rel = path.relative(w.root, p).split(path.sep).join('/');
    within(w.root, p);
    if (FILESYSTEM_RUNTIME_DIRECTORIES.some(d => rel === d || rel.startsWith(d + '/')))
        continue;
    const s = fs.lstatSync(p);
    if (s.isDirectory()) {
        directories.push(rel);
        if (directories.length > FILESYSTEM_LIMITS.max_directories)
            throw new BugateError('OUTPUT_LIMIT', 'Execution-world directory count exceeds its bound', 1);
    }
    else if (s.isFile()) {
        if (FILESYSTEM_EXCLUSIONS.exact.includes(rel))
            continue;
        if (files.length + sources.length >= FILESYSTEM_LIMITS.max_files)
            throw new BugateError('OUTPUT_LIMIT', 'Execution-world file count exceeds its bound', 1);
        files.push(entry(p, rel));
    }
    else
        throw new BugateError('PATH_OUTSIDE_SCOPE', 'Execution-world witness requires regular files and directories');
} for (const source of w.profile.sources) {
    if (files.length + sources.length >= FILESYSTEM_LIMITS.max_files)
        throw new BugateError('OUTPUT_LIMIT', 'Execution-world file count exceeds its bound', 1);
    const p = within(w.project, source.path, true);
    sources.push({ id: source.id, ...entry(p, p) });
} return { version: 1, task_root: w.root, task_files: files, task_directories: directories, sources, excluded_runtime_files: FILESYSTEM_EXCLUSIONS, excluded_runtime_directories: FILESYSTEM_RUNTIME_DIRECTORIES, limits: FILESYSTEM_LIMITS }; }
