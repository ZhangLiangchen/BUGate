import fs from 'node:fs';
import path from 'node:path';
import { API_VERSION, PROTOCOL_VERSION, BugateError, eq, subset, sameSet, difference, intersect, values } from './errors.js';
import { loadJson, objectDigest, validateSchema } from './protocol.js';
import { Workspace, within, locked, writeJson, safeId } from './workspace.js';
import { Facts } from './assessment.js';
export const COVERAGE_STATEMENT = 'This conclusion covers only the agreed acceptance scope.';
function boundDraft(w: Workspace, value: any, kind: string) {
    const expected: any = { apiVersion: API_VERSION, kind, task_id: w.task.id, workspace_id: w.id };
    if (kind === 'ScopeContract')
        expected.schema_version = PROTOCOL_VERSION;
    for (const [field, actual] of Object.entries(expected))
        if (Object.hasOwn(value, field) && !eq(value[field], actual))
            throw new BugateError(['task_id', 'workspace_id'].includes(field) ? 'WORKSPACE_MISMATCH' : 'PROTOCOL_MISMATCH', 'Explicit draft identity/version is unsupported; author a current task-bound ' + kind, 2, field);
}
export const scopeRef = (scope: any) => scope ? { id: scope.id, revision: scope.revision, digest: scope.digest } : null;
export const planRef = (facts: any) => facts.by_type.inventory?.length === 1 ? { id: facts.by_type.inventory[0].id, digest: facts.by_type.inventory[0].digest } : null;
export function activeScope(w: Workspace, required = true): any {
    if (!fs.existsSync(w.path('scope/active.json'))) {
        if (required)
            throw new BugateError('SCOPE_MISSING', 'Create a current ScopeContract with scope record before governed implementation or execution.');
        return null;
    }
    const scope = validateSchema(w.read('scope/active.json'), 'ScopeContract');
    if (scope.task_id !== w.task.id || scope.workspace_id !== w.id)
        throw new BugateError('WORKSPACE_MISMATCH', 'Scope belongs to another task');
    if (scope.digest !== objectDigest(scope))
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Scope content differs from its digest');
    validateRequests(w, scope);
    return scope;
}
export function recordRequest(task: string, inputFile: string): any {
    const w = new Workspace(task), value = loadJson(within(w.root, inputFile, true));
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new BugateError('STRUCTURE_INVALID', 'User request must be an object');
    boundDraft(w, value, 'ScopeRequest');
    Object.assign(value, { apiVersion: API_VERSION, kind: 'ScopeRequest', task_id: w.task.id, workspace_id: w.id });
    const actual = objectDigest(value);
    if (value.digest !== undefined && value.digest !== actual)
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'User request digest is invalid');
    value.digest = actual;
    validateSchema(value, 'ScopeRequest');
    locked(w.root, () => writeJson(w.root, '.bugate/scope-requests/' + safeId(value.id) + '.json', value, { immutable: true }));
    return value;
}
function request(w: Workspace, id: string) {
    const p = w.path('.bugate/scope-requests/' + safeId(id) + '.json');
    if (!fs.existsSync(p))
        throw new BugateError('SCOPE_AUTHORITY_MISSING', 'No direct user request receipt exists', 2, id);
    const value = validateSchema(loadJson(p), 'ScopeRequest');
    if (value.workspace_id !== w.id || value.task_id !== w.task.id)
        throw new BugateError('WORKSPACE_MISMATCH', 'User request belongs to another task');
    if (value.digest !== objectDigest(value))
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'User request bytes changed');
    return value;
}
function citations(s: any): any[] { return [...s.request_refs, ...s.change.request_refs, ...s.objectives.flatMap(x => x.request_refs), ...s.requirements.flatMap(x => x.basis.request_refs), ...s.scenarios.flatMap(x => x.necessity.request_refs)]; }
function validateRequests(w: Workspace, s: any) {
    const cited = new Set<string>();
    for (const c of citations(s)) {
        const v = request(w, c.request_id);
        cited.add(v.id);
        if (!v.message.includes(c.quote))
            throw new BugateError('SCOPE_AUTHORITY_INVALID', 'Scope citation is not verbatim text from its user event', 2, v.id);
        if (s.request_digests[v.id] !== v.digest)
            throw new BugateError('SCOPE_AUTHORITY_INVALID', 'Scope does not bind the exact direct user request', 2, v.id);
    }
    if (!sameSet(cited, Object.keys(s.request_digests)))
        throw new BugateError('SCOPE_AUTHORITY_INVALID', 'Scope request digest set differs from its citations');
}
export function recordScope(task: string, inputFile: string, options: {
    expectedDigest?: string;
} = {}): any {
    const w = new Workspace(task), value = loadJson(within(w.root, inputFile, true));
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new BugateError('STRUCTURE_INVALID', 'Scope draft must be an object');
    boundDraft(w, value, 'ScopeContract');
    Object.assign(value, { apiVersion: API_VERSION, kind: 'ScopeContract', task_id: w.task.id, workspace_id: w.id, schema_version: PROTOCOL_VERSION });
    try {
        value.request_digests = Object.fromEntries(citations(value).map(c => [c.request_id, request(w, c.request_id).digest]));
    }
    catch (e) {
        if (e instanceof BugateError)
            throw e;
        throw new BugateError('STRUCTURE_INVALID', 'Scope draft requires objectives, requirements, scenarios and user request citations');
    }
    const actual = objectDigest(value);
    if (value.digest !== undefined && value.digest !== actual)
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Scope digest is invalid');
    value.digest = actual;
    validateSchema(value, 'ScopeContract');
    validateRequests(w, value);
    const before = w.snapshot();
    locked(w.root, () => {
        if (before !== w.snapshot())
            throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed during scope authoring');
        const old = activeScope(w, false);
        if (eq(old, value))
            return;
        const denominators = sameSet(value.requirements.map(x => x.id), w.profile.requirements) && sameSet(value.risk_ids, w.profile.risks);
        if (old) {
            if (!denominators)
                throw new BugateError('SCOPE_PROFILE_CHANGE_REQUIRED', 'The locked Profile fixes requirement/risk denominators. Author an explicitly sourced new Profile version and initialize a new task; preserve this task and its failures.', 2, { scope_ref: scopeRef(old), current_profile: w.binding.profile, usage: 'bugate init --project ' + w.project + ' --task <new-task-id> --sut-profile <new-profile.json> --json' });
            if (options.expectedDigest !== old.digest)
                throw new BugateError('CONCURRENT_CONFLICT', 'Scope change requires the active expected digest', 2, scopeRef(old));
            if (value.id !== old.id || value.revision !== old.revision + 1 || value.change.previous_digest !== old.digest)
                throw new BugateError('SCOPE_CHANGE_INVALID', 'Scope change must preserve id, increment revision and reference its predecessor');
            if (!difference(value.change.request_refs.map(c => c.request_id), Object.keys(old.request_digests)).length)
                throw new BugateError('SCOPE_CHANGE_UNAUTHORIZED', 'Changing agreed scope requires a new direct user request; failure or time pressure is not authority');
            writeJson(w.root, '.bugate/scope-history/' + old.digest.split(':')[1] + '.json', old, { immutable: true });
        }
        else {
            if (options.expectedDigest !== undefined || value.revision !== 1 || value.change.previous_digest !== null)
                throw new BugateError('SCOPE_CHANGE_INVALID', 'Initial scope must have revision 1 and no predecessor');
            if (!denominators)
                throw new BugateError('SCOPE_DENOMINATOR_CHANGED', 'Initial scope must preserve all Profile requirements and risks; later changes require direct user authority');
        }
        writeJson(w.root, 'scope/active.json', value);
    });
    return value;
}
export function recordSuggestion(task: string, inputFile?: string, provided?: any): any {
    const w = new Workspace(task), scope = activeScope(w), value = structuredClone(provided ?? loadJson(within(w.root, inputFile!, true)));
    if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new BugateError('STRUCTURE_INVALID', 'Suggestion must be an object');
    boundDraft(w, value, 'Suggestion');
    if (Object.hasOwn(value, 'scope_ref') && !eq(value.scope_ref, scopeRef(scope)))
        throw new BugateError('SCOPE_STALE', 'Suggestion draft explicitly refers to a different scope');
    Object.assign(value, { apiVersion: API_VERSION, kind: 'Suggestion', task_id: w.task.id, workspace_id: w.id, scope_ref: scopeRef(scope), status: 'suggested' });
    const actual = objectDigest(value);
    if (value.digest !== undefined && value.digest !== actual)
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Suggestion digest is invalid');
    value.digest = actual;
    validateSchema(value, 'Suggestion');
    if (!subset(value.source_refs, w.profile.sources.map(s => s.id)))
        throw new BugateError('REFERENCE_MISSING', 'Suggestion cites undeclared sources');
    locked(w.root, () => writeJson(w.root, 'suggestions/' + safeId(value.id) + '.json', value, { immutable: true }));
    return value;
}
export function suggestions(w: Workspace): any[] {
    const dir = w.path('suggestions');
    if (!fs.existsSync(dir))
        return [];
    return fs.readdirSync(dir).filter(x => x.endsWith('.json')).sort().map(file => {
        try {
            const v = validateSchema(loadJson(within(w.root, path.join(dir, file), true)), 'Suggestion');
            if (v.task_id !== w.task.id || v.workspace_id !== w.id || v.digest !== objectDigest(v))
                throw new BugateError('SUGGESTION_INVALID', 'Suggestion identity or digest is invalid');
            return v;
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
            return { id: path.basename(file, '.json'), status: 'invalid_advisory', code: e.code };
        }
    });
}
export function checkScope(f: any, { plan = true, review = true } = {}): void {
    const s = f.scope;
    if (!s) {
        f.add('SCOPE_MISSING', 'scope', 'A ScopeContract is required for this claim', 'scope');
        return;
    }
    function index(items: any[], label: string): any {
        const result: any = Object.create(null);
        for (const item of items) {
            if (Object.hasOwn(result, item.id))
                f.add('DUPLICATE_ID', item.id, 'Duplicate scope ' + label, 'scope');
            result[item.id] = item;
        }
        return result;
    }
    const goals = index(s.objectives, 'objective'), requirements = index(s.requirements, 'requirement'), flows = index(s.flows, 'flow'), scenarios = index(s.scenarios, 'scenario'), sourceIds = Object.keys(f.sources);
    if (!sameSet(Object.keys(requirements), f.w.profile.requirements) || !sameSet(s.risk_ids, f.w.profile.risks))
        f.add('SCOPE_PROFILE_CHANGE_REQUIRED', s.id, 'Scope cannot alter the locked Profile denominator; use a new explicit Profile version and task', 'scope');
    if (!subset(s.source_refs, sourceIds))
        f.add('REFERENCE_MISSING', s.id, 'Scope source is not declared by Profile', 'scope');
    for (const a of s.assumptions)
        if (a.blocking)
            f.add('SCOPE_AMBIGUITY', a.id, 'Unconfirmed assumption affects acceptance direction; ask the smallest clarification', 'scope', { basis: [a.statement] });
    for (const e of s.exclusions) {
        const overlap = e.requirement_ids.filter(r => Object.hasOwn(requirements, r));
        if (overlap.length)
            f.add('SCOPE_EXCLUSION_CONFLICT', e.id, 'An exclusion conflicts with an in-scope requirement', 'scope', { basis: overlap.sort() });
    }
    for (const flow of values(flows)) {
        if (!subset(flow.objective_ids, Object.keys(goals)) || (flow.branch_of !== null && (!(Object.hasOwn(flows, flow.branch_of)) || flow.branch_of === flow.id)))
            f.add('SCOPE_REFERENCE_INVALID', flow.id, 'Flow has no matching scope objective or parent branch', 'scope');
        let node = flow;
        const seen = new Set();
        while (node && node.branch_of !== null) {
            if (seen.has(node.id)) {
                f.add('SCOPE_REFERENCE_INVALID', flow.id, 'Flow branch ancestry contains a cycle', 'scope');
                break;
            }
            seen.add(node.id);
            node = flows[node.branch_of];
        }
    }
    for (const r of values(requirements)) {
        if (!subset(r.objective_ids, Object.keys(goals)))
            f.add('SCOPE_REFERENCE_INVALID', r.id, 'Requirement does not prove a declared objective', 'scope');
        if (!subset(r.oracle_ids, Object.keys(f.oracles)))
            f.add('ORACLE_MISSING', r.id, 'Scope requirement lacks its referenced business Oracle', 'scope');
    }
    for (const scenario of values(scenarios)) {
        const flow = flows[scenario.flow_id];
        if (!subset(scenario.requirement_ids, Object.keys(requirements)) || !flow) {
            f.add('SCENARIO_UNRELATED', scenario.id, 'Required scenario must prove declared requirements and a flow', 'scope');
            continue;
        }
        const allowed = scenario.requirement_ids.flatMap(r => requirements[r].oracle_ids);
        if (!subset(scenario.oracle_ids, allowed))
            f.add('SCOPE_REFERENCE_INVALID', scenario.id, 'Scenario Oracle is not an Oracle of its requirements', 'scope');
        if (!subset(scenario.requirement_ids.flatMap(r => requirements[r].objective_ids), flow.objective_ids))
            f.add('SCENARIO_UNRELATED', scenario.id, 'Scenario flow does not serve its requirement objectives', 'scope');
    }
    for (const rid of difference(Object.keys(requirements), values(scenarios).flatMap(c => c.requirement_ids)))
        f.add('SCOPE_SCENARIO_MISSING', rid, 'In-scope requirement has no necessary scenario', 'scope');
    for (const goal of Object.keys(goals))
        if (!values(requirements).some(r => r.objective_ids.includes(goal)))
            f.add('SCOPE_SCENARIO_MISSING', goal, 'Objective has no acceptance requirement', 'scope');
    for (const a of s.auxiliary)
        if (!subset(a.related_scenario_ids, Object.keys(scenarios)) || (a.purpose === 'diagnosis' && !a.related_scenario_ids.length))
            f.add('DIAGNOSTIC_BOUNDARY_INVALID', a.id, 'Diagnostic policy must name current scenarios and a finite run bound', 'scope');
    if (plan) {
        const plans = f.by_type.inventory ?? [];
        if (plans.length !== 1)
            f.add('PLAN_AMBIGUOUS', 'inventory', 'Exactly one current inventory is the acceptance plan', 'scope');
        const bound: any = Object.create(null);
        for (const p of plans) {
            if (!eq(p.content.scope_ref, scopeRef(s)))
                f.add('SCOPE_STALE', p.id, 'Inventory belongs to another scope revision', 'scope');
            for (const c of p.content.cases) {
                const scenario = scenarios[c.scenario_id];
                if (!scenario) {
                    f.add('SCENARIO_OUT_OF_SCOPE', c.id, 'Record this extension as a suggestion; it cannot enter the required plan', 'scope', { remediation: 'Remove the unaccepted extension from the executable inventory and record a non-executable suggestion.' });
                    continue;
                }
                if (Object.hasOwn(bound, c.scenario_id))
                    f.add('SCENARIO_REDUNDANT', c.id, 'A required scenario already has a case; add another only after scope necessity review', 'scope', { basis: [bound[c.scenario_id]] });
                bound[c.scenario_id] = c.id;
                if (!sameSet(c.oracle_ids, scenario.oracle_ids))
                    f.add('SCOPE_ORACLE_WEAKENED', c.id, 'Case must retain every agreed business Oracle', 'scope');
                const traced = c.proposition_ids.filter(p => Object.hasOwn(f.props, p)).flatMap(p => f.props[p].requirement_ids);
                if (!subset(scenario.requirement_ids, traced))
                    f.add('SCENARIO_UNRELATED', c.id, 'Case propositions do not prove its scope requirements', 'scope');
                if (!sameSet(c.expected_observations, scenario.expected_evidence))
                    f.add('SCOPE_ORACLE_WEAKENED', c.id, 'Case expected evidence differs from the agreed scenario', 'scope');
            }
        }
        for (const sid of difference(Object.keys(scenarios), Object.keys(bound)))
            f.add('SCOPE_SCENARIO_MISSING', sid, 'Required scope scenario is absent from current inventory', 'scope');
    }
    if (review) {
        let valid = false;
        for (const a of f.by_type.semantic_review ?? []) {
            const c = a.content;
            if (c.scope_digest !== s.digest || c.conclusion !== 'accepted')
                continue;
            if (Object.entries(c.artifact_digests).some(([aid, h]) => !f.artifacts[aid] || f.artifacts[aid].digest !== h))
                continue;
            const decisions = Object.fromEntries(c.scope_decisions.map(d => [d.scenario_id, d]));
            if (!sameSet(Object.keys(decisions), Object.keys(scenarios)) || values(decisions).some(d => d.necessity !== 'accepted' || !subset(d.source_refs, sourceIds)))
                continue;
            if (s.revision > 1 && c.change_authorization !== 'accepted')
                continue;
            const required = (plan ? ['business_brief', 'testability', 'inventory'] : ['business_brief']).flatMap(k => (f.by_type[k] ?? []).map(a => a.id));
            if (!subset(required, Object.keys(c.artifact_digests)) || !subset(c.source_refs, sourceIds))
                continue;
            if (!f.reviewIsValid(a))
                continue;
            valid = true;
        }
        if (!valid)
            f.add('SCOPE_SEMANTIC_REVIEW_MISSING', s.id, 'Current source-backed review must establish necessity and user authorization; populated fields are not sufficient', 'semantic_review', { remediation: 'Review the exact user requests, scope changes, requirement/Oracle mapping and each scenario necessity against sources. Record uncertainty instead of claiming user consent.' });
    }
}
export function checkScopeTask(task: string): any {
    const w = new Workspace(task), f = new Facts(w);
    for (const issue of f.findings)
        if (Object.hasOwn(f.evidence, issue.subject)) {
            issue.blocking = false;
            issue.severity = 'warning';
        }
    checkScope(f);
    return { valid: !f.findings.some(x => x.blocking), scope_ref: scopeRef(f.scope), plan_ref: planRef(f), findings: f.findings, suggestions: suggestions(w) };
}
export interface AuthorizationOptions {
    caseIds?: string[];
    activityId?: string;
    environmentId?: string;
    expectedScopeDigest?: string;
    expectedPlanDigest?: string;
}
export function authorize(task: string, { caseIds = [], activityId = null, environmentId = null, expectedScopeDigest = null, expectedPlanDigest = null }: AuthorizationOptions = {}): any {
    const w = new Workspace(task), f = new Facts(w), s = f.scope, initial = [...f.findings];
    if (caseIds.length)
        checkScope(f);
    else if (!s)
        f.add('SCOPE_MISSING', 'scope', 'Auxiliary execution requires a scope activity declaration', 'scope');
    const sr = scopeRef(s), pr = planRef(f), environments = s?.subject.environment_ids ?? [];
    if (environmentId === null && environments.length === 1)
        environmentId = environments[0];
    if (!environments.includes(environmentId))
        f.add('SCOPE_ENVIRONMENT_INVALID', environmentId ?? 'environment', 'Select an environment explicitly permitted by the current scope', 'scope');
    if (expectedScopeDigest && sr?.digest !== expectedScopeDigest)
        f.add('SCOPE_STALE', 'execution', 'Execution expected another scope digest', 'scope');
    if (expectedPlanDigest && pr?.digest !== expectedPlanDigest)
        f.add('PLAN_STALE', 'execution', 'Execution expected another plan digest', 'scope');
    if (caseIds.length) {
        if (activityId)
            f.add('ACTIVITY_CLASSIFICATION_INVALID', activityId, 'An execution cannot be both required and auxiliary', 'scope');
        for (const cid of caseIds)
            if (!(Object.hasOwn(f.cases, cid)))
                f.add('SCENARIO_OUT_OF_SCOPE', cid, 'Only current inventory cases can execute as required acceptance', 'scope');
        f.business();
        f.design();
        f.implementation();
    }
    else {
        const activity = s?.auxiliary.find(a => a.id === activityId);
        if (!activity)
            f.add('AUXILIARY_NOT_DECLARED', activityId ?? 'execution', 'Select a declared auxiliary activity; suggestions cannot execute', 'scope');
        else {
            const ledger = w.path('.bugate/activity-runs/' + s.digest.split(':')[1] + '/' + safeId(activityId));
            const runs = fs.existsSync(ledger) ? fs.readdirSync(ledger).filter(x => x.endsWith('.json')) : [];
            if (runs.length >= activity.max_runs)
                f.add('DIAGNOSTIC_LIMIT_REACHED', activityId, 'Activity reached its agreed bound; preserve findings or report the blocking gap', 'scope');
            if (activity.purpose === 'diagnosis') {
                const ids = values(f.cases).filter(c => activity.related_scenario_ids.includes(c.scenario_id)).map(c => c.id);
                const failures = values(f.evidence).filter(e => e.execution && e.source_type === 'host_observed' && eq(e.execution.scope_ref, sr) && eq(e.execution.plan_ref, pr) && !f.findings.some(i => i.subject === e.id && i.blocking) && e.execution.cases.some(c => ids.includes(c.id) && ['failed', 'error'].includes(c.outcome)));
                if (!failures.length)
                    f.add('DIAGNOSTIC_FAILURE_MISSING', activityId, 'Diagnosis must relate to a recorded failure of a current required scenario', 'scope');
            }
        }
    }
    if (!caseIds.length)
        for (const issue of initial)
            if (issue.subject !== 'scope') {
                issue.blocking = false;
                issue.severity = 'warning';
            }
    for (const issue of f.findings)
        if (Object.hasOwn(f.evidence, issue.subject)) {
            issue.blocking = false;
            issue.severity = 'warning';
        }
    return { allowed: !f.findings.some(x => x.blocking), scope_ref: sr, plan_ref: pr, activity_id: activityId, environment_id: environmentId, findings: f.findings };
}
export function showScope(task: string): any { const w = new Workspace(task); return { scope: activeScope(w, false), requests: w.objects('.bugate/scope-requests', 'ScopeRequest'), suggestions: suggestions(w) }; }
