import fs from 'node:fs';
import { API_VERSION, VERSION, BugateError, eq, subset, intersect } from './errors.js';
import { canonical, codePointCompare, contentDigest, digest, objectDigest, loadJson, parseJson, validateSchema, evaluatorIdentity } from './protocol.js';
import { Workspace, within, safeId, writeJson, locked } from './workspace.js';
import { activeScope, scopeRef } from './scope.js';
import { verifyHostContext, observedContext, receiptContext } from './host-auth.js';
export interface HostContext {
    host: string;
    host_version: string;
    execution?: any;
    messages?: any[];
    artifact?: any;
    user_request?: any;
    settlement?: any;
    authentication?: any;
}
export function reviewInputSnapshot(w: Workspace, review?: any): string {
    const all = w.objects('artifacts', 'Artifact'), designTypes = ['business_brief', 'testability', 'inventory', 'domain_model', 'state_model', 'dimension_matrix', 'adversarial_review'];
    const targets = Object.keys(review?.content?.artifact_digests ?? {}), byId = Object.assign(Object.create(null), Object.fromEntries(all.map(a => [a.id, a])));
    const hasDesign = targets.length === 0 || targets.some(id => designTypes.includes(byId[id]?.artifact_type));
    const included = new Set<string>(hasDesign ? all.filter(a => designTypes.includes(a.artifact_type)).map(a => a.id) : []);
    for (const id of targets)
        included.add(id);
    for (const id of included)
        for (const ref of byId[id]?.relations ?? [])
            if (byId[ref] && byId[ref].artifact_type !== 'semantic_review')
                included.add(ref);
    const relevant = [...included].filter(id => byId[id] && byId[id].artifact_type !== 'semantic_review').map(id => byId[id]);
    const artifacts = relevant.map(a => ({ id: a.id, digest: objectDigest(a) })).sort((a, b) => codePointCompare(a.id, b.id));
    const evidenceIds = new Set<string>([...relevant.flatMap(a => a.content?.evidence_ids ?? []), ...(review?.content?.findings ?? []).flatMap(x => [...x.evidence_refs, ...x.counterevidence]), ...(review?.content?.cross_checks ?? []).flatMap(x => x.evidence_refs)]);
    const evidence = w.objects('evidence', 'Evidence').filter(e => evidenceIds.has(e.id)).map(e => ({ id: e.id, digest: objectDigest(e), references: e.references.map(ref => ({ path: ref.path, digest: contentDigest(fs.readFileSync(within(w.root, ref.path, true))) })) })).sort((a, b) => codePointCompare(a.id, b.id));
    const implementations: any = Object.create(null);
    for (const a of relevant)
        for (const c of a.content?.cases ?? [])
            if (c.implementation?.path) {
                const p = within(w.root, c.implementation.path);
                implementations[c.implementation.path] = fs.existsSync(p) ? contentDigest(fs.readFileSync(p)) : null;
            }
    const source_bytes = Object.fromEntries(w.profile.sources.map(s => [s.id, contentDigest(fs.readFileSync(within(w.project, s.path, true)))]));
    const plans = all.filter(a => a.artifact_type === 'inventory');
    return digest({ binding: w.binding, evaluator: evaluatorIdentity(), source_bytes, artifacts, evidence, implementations, scope: scopeRef(activeScope(w, false)), plan: plans.length === 1 ? { id: plans[0].id, digest: plans[0].digest } : null }, 'team-review-input/v1');
}
export const hostObservations = (w: Workspace): any[] => w.objects('.bugate/host-observations', 'HostObservation');
export const hostSettlements = (w: Workspace): any[] => w.objects('.bugate/host-settlements', 'HostSettlement');
function refArtifact(w: Workspace, ref: any): any | null {
    const current = w.path('artifacts/' + safeId(ref.id) + '.json');
    if (fs.existsSync(current)) {
        const a = loadJson(current);
        if (objectDigest(a) === ref.digest && a.digest === ref.digest)
            return a;
    }
    const historical = w.path('.bugate/artifact-history/' + safeId(ref.id) + '/' + String(ref.digest).split(':')[1] + '.json');
    if (fs.existsSync(historical)) {
        const a = loadJson(historical);
        if (objectDigest(a) === ref.digest && a.digest === ref.digest)
            return a;
    }
    return null;
}
/** Only documented deterministic digest filling may differ from the raw model arguments. */
function outputMatches(raw: any, artifact: any): boolean {
    if (!raw)
        return false;
    const draft = structuredClone(raw);
    delete draft.digest;
    const final = structuredClone(artifact);
    delete final.digest;
    if (draft.artifact_type === 'inventory')
        for (const c of draft.content?.cases ?? []) {
            const f = final.content?.cases?.find(x => x.id === c.id);
            if (c.implementation?.digest == null && f?.implementation)
                c.implementation.digest = f.implementation.digest;
        }
    if (draft.artifact_type === 'semantic_review' && Object.values(draft.content?.artifact_digests ?? {}).some(h => h === null))
        return false;
    return eq(draft, final);
}
function settlementStatus(observation: any, event: any): string {
    const m = event?.data?.message;
    if (event?.type !== 'tool/result' || m?.toolCallId !== observation.execution.call_id)
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Final tool result is not bound to the observed call');
    const text = (m.content ?? []).filter(x => x.type === 'text').map(x => x.text).join('\n');
    if (m.isError || event.data?.error) {
        const raw = JSON.stringify(event.data).toLowerCase();
        return raw.includes('cancel') ? 'cancelled' : raw.includes('timeout') ? 'timeout' : 'failed';
    }
    try {
        const v = parseJson(text);
        validateSchema(v, 'CommandResult');
        if (v.invocation === 'cancelled')
            return 'cancelled';
        if (v.invocation === 'timeout')
            return 'timeout';
        if (v.invocation === 'completed' && !v.errors.length && v.operation === 'artifact.publish' && v.data?.kind === 'Artifact' && v.data.id === observation.subject_ref.id && v.data.digest === observation.subject_ref.digest && objectDigest(v.data) === v.data.digest)
            return 'completed';
    }
    catch { }
    return 'failed';
}
function settled(w: Workspace, r: any): boolean { return hostSettlements(w).some(s => s.digest === objectDigest(s) && s.workspace_id === w.id && s.task_id === w.task.id && eq(s.observation_ref, { id: r.id, digest: r.digest }) && s.session_id === r.execution.session_id && s.call_id === r.execution.call_id && s.result_event_digest === contentDigest(canonical(s.result_event)) && s.status === 'completed' && eq(receiptContext(w, s, 'artifact.settle').settlement, { session_id: s.session_id, call_id: s.call_id, result_event: s.result_event }) && settlementStatus(r, s.result_event) === 'completed'); }
export function settleArtifact(task: string, context: HostContext): any {
    if (context?.host !== 'deepseek-harness' || !context.settlement)
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Settlement requires a captured DSH final tool result');
    const w = new Workspace(task);
    verifyHostContext(w, 'artifact.settle', context);
    const s = context.settlement;
    const r = hostObservations(w).find(r => r.execution.session_id === s.session_id && r.execution.call_id === s.call_id);
    if (!r)
        return { settled: false, reason: 'no_published_output', session_id: s.session_id, call_id: s.call_id };
    if (r.digest !== objectDigest(r))
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Published output receipt is invalid');
    const status = settlementStatus(r, s.result_event);
    const v: any = { apiVersion: API_VERSION, kind: 'HostSettlement', id: 'settlement-' + r.id, task_id: w.task.id, workspace_id: w.id, observation_ref: { id: r.id, digest: r.digest }, session_id: s.session_id, call_id: s.call_id, status, result_event: s.result_event, result_event_digest: contentDigest(canonical(s.result_event)), authentication: context.authentication, host_context: observedContext(context) };
    v.digest = objectDigest(v);
    validateSchema(v, 'HostSettlement');
    return locked(w.root, () => {
        const rel = '.bugate/host-settlements/' + safeId(v.id) + '.json';
        if (fs.existsSync(w.path(rel))) {
            const old = validateSchema(w.read(rel), 'HostSettlement');
            const signed = receiptContext(w, old, 'artifact.settle');
            if (old.digest !== objectDigest(old) || !eq(signed.settlement, { session_id: old.session_id, call_id: old.call_id, result_event: old.result_event }))
                throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Existing settlement signature or event differs');
            const logical = x => Object.fromEntries(Object.entries(x).filter(([k]) => !['authentication', 'host_context', 'digest'].includes(k)));
            if (!eq(logical(old), logical(v)))
                throw new BugateError('CONCURRENT_CONFLICT', 'A different final result is already settled for this call');
            return old;
        }
        writeJson(w.root, rel, v, { immutable: true });
        return v;
    });
}
function rawValid(w: Workspace, r: any): boolean {
    try {
        validateSchema(r, 'HostObservation');
        const signed = receiptContext(w, r, 'artifact.publish');
        if (!eq(signed.execution, r.execution) || !eq(signed.messages ?? [], r.messages) || !outputMatches(signed.artifact, refArtifact(w, r.subject_ref)))
            return false;
        const e = r.execution, t = e.session_event.tool_call;
        if (r.digest !== objectDigest(r) || r.workspace_id !== w.id || r.task_id !== w.task.id || r.protocol_digest !== w.binding.protocol.digest || r.profile_digest !== w.binding.profile.digest || r.binding_digest !== objectDigest(w.binding) || r.evaluator_version !== VERSION || r.evaluator_digest !== evaluatorIdentity() || r.status !== 'published' || !settled(w, r))
            return false;
        if (e.arguments_digest !== contentDigest(canonical(e.arguments)) || e.session_event_digest !== contentDigest(canonical(e.session_event)) || !t || t.id !== e.call_id || t.name !== e.tool_name || !eq(t.arguments, e.arguments) || e.tool_name !== 'bugate_publish_artifact' || typeof t.arguments_json !== 'string' || !eq(parseJson(t.arguments_json), t.arguments))
            return false;
        const a = refArtifact(w, r.subject_ref);
        if (!a || !outputMatches(e.arguments.artifact, a))
            return false;
        if (r.messages.some(m => m.content_digest !== contentDigest(canonical(m.content))))
            return false;
        return true;
    }
    catch {
        return false;
    }
}
export function createHostObservation(w: Workspace, a: any, context: HostContext): any {
    if (context?.host !== 'deepseek-harness' || !context.execution)
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'A live DSH tool-call observation is required');
    verifyHostContext(w, 'artifact.publish', context);
    const e = context.execution, raw = e.arguments?.artifact;
    if (!raw || !eq(raw, context.artifact) || !outputMatches(raw, a))
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Only inline tool output establishes authorship; imported files cannot create host provenance');
    const refs = a.artifact_type === 'semantic_review' ? Object.entries(a.content.artifact_digests).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([id, digest]) => ({ id, digest })) : [], plans = w.objects('artifacts', 'Artifact').filter(a => a.artifact_type === 'inventory'), scope = activeScope(w, false);
    const r: any = { apiVersion: API_VERSION, kind: 'HostObservation', id: 'host-' + digest({ workspace: w.id, session: e.session_id, call: e.call_id }, 'host-call/v1').split(':')[1], task_id: w.task.id, workspace_id: w.id, protocol_digest: w.binding.protocol.digest, profile_digest: w.binding.profile.digest, binding_digest: objectDigest(w.binding), evaluator_version: VERSION, evaluator_digest: evaluatorIdentity(), host: context.host, host_version: context.host_version, operation: a.artifact_type === 'semantic_review' ? 'review' : 'artifact_author', execution: e, subject_ref: { id: a.id, digest: a.digest }, artifact_refs: refs, input_snapshot: reviewInputSnapshot(w, a), scope_ref: scopeRef(scope), plan_ref: plans.length === 1 ? { id: plans[0].id, digest: plans[0].digest } : null, status: 'published', messages: context.messages ?? [], authentication: context.authentication, host_context: observedContext(context) };
    r.digest = objectDigest(r);
    validateSchema(r, 'HostObservation');
    const t = e.session_event?.tool_call;
    if (!t || t.id !== e.call_id || t.name !== e.tool_name || e.tool_name !== 'bugate_publish_artifact' || !eq(t.arguments, e.arguments) || typeof t.arguments_json !== 'string' || !eq(parseJson(t.arguments_json), t.arguments) || e.arguments_digest !== contentDigest(canonical(e.arguments)) || e.session_event_digest !== contentDigest(canonical(e.session_event)) || r.messages.some(m => m.content_digest !== contentDigest(canonical(m.content))))
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Host event or message bytes do not match the captured tool invocation');
    if (a.artifact_type === 'semantic_review' && w.profile.team_review_policy) {
        const previous = hostObservations(w).filter(x => x.operation === 'review' && x.input_snapshot === r.input_snapshot && x.id !== r.id && eq(x.artifact_refs, r.artifact_refs) && refArtifact(w, x.subject_ref)?.content.perspective_id === a.content.perspective_id);
        if (previous.length >= w.profile.team_review_policy.max_rounds)
            throw new BugateError('REVIEW_ROUND_LIMIT', 'The Profile review bound is exhausted; preserve disagreement and report the gap');
    }
    return r;
}
export function publishHostObservation(w: Workspace, r: any) { writeJson(w.root, '.bugate/host-observations/' + safeId(r.id) + '.json', r, { immutable: true }); }
function eligible(w: Workspace, r: any, receipts: any[], policy: any, historical = false): boolean {
    if (!rawValid(w, r) || r.operation !== 'review' || r.execution.member_role !== 'teammate' || !['fresh', 'fork'].includes(r.execution.context_mode))
        return false;
    if (r.execution.context_mode === 'fresh' && r.execution.inherited_event_count !== 0)
        return false;
    if (policy?.context === 'fresh_required' && r.execution.context_mode !== 'fresh')
        return false;
    const a = refArtifact(w, r.subject_ref);
    if (!a || (!historical && (r.input_snapshot !== reviewInputSnapshot(w, a) || !eq(r.scope_ref, scopeRef(activeScope(w, false))))))
        return false;
    for (const ref of r.artifact_refs) {
        const authors = receipts.filter(x => x.operation === 'artifact_author' && eq(x.subject_ref, ref) && rawValid(w, x));
        if (!authors.length || authors.some(x => x.execution.agent_id === r.execution.agent_id || x.execution.session_id === r.execution.session_id) || authors.some(x => x.execution.team_id !== r.execution.team_id))
            return false;
    }
    return true;
}
function relatedTargets(a: any, b: any): boolean { return intersect(a.artifact_refs.map(x => x.id), b.artifact_refs.map(x => x.id)); }
/** Historical independent output can be discussed and disposed, but never supplies
 * current authorization. Stale peers require an exact finding and target relation. */
