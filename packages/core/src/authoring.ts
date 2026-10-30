import fs from 'node:fs';
import { BugateError, eq, values } from './errors.js';
import { contentDigest, loadJson, objectDigest, validateSchema } from './protocol.js';
import { Workspace, within, locked, writeJson, safeId } from './workspace.js';
import { activeScope, scopeRef, recordSuggestion } from './scope.js';
import { HostContext, createHostObservation, publishHostObservation } from './team.js';
export function recordArtifact(task: string, inputFile: string, options: {
    expectedDigest?: string;
} = {}): any { return author(task, loadJson(within(new Workspace(task).root, inputFile, true)), options); }
export function publishArtifact(task: string, artifact: any, options: {
    expectedDigest?: string;
    hostContext: HostContext;
}): any { return author(task, artifact, options); }
function author(task: string, input: any, options: {
    expectedDigest?: string;
    hostContext?: HostContext;
}): any { const w = new Workspace(task), value = structuredClone(input); if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new BugateError('STRUCTURE_INVALID', 'Artifact input must be an object'); safeId(value.id); if (value.kind !== 'Artifact')
    throw new BugateError('STRUCTURE_INVALID', 'Artifact authoring requires kind Artifact'); const target = 'artifacts/' + value.id + '.json', content = value.content ?? {}; if (value.artifact_type === 'inventory')
    for (const c of content.cases ?? []) {
        const ref = c.implementation ?? {};
        if (ref.path) {
            const p = within(w.root, ref.path);
            if (fs.existsSync(p) && fs.statSync(p).isFile()) {
                const actual = contentDigest(fs.readFileSync(p));
                if (ref.digest == null)
                    ref.digest = actual;
                else if (ref.digest !== actual)
                    throw new BugateError('REFERENCE_DIGEST_MISMATCH', 'Implementation reference changed');
            }
        }
    } if (value.artifact_type === 'semantic_review')
    for (const [aid, expected] of Object.entries(content.artifact_digests ?? {})) {
        const existing = validateSchema(w.read('artifacts/' + safeId(aid) + '.json'), 'Artifact'), actual = objectDigest(existing);
        if (existing.digest !== actual)
            throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Reviewed artifact has invalid digest');
        if (expected === null && options.hostContext)
            throw new BugateError('REVIEW_BINDING_STALE', 'Native review publication requires the exact digest observed by the reviewer; unresolved references cannot be stamped with current bytes');
        if (expected === null)
            content.artifact_digests[aid] = actual;
        else if (expected !== actual)
            throw new BugateError('SEMANTIC_REVIEW_STALE', 'Review does not refer to current artifact bytes');
    } const actual = objectDigest(value); if (value.digest !== undefined && value.digest !== actual)
    throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Artifact digest differs from actual content'); value.digest = actual; validateSchema(value, 'Artifact'); if (value.artifact_type === 'inventory') {
    const scope = activeScope(w);
    if (!eq(content.scope_ref, scopeRef(scope)))
        throw new BugateError('SCOPE_STALE', 'Inventory draft does not bind the current scope');
    const allowed = scope.scenarios.map(c => c.id), extras = content.cases.filter(c => !allowed.includes(c.scenario_id));
    if (extras.length) {
        const ids = [];
        for (const c of extras) {
            const sid = 'extension-' + objectDigest({ case: c.id, scope: scope.digest }).split(':')[1].slice(0, 32);
            recordSuggestion(task, undefined, { id: sid, description: 'Proposed additional scenario ' + c.scenario_id, rationale: 'No required scenario in the current scope. Proposed executable details were not admitted.', source_refs: value.source_refs });
            ids.push(sid);
        }
        throw new BugateError('SCENARIO_OUT_OF_SCOPE', 'Unaccepted extensions were recorded as suggestions; the active inventory was not changed', 2, { suggestion_ids: ids });
    }
} const sources = w.profile.sources.map(s => s.id); for (const ref of value.source_refs)
    if (!sources.includes(ref))
        throw new BugateError('REFERENCE_MISSING', 'Artifact source is absent from Profile', 2, ref); const existing = w.objects('artifacts', 'Artifact'), ids = new Set([...existing.map(a => a.id), ...w.objects('evidence', 'Evidence').map(e => e.id), ...sources, value.id]); for (const a of [...existing, value])
    for (const group of ['propositions', 'oracles', 'cases'])
        for (const item of a.content[group] ?? [])
            ids.add(item.id); for (const ref of value.relations)
    if (!ids.has(ref))
        throw new BugateError('REFERENCE_MISSING', 'Artifact relation is absent', 2, ref); const before = w.snapshot(); locked(w.root, () => { if (w.snapshot() !== before)
    throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed during artifact authoring'); const p = w.path(target); let unchanged = false; if (fs.existsSync(p)) {
    const old = validateSchema(loadJson(p), 'Artifact');
    if (old.digest !== objectDigest(old))
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Current artifact digest is invalid');
    unchanged = eq(old, value);
    if (!unchanged) {
        if (options.expectedDigest !== old.digest)
            throw new BugateError('CONCURRENT_CONFLICT', 'Updating an artifact requires its current expected_digest', 2, { current_digest: old.digest });
        writeJson(w.root, '.bugate/artifact-history/' + value.id + '/' + old.digest.split(':')[1] + '.json', old, { immutable: true });
    }
}
else if (options.expectedDigest !== undefined)
    throw new BugateError('CONCURRENT_CONFLICT', 'Expected artifact does not exist'); if (options.hostContext) {
    const observation = createHostObservation(w, value, options.hostContext);
    publishHostObservation(w, observation);
} if (!unchanged)
    writeJson(w.root, target, value); }); return value; }
