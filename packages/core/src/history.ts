import fs from 'node:fs';
import path from 'node:path';
import { BugateError } from './errors.js';
import { loadJson, contentDigest, walk } from './protocol.js';
import { within } from './workspace.js';
/** Read-only historical inspection deliberately does not resolve an active Workspace or grant authorization. */
export function inspectHistory(task: string): any {
    const requested = path.resolve(task);
    if (!fs.existsSync(requested) || !fs.statSync(requested).isDirectory())
        throw new BugateError('TASK_NOT_FOUND', 'Historical task directory does not exist');
    const parts = requested.split(path.sep);
    let cursor = path.parse(requested).root;
    for (const part of parts.filter(Boolean)) {
        cursor = path.join(cursor, part);
        if (fs.lstatSync(cursor).isSymbolicLink())
            throw new BugateError('PATH_SYMLINK', 'Historical inspection rejects symbolic-link task roots');
    }
    const root = fs.realpathSync(requested);
    let total = 0;
    const records: any[] = [];
    const selected = ['task.json', 'profile.json', '.bugate/protocol.lock.json', 'scope/active.json'];
    for (const folder of ['artifacts', 'evidence', 'claims', 'assessments', '.bugate/artifact-history', '.bugate/scope-history', '.bugate/host-observations', '.bugate/host-settlements', '.bugate/host-authorities'])
        for (const p of walk(within(root, folder)))
            if (fs.lstatSync(p).isSymbolicLink())
                throw new BugateError('PATH_SYMLINK', 'Historical records contain a symbolic link');
            else if (fs.lstatSync(p).isFile() && p.endsWith('.json'))
                selected.push(path.relative(root, p));
    for (const rel of selected) {
        const p = within(root, rel);
        if (!fs.existsSync(p))
            continue;
        const bytes = fs.readFileSync(p);
        total += bytes.length;
        if (total > 32 * 1024 * 1024 || records.length >= 2048)
            throw new BugateError('OUTPUT_LIMIT', 'Historical view exceeds finite record bound');
        const value = loadJson(p);
        records.push({ path: rel, digest: contentDigest(bytes), bytes: bytes.length, kind: value.kind ?? null, id: value.id ?? null, recorded_version: value.schema_version ?? value.evaluator_version ?? value.protocol?.version ?? value.version ?? null, value });
    }
    return { mode: 'historical_read_only', authorization: false, verification: 'unverified_historical_records', task_root: root, records, limitations: ['Original recorded versions and failures are preserved. This command does not assess current validity, convert formats or create execution authority.', 'Local file integrity is observable; historical producer claims are not newly authenticated.'] };
}