function validCrossCheck(w: Workspace, r: any, c: any, receipts: any[], policy: any): boolean {
    const peer = receipts.find(x => eq(x.subject_ref, c.review_ref) && eligible(w, x, receipts, policy, true));
    const message = r.messages.find(m => m.message_id === c.message_id);
    if (!peer || peer.execution.session_id === r.execution.session_id || peer.execution.team_id !== r.execution.team_id || !message || message.sender_session_id !== peer.execution.session_id || !eq(peer.scope_ref, r.scope_ref)) return false;
    if (!eligible(w, peer, receipts, policy)) {
        const opinion = refArtifact(w, peer.subject_ref), ids = c.finding_ids ?? [];
        if (!relatedTargets(peer, r) || !ids.length || !ids.every(id => opinion?.content.findings?.some(f => f.id === id))) return false;
    }
    const body = canonical(message.content).toString(); return body.includes(c.review_ref.id) && body.includes(c.review_ref.digest);
}
function evidenceRefsPresent(f: any, refs: string[]): boolean { return refs.length > 0 && refs.every(ref => Object.hasOwn(f.sources, ref) || Object.hasOwn(f.evidence, ref) || Object.hasOwn(f.artifacts, ref) || Object.hasOwn(f.oracles, ref)); }
export function validateReviewProvenance(f: any, a: any): boolean {
    const policy = f.w.profile.team_review_policy, required = !!policy || !!f.w.profile.quality_requirements?.independent_review;
    if (!required)
        return true;
    let good = true;
    const add = (code, msg, basis = []) => { f.add(code, a.id, msg, 'semantic_review', { basis }); good = false; };
    let receipts: any[] = [];
    try {
        receipts = hostObservations(f.w);
    }
    catch {
        add('REVIEW_PROVENANCE_MISSING', 'Host observation storage is invalid');
        return false;
    }
    const r = receipts.find(x => x.operation === 'review' && eq(x.subject_ref, { id: a.id, digest: a.digest }) && rawValid(f.w, x));
    if (!r) {
        add('REVIEW_PROVENANCE_MISSING', 'No durable published and successfully settled host review binds this artifact');
        return false;
    }
    if (r.input_snapshot !== reviewInputSnapshot(f.w, a) || !eq(r.scope_ref, scopeRef(f.scope)))
        add('REVIEW_BINDING_STALE', 'Review input snapshot or scope is no longer current');
    const plans = f.by_type.inventory ?? [], pr = plans.length === 1 ? { id: plans[0].id, digest: plans[0].digest } : null;
    if (!eq(r.plan_ref, pr) || !eq(r.artifact_refs, Object.entries(a.content.artifact_digests).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([id, digest]) => ({ id, digest }))))
        add('REVIEW_BINDING_STALE', 'Review plan or artifact references differ');
    if (r.execution.member_role !== 'teammate' || !['fresh', 'fork'].includes(r.execution.context_mode))
        add('REVIEW_INDEPENDENCE_MISSING', 'Review must be performed by an observed Team teammate');
    if (r.execution.context_mode === 'fresh' && r.execution.inherited_event_count !== 0)
        add('REVIEW_CONTEXT_INVALID', 'Fresh context cannot contain inherited events');
    if (policy?.context === 'fresh_required' && r.execution.context_mode !== 'fresh')
        add('REVIEW_CONTEXT_INVALID', 'Profile requires fresh context; a fork is not a blind review');
    for (const [aid, h] of Object.entries(a.content.artifact_digests)) {
        const authors = receipts.filter(x => x.operation === 'artifact_author' && eq(x.subject_ref, { id: aid, digest: h }) && rawValid(f.w, x));
        if (!authors.length) {
            add('REVIEW_AUTHOR_MISSING', 'Reviewed artifact has no captured and settled author provenance', [aid]);
            continue;
        }
        if (authors.some(x => x.execution.agent_id === r.execution.agent_id || x.execution.session_id === r.execution.session_id || x.execution.team_id !== r.execution.team_id))
            add('REVIEW_INDEPENDENCE_MISSING', 'Reviewer is an observed author/contributor or belongs to another Team', [aid]);
    }
    if (policy) {
        const p = policy.perspectives.find(p => p.id === a.content.perspective_id);
        if (!p || !subset(p.source_refs, Object.keys(f.sources)) || !subset(p.proposition_ids, Object.keys(f.props)))
            add('REVIEW_PERSPECTIVE_MISSING', 'Review perspective lacks its declared proposition or source basis');
        for (const item of a.content.findings ?? []) {
            if (!(Object.hasOwn(f.props, item.proposition_id)))
                add('REVIEW_PERSPECTIVE_MISSING', 'Review finding refers to unknown proposition', [item.id]);
            if ([...item.evidence_refs, ...item.counterevidence].some(ref => !(Object.hasOwn(f.sources, ref)) && !(Object.hasOwn(f.evidence, ref)) && !(Object.hasOwn(f.artifacts, ref)) && !(Object.hasOwn(f.oracles, ref))))
                add('REFERENCE_MISSING', 'Review finding evidence is absent', [item.id]);
        }
        if (policy.cross_check_required && !a.content.cross_checks?.length)
            add('REVIEW_CROSS_CHECK_MISSING', 'Profile requires an observed peer exchange and evidenced disposition');
        for (const c of a.content.cross_checks ?? []) {
            if (!validCrossCheck(f.w, r, c, receipts, policy))
                add('REVIEW_CROSS_CHECK_MISSING', 'Cross-check requires an eligible peer review and received message containing its exact reference', [c.review_ref.id]);
            if (c.disposition === 'unresolved')
                add('REVIEW_DISSENT_UNRESOLVED', 'A peer disagreement remains unresolved', [c.review_ref.id]);
            if (!evidenceRefsPresent(f, c.evidence_refs))
                add('REFERENCE_MISSING', 'Cross-check disposition requires existing evidence', [c.review_ref.id]);
        }
        const history = receipts.filter(x => x.operation === 'review' && relatedTargets(x, r) && eq(x.scope_ref, r.scope_ref) && eligible(f.w, x, receipts, policy, true));
        for (const old of history) {
            const original = refArtifact(f.w, old.subject_ref);
            for (const item of original?.content.findings ?? []) {
                if (!item.blocking || item.status !== 'open')
                    continue;
                const resolved = history.some(next => {
                    if (!eligible(f.w, next, receipts, policy, true))
                        return false;
                    const opinion = refArtifact(f.w, next.subject_ref);
                    if (next.execution.session_id === old.execution.session_id && next.execution.session_event.seq > old.execution.session_event.seq && opinion?.content.findings?.some(y => y.id === item.id && y.proposition_id === item.proposition_id && y.status === 'resolved' && evidenceRefsPresent(f, y.evidence_refs)))
                        return true;
                    return opinion?.content.cross_checks?.some(c => eq(c.review_ref, old.subject_ref) && c.finding_ids?.includes(item.id) && c.disposition !== 'unresolved' && evidenceRefsPresent(f, c.evidence_refs) && validCrossCheck(f.w, next, c, receipts, policy));
                });
                if (!resolved)
                    add('REVIEW_DISSENT_UNRESOLVED', 'Original blocking dissent has no exact evidenced disposition; agreement is not required', [old.subject_ref.id, item.id]);
            }
        }
    }
    return good;
}
function artifactPropositions(f: any, a: any): string[] {
    const c = a.content;
    switch (a.artifact_type) {
        case 'business_brief': return c.propositions.map(x => x.id);
        case 'testability': return c.decisions.map(x => x.proposition_id);
        case 'inventory': return c.cases.flatMap(x => x.proposition_ids);
        case 'domain_model': return c.invariants.flatMap(x => x.proposition_ids);
        case 'state_model': return c.transitions.flatMap(x => f.oracles[x.oracle_id]?.proposition_ids ?? []);
        case 'dimension_matrix': return c.dimensions.flatMap(x => x.proposition_ids);
        case 'adversarial_review': return c.findings.flatMap(x => x.proposition_ids);
        default: return [];
    }
}
export function requiredPerspectiveCoverage(f: any, artifact: any): boolean {
    const policy = f.w.profile.team_review_policy;
    if (!policy)
        return true;
    const applicable = policy.perspectives.filter(p => intersect(p.proposition_ids, artifactPropositions(f, artifact)));
    const reviews = (f.by_type.semantic_review ?? []).filter(r => f.validReviews.has(r.id) && r.content.artifact_digests[artifact.id] === artifact.digest);
    const missing = applicable.filter(p => !reviews.some(r => r.content.perspective_id === p.id));
    for (const p of missing)
        f.add('REVIEW_PERSPECTIVE_MISSING', artifact.id, 'A relevant Profile-required perspective has no valid current review', 'semantic_review', { basis: [p.id] });
    return missing.length === 0;
}
/** Separates observable local execution provenance from the content of a model opinion. */
export function derivedReviewAssurance(f: any): any[] {
    const required = !!f.w.profile.team_review_policy || !!f.w.profile.quality_requirements?.independent_review;
    let receipts: any[] = [], settlements: any[] = [];
    try {
        receipts = hostObservations(f.w);
        settlements = hostSettlements(f.w);
    }
    catch { }
    return (f.by_type.semantic_review ?? []).map(a => {
        const observation = receipts.find(r => eq(r.subject_ref, { id: a.id, digest: a.digest }) && rawValid(f.w, r));
        const settlement = observation ? settlements.find(s => eq(s.observation_ref, { id: observation.id, digest: observation.digest }) && s.status === 'completed') : null;
        const independent = !!observation && eligible(f.w, observation, receipts, f.w.profile.team_review_policy);
        return { review_id: a.id, review_digest: a.digest, observation_id: observation?.id ?? null, settlement_id: settlement?.id ?? null,
            execution_independence: { required, status: independent ? 'observed' : required ? 'not_proven' : 'not_required', context_mode: observation?.execution.context_mode ?? null, limitations: ['Local signed DSH observations establish observed execution and context separation only; they do not prove cognitive independence or resistance to same-permission public-key replacement.'] },
            semantic_opinion: { conclusion: a.content.conclusion, limitations: [...a.content.limitations, 'A source-backed model opinion is not proof that its business reasoning is correct.'] } };
    });
}
