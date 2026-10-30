import fs from 'node:fs';
import path from 'node:path';
import { API_VERSION, VERSION, BugateError, eq, subset, sameSet, difference, intersect, values } from './errors.js';
import { digest, objectDigest, contentDigest, loadJson, validateSchema, evaluatorIdentity, canonical, qualityRules } from './protocol.js';
import { Workspace, within, writeJson, locked, safeId, executionInputManifest, filesystemManifest } from './workspace.js';
import { activeScope, scopeRef, planRef, checkScope, suggestions, COVERAGE_STATEMENT } from './scope.js';
import { checkSourcesAndPlaceholders, qualityModels } from './semantics.js';
import { validateReviewProvenance, requiredPerspectiveCoverage, derivedReviewAssurance } from './team.js';
export function finding(code: string, subject: any, message: string, dimension = 'integrity', options: any = {}): any { return { code, subject: String(subject), dimension, severity: options.blocking === false ? 'warning' : 'error', blocking: options.blocking !== false, message, basis: options.basis ?? [], remediation: options.remediation ?? 'Correct the referenced input and reassess the current snapshot.' }; }
export class Facts {
    w: Workspace;
    findings: any[] = [];
    artifacts: any = Object.create(null);
    evidence: any = Object.create(null);
    scope: any = null;
    requirements: string[];
    risks: string[];
    by_type: any = Object.create(null);
    sources: any = Object.create(null);
    props: any = Object.create(null);
    oracles: any = Object.create(null);
    cases: any = Object.create(null);
    advisory: any[] = [];
    referenceObservations: Record<string, any> = Object.create(null);
    collected = false;
    reviewed = new Set<string>();
    validReviews = new Set<string>();
    constructor(w: Workspace) {
        this.w = w;
        this.advisory = suggestions(w);
        try {
            this.scope = activeScope(w, false);
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
            this.add(e.code, 'scope', e.message, 'scope');
        }
        this.requirements = w.profile.requirements;
        this.risks = w.profile.risks;
        for (const [folder, kind, container] of [['artifacts', 'Artifact', this.artifacts], ['evidence', 'Evidence', this.evidence]]) {
            try {
                for (const o of w.objects(folder, kind)) {
                    container[o.id] = o;
                    if (o.digest !== objectDigest(o))
                        this.add('OBJECT_DIGEST_MISMATCH', o.id, 'Object content differs from its registered digest');
                }
            }
            catch (e) {
                if (!(e instanceof BugateError))
                    throw e;
                this.add(e.code, folder, e.message, 'integrity', { basis: [String(e.details)] });
            }
        }
        for (const a of values(this.artifacts))
            (this.by_type[a.artifact_type] ??= []).push(a);
        this.sources = Object.assign(Object.create(null), Object.fromEntries(w.profile.sources.map(s => [s.id, s])));
        for (const s of values(this.sources))
            this.checkRef(s, w.project, 'source:' + s.id);
        for (const a of values(this.artifacts))
            for (const ref of a.source_refs)
                if (!(Object.hasOwn(this.sources, ref)))
                    this.add('REFERENCE_MISSING', a.id, 'Artifact source is not declared by Profile', 'integrity', { basis: [ref] });
        for (const e of values(this.evidence)) {
            if (e.task_id !== w.task.id || e.workspace_id !== w.id)
                this.add('WORKSPACE_MISMATCH', e.id, 'Evidence belongs to a different task or workspace');
            for (const ref of e.references)
                this.checkRef(ref, w.root, e.id);
            const ex = e.execution;
            if (ex) {
                if (this.scope && !eq(ex.scope_ref, scopeRef(this.scope)))
                    this.add('SCOPE_STALE', e.id, 'Evidence belongs to another scope revision', 'execution');
                if (!eq(ex.input_manifest, executionInputManifest(w)))
                    this.add('EXECUTION_INPUTS_CHANGED', e.id, 'Execution inputs or their path set changed; rerun against current design and source bytes', 'execution');
                for (const ref of [ex.stdout, ex.stderr, ...ex.report ? [ex.report] : []])
                    this.checkRef(ref, w.root, e.id);
                for (const [name, h] of Object.entries(ex.implementation_digests))
                    this.checkRef({ path: name, digest: h }, w.root, e.id);
            }
            if (e.source_type === 'host_observed') {
                const receipt = w.path('.bugate/observations/' + safeId(e.id) + '.json');
                if (!fs.existsSync(receipt) || !eq(loadJson(receipt), { evidence_digest: e.digest, workspace_id: w.id, collector: 'bugate.execution/v1' }))
                    this.add('EVIDENCE_UNTRUSTED', e.id, 'No matching local collector receipt; source labels do not authenticate execution');
                if (!ex || ex.collector !== 'bugate.execution/v1')
                    this.add('EVIDENCE_UNTRUSTED', e.id, 'Host observation requires a managed execution record');
            }
        }
        this.indexes();
        checkSourcesAndPlaceholders(this);
        const identities = new Set([...Object.keys(this.sources), ...Object.keys(this.artifacts), ...Object.keys(this.evidence), ...Object.keys(this.props), ...Object.keys(this.oracles), ...Object.keys(this.cases)]);
        for (const a of values(this.artifacts))
            for (const ref of a.relations)
                if (!identities.has(ref))
                    this.add('REFERENCE_MISSING', a.id, 'Artifact relation points to an absent identity', 'integrity', { basis: [ref] });
        for (const a of this.by_type.diagnosis ?? [])
            for (const h of a.content.hypotheses) {
                for (const ref of [...h.supporting_evidence, ...h.contradicting_evidence])
                    if (!(Object.hasOwn(this.evidence, ref)))
                        this.add('REFERENCE_MISSING', a.id, 'Diagnosis evidence is absent', 'integrity', { basis: [ref] });
                if (h.status === 'confirmed' && !h.supporting_evidence.length)
                    this.add('EVIDENCE_UNTRUSTED', a.id, 'A confirmed diagnosis requires supporting evidence');
            }
    }
    add(code: string, subject: any, message: string, dimension = 'integrity', options: any = {}) { this.findings.push(finding(code, subject, message, dimension, options)); }
    observeRef(ref: any, root: string): any {
        const key = canonical({ path: ref.path, root }).toString();
        if (Object.hasOwn(this.referenceObservations, key))
            return this.referenceObservations[key];
        if (this.collected)
            throw new BugateError('REFERENCE_MISSING', 'Collected evaluation input has no reference observation', 2, { path: ref.path });
        let observation: any;
        try {
            observation = { digest: contentDigest(fs.readFileSync(within(root, ref.path, true))) };
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
            observation = { error: { code: e.code, message: e.message } };
        }
        this.referenceObservations[key] = observation;
        return observation;
    }
    checkRef(ref: any, root: string, subject: string, dimension = 'integrity') {
        const observation = this.observeRef(ref, root);
        if (observation.error)
            this.add(observation.error.code, subject, observation.error.message, dimension, { basis: [ref.path] });
        else if (observation.digest !== ref.digest)
            this.add('REFERENCE_DIGEST_MISMATCH', subject, 'Referenced bytes changed', dimension, { basis: [ref.path] });
    }
    unique(index: any, items: any[], subject: string) {
        for (const item of items) {
            if (Object.hasOwn(index, item.id))
                this.add('DUPLICATE_ID', item.id, 'Duplicate semantic identity', 'integrity', { basis: [subject] });
            index[item.id] = item;
        }
    }
    indexes() {
        for (const a of this.by_type.business_brief ?? []) {
            this.unique(this.props, a.content.propositions, a.id);
            this.unique(this.oracles, a.content.oracles, a.id);
        }
        for (const a of this.by_type.inventory ?? [])
            this.unique(this.cases, a.content.cases, a.id);
        if (this.w.profile.team_review_policy) {
            const seen = new Set();
            for (const p of this.w.profile.team_review_policy.perspectives) {
                if (seen.has(p.id))
                    this.add('DUPLICATE_ID', p.id, 'Duplicate Profile review perspective id', 'semantic_review');
                seen.add(p.id);
                if (!subset(p.proposition_ids, Object.keys(this.props)) || !subset(p.source_refs, Object.keys(this.sources)))
                    this.add('REVIEW_PERSPECTIVE_MISSING', p.id, 'Profile perspective requires a source-backed proposition that is absent', 'semantic_review');
            }
        }
        for (const a of this.by_type.semantic_review ?? []) {
            const c = a.content;
            let valid = c.conclusion === 'accepted';
            for (const src of c.source_refs)
                if (!(Object.hasOwn(this.sources, src))) {
                    this.add('REFERENCE_MISSING', a.id, 'Reviewer source is undeclared', 'semantic_review', { basis: [src] });
                    valid = false;
                }
            for (const [aid, expected] of Object.entries(c.artifact_digests))
                if (!(Object.hasOwn(this.artifacts, aid)) || this.artifacts[aid].digest !== expected) {
                    this.add('SEMANTIC_REVIEW_STALE', a.id, 'Review does not bind current artifact content', 'semantic_review', { basis: [aid] });
                    valid = false;
                }
            if (!validateReviewProvenance(this, a))
                valid = false;
            if (valid) {
                this.validReviews.add(a.id);
                for (const aid of Object.keys(c.artifact_digests))
                    this.reviewed.add(aid);
            }
        }
    }
    reviewIsValid(a: any) { return this.validReviews.has(a.id); }
    requireReview(types: string[]) {
        for (const t of types)
            for (const a of this.by_type[t] ?? [])
                if (!this.reviewed.has(a.id) || !requiredPerspectiveCoverage(this, a))
                    this.add('SEMANTIC_REVIEW_MISSING', a.id, 'Current source-backed semantic review is required', 'semantic_review', { remediation: 'Provide a reviewer artifact bound to current artifact digests; structural validity is not business correctness.' });
    }
    business() {
        if (!Object.keys(this.props).length)
            this.add('BUSINESS_UNDERSTANDING_MISSING', 'business_brief', 'No source-backed business propositions', 'business_understanding');
        const covered = new Set<string>();
        for (const p of values(this.props)) {
            for (const r of p.requirement_ids)
                covered.add(r);
            if (p.classification !== 'fact')
                this.add('BUSINESS_SEMANTICS_UNRESOLVED', p.id, 'Proposition remains inference or unknown', 'business_understanding');
            for (const rid of p.requirement_ids)
                if (!this.requirements.includes(rid))
                    this.add('REFERENCE_MISSING', p.id, 'Requirement is outside frozen task scope', 'business_understanding', { basis: [rid] });
            for (const src of p.source_refs)
                if (!(Object.hasOwn(this.sources, src)))
                    this.add('REFERENCE_MISSING', p.id, 'Proposition source is undeclared', 'business_understanding', { basis: [src] });
        }
        for (const rid of difference(this.requirements, covered))
            this.add('REQUIREMENT_UNCOVERED', rid, 'Frozen task requirement has no proposition', 'business_understanding');
        for (const o of values(this.oracles)) {
            for (const pid of o.proposition_ids)
                if (!(Object.hasOwn(this.props, pid)))
                    this.add('REFERENCE_MISSING', o.id, 'Oracle references missing proposition', 'business_understanding', { basis: [pid] });
            for (const src of o.source_refs)
                if (!(Object.hasOwn(this.sources, src)))
                    this.add('REFERENCE_MISSING', o.id, 'Oracle source is undeclared', 'business_understanding', { basis: [src] });
        }
        for (const pid of Object.keys(this.props))
            if (!values(this.oracles).some(o => o.proposition_ids.includes(pid)))
                this.add('ORACLE_MISSING', pid, 'Proposition has no observable business oracle', 'business_understanding', { remediation: 'Define the expected business result, observation, deadline, prerequisites and source.' });
        for (const a of this.by_type.business_brief ?? [])
            if (a.content.unknowns.length)
                this.add('BUSINESS_SEMANTICS_UNRESOLVED', a.id, 'Business brief contains unresolved unknowns', 'business_understanding', { basis: a.content.unknowns });
        if (this.w.task.unknowns.length || this.w.profile.unknowns.length)
            this.add('BUSINESS_SEMANTICS_UNRESOLVED', 'task', 'Task/Profile has unresolved unknowns', 'business_understanding');
        this.requireReview(['business_brief']);
        qualityModels(this, 'business_understanding');
    }
    design() {
        checkScope(this);
        const decisions: any = Object.create(null), mapped = new Set<string>(), pairs: any = Object.create(null);
        for (const a of this.by_type.testability ?? []) {
            if (a.content.gaps.length)
                this.add('TESTABILITY_GAP', a.id, 'Unresolved observability or prerequisite gaps', 'testability', { basis: a.content.gaps });
            for (const d of a.content.decisions) {
                const pid = d.proposition_id, key = JSON.stringify([pid, d.oracle_id]);
                if (Object.hasOwn(pairs, key))
                    this.add('DUPLICATE_ID', pid, 'Duplicate proposition/oracle testability decision', 'testability');
                pairs[key] = d.layer;
                decisions[pid] = true;
                mapped.add(d.oracle_id);
                if (!(Object.hasOwn(this.props, pid)) || !(Object.hasOwn(this.oracles, d.oracle_id)))
                    this.add('REFERENCE_MISSING', pid, 'Testability proposition or oracle missing', 'testability');
                else if (!this.oracles[d.oracle_id].proposition_ids.includes(pid))
                    this.add('ORACLE_BINDING_INVALID', pid, 'Testability oracle belongs to a different proposition', 'testability', { basis: [d.oracle_id] });
                if (!['unit', 'component', 'integration', 'api', 'e2e', 'static', 'probe'].includes(d.layer))
                    this.add('TESTABILITY_GAP', pid, 'Unknown test layer is not an evaluated strategy', 'testability');
            }
        }
        for (const pid of Object.keys(this.props))
            if (!(Object.hasOwn(decisions, pid)))
                this.add('TESTABILITY_GAP', pid, 'No explicit observation/layer decision', 'testability');
        for (const oid of difference(Object.keys(this.oracles), mapped))
            this.add('TESTABILITY_ORACLE_UNMAPPED', oid, 'Business oracle has no explicit testability decision', 'testability');
        if (!Object.keys(this.cases).length)
            this.add('CASE_MISSING', 'inventory', 'No concrete test cases', 'test_design');
        const coveredP = new Set<string>(), coveredR = new Set<string>(), coveredO = new Set<string>();
        for (const c of values(this.cases)) {
            for (const p of c.proposition_ids)
                coveredP.add(p);
            for (const r of c.risk_ids)
                coveredR.add(r);
            for (const o of c.oracle_ids)
                coveredO.add(o);
            for (const pid of c.proposition_ids) {
                for (const oid of c.oracle_ids)
                    if (this.oracles[oid]?.proposition_ids.includes(pid) && pairs[JSON.stringify([pid, oid])] !== c.layer)
                        this.add('LAYER_DECISION_MISMATCH', c.id, 'Case test layer differs from its proposition/oracle testability decision', 'test_design', { basis: [pid, oid] });
                if (!(Object.hasOwn(this.props, pid)))
                    this.add('REFERENCE_MISSING', c.id, 'Case proposition is missing', 'test_design', { basis: [pid] });
            }
            for (const oid of c.oracle_ids) {
                if (!(Object.hasOwn(this.oracles, oid)))
                    this.add('ORACLE_MISSING', c.id, 'Case oracle is missing', 'test_design', { basis: [oid] });
                else if (!intersect(this.oracles[oid].proposition_ids, c.proposition_ids))
                    this.add('ORACLE_BINDING_INVALID', c.id, 'Case oracle does not cover its proposition', 'test_design', { basis: [oid] });
            }
            const oracleProps = c.oracle_ids.filter(o => Object.hasOwn(this.oracles, o)).flatMap(o => this.oracles[o].proposition_ids);
            for (const pid of difference(c.proposition_ids, oracleProps))
                this.add('ORACLE_BINDING_INVALID', c.id, 'Case proposition is not covered by any referenced oracle', 'test_design', { basis: [pid] });
            for (const rid of c.risk_ids)
                if (!this.risks.includes(rid))
                    this.add('REFERENCE_MISSING', c.id, 'Case risk is outside frozen scope', 'test_design', { basis: [rid] });
        }
        for (const p of difference(Object.keys(this.props), coveredP))
            this.add('PROPOSITION_UNCOVERED', p, 'Proposition has no case', 'test_design');
        for (const o of difference(Object.keys(this.oracles), coveredO))
            this.add('ORACLE_UNCOVERED', o, 'Oracle has no case', 'test_design');
        for (const r of difference(this.risks, coveredR))
            this.add('RISK_UNCOVERED', r, 'Frozen risk has no case', 'test_design');
        for (const a of this.by_type.inventory ?? [])
            if (!sameSet(a.content.requirement_scope, this.requirements) || !sameSet(a.content.risk_scope, this.risks))
                this.add('SCOPE_DENOMINATOR_CHANGED', a.id, 'Inventory scope differs from frozen task requirements/risks', 'test_design');
        this.requireReview(['testability', 'inventory']);
        qualityModels(this, 'test_design');
    }
    implementation() {
        if (!Object.keys(this.cases).length)
            this.add('CASE_MISSING', 'inventory', 'Implementation requires case/oracle mapping', 'implementation');
        for (const c of values(this.cases))
            if (!c.implementation.digest)
                this.add('IMPLEMENTATION_MISSING', c.id, 'Implementation has not been bound by content digest', 'implementation');
            else
                this.checkRef(c.implementation, this.w.root, c.id, 'implementation');
    }
    executions(claim: any): any[] {
        const runs = [];
        for (const eid of claim.evidence_ids) {
            const e = this.evidence[eid];
            if (!e)
                continue;
            if (e.source_type !== 'host_observed' || !e.applicability.current_task || e.task_id !== this.w.task.id || e.workspace_id !== this.w.id) {
                this.add('EVIDENCE_UNTRUSTED', eid, 'Imported/model/historical evidence is not current host execution', 'execution');
                continue;
            }
            const ex = e.execution;
            if (!ex || this.findings.some(f => f.subject === eid && f.blocking) || ex.activity_id !== null && ex.activity_id !== undefined)
                continue;
            runs.push(ex);
            const observed = ex.cases.map(c => c.id);
            if (!sameSet(ex.requested_case_ids, observed))
                this.add('EXECUTION_CASE_MISSING', eid, 'Framework report does not account for every requested case', 'execution', { basis: difference(ex.requested_case_ids, observed) });
            if (['cancelled', 'timeout', 'blocked'].includes(ex.status) || ex.statistics.errors || (ex.status === 'failed' && !ex.statistics.failed))
                this.add('EXECUTION_INCOMPLETE', eid, 'Execution did not reach a complete test result', 'execution', { basis: [ex.status] });
            if (ex.statistics.tests === 0)
                this.add('ZERO_TESTS', eid, 'No test cases were executed', 'execution');
            else if (ex.statistics.passed + ex.statistics.failed + ex.statistics.errors === 0)
                this.add('ALL_TESTS_SKIPPED', eid, 'All observed cases were skipped or expected failures', 'execution');
            if (!ex.report)
                this.add('TEST_REPORT_MISSING', eid, 'A framework report is required for execution claims', 'execution');
        }
        const required = ['execution_complete', 'task_delivery_complete'].includes(claim.claim_type) ? Object.keys(this.cases) : [], observed = runs.flatMap(r => r.cases.filter(c => ['passed', 'failed'].includes(c.outcome)).map(c => c.id));
        for (const cid of difference(required, observed))
            this.add('CASE_NOT_EXECUTED', cid, 'Required scenario lacks a conclusive current execution observation', 'execution');
        if (!runs.length)
            this.add('EXECUTION_NOT_RUN', claim.id, 'No applicable managed execution evidence', 'execution');
        return runs;
    }
    verifyRequirements(claim: any, runs: any[]) {
        if (!claim.requirement_ids.length)
            this.add('CLAIM_SCOPE_EMPTY', claim.id, 'Requirement verification must name requirements', 'verification');
        for (const rid of claim.requirement_ids) {
            if (!this.requirements.includes(rid)) {
                this.add('REFERENCE_MISSING', rid, 'Requirement is not in frozen task scope', 'verification');
                continue;
            }
            const pids = values(this.props).filter(p => p.requirement_ids.includes(rid)).map(p => p.id), required = values(this.cases).filter(c => intersect(c.proposition_ids, pids)).map(c => c.id);
            if (!required.length)
                this.add('CASE_MISSING', rid, 'No cases trace this requirement', 'verification');
            for (const cid of required.sort()) {
                const observed = runs.flatMap(r => r.cases.filter(c => c.id === cid && c.selector === this.cases[cid].test_selector).map(c => [r, c]));
                if (!observed.length)
                    this.add('CASE_NOT_EXECUTED', cid, 'No managed report observation for required case', 'verification');
                else if (observed.some(([r, c]) => c.outcome !== 'passed' || r.status !== 'passed'))
                    this.add('REQUIREMENT_NOT_VERIFIED', cid, 'Observed failure, skip, cancellation or uncertain run cannot verify requirement', 'verification');
            }
            for (const e of values(this.evidence))
                if (e.execution && e.source_type === 'host_observed' && !this.findings.some(x => x.subject === e.id && x.code !== 'REQUIREMENT_NOT_VERIFIED') && e.execution.cases.some(c => required.includes(c.id) && ['failed', 'error'].includes(c.outcome)))
                    this.add('REQUIREMENT_NOT_VERIFIED', e.id, 'An applicable in-scope failure is preserved even when omitted from claim evidence', 'verification');
        }
    }
    reports(claim: any, { includeReport = true, includeKnowledge = true } = {}) {
        const reports = includeReport ? this.by_type.execution_report ?? [] : [];
        if (includeReport && !reports.length)
            this.add('EXECUTION_REPORT_MISSING', 'execution_report', 'Task delivery requires an execution report', 'report');
        for (const a of reports) {
            const c = a.content;
            if (!eq(c.scope_ref, scopeRef(this.scope)))
                this.add('SCOPE_STALE', a.id, 'Report belongs to another scope revision', 'report');
            if (!sameSet(c.requirement_ids, this.requirements))
                this.add('REPORT_SCOPE_INVALID', a.id, 'Report must list exactly the current acceptance requirements', 'report');
            if (c.coverage_statement !== COVERAGE_STATEMENT)
                this.add('REPORT_SCOPE_INVALID', a.id, 'Report must state its bounded coverage', 'report');
            for (const eid of c.evidence_ids)
                if (!(Object.hasOwn(this.evidence, eid)) || !claim.evidence_ids.includes(eid))
                    this.add('REFERENCE_MISSING', a.id, 'Report evidence is missing from claim', 'report', { basis: [eid] });
            if (!sameSet(c.case_ids, Object.keys(this.cases)))
                this.add('REPORT_COVERAGE_INCOMPLETE', a.id, 'Report must account for all inventory cases', 'report');
            const observed = values(this.evidence).filter(e => claim.evidence_ids.includes(e.id) && e.source_type === 'host_observed' && e.execution && e.applicability.current_task && !this.findings.some(f => f.subject === e.id && f.blocking)).flatMap(e => e.execution.cases.map(c => c.id));
            for (const cid of difference(Object.keys(this.cases), observed))
                this.add('CASE_NOT_EXECUTED', cid, 'Delivery report lists a case without applicable managed execution', 'report');
            const failures = values(this.evidence).filter(e => claim.evidence_ids.includes(e.id) || (e.source_type === 'host_observed' && !this.findings.some(x => x.subject === e.id))).some(e => e.execution && !e.execution.activity_id && e.execution.cases.some(c => c.outcome === 'failed'));
            const applicable = values(this.evidence).filter(e => claim.evidence_ids.includes(e.id) && e.source_type === 'host_observed' && e.execution && !e.execution.activity_id && !this.findings.some(f => f.subject === e.id && f.blocking)).map(e => e.execution);
            const conclusive = applicable.filter(r => ['passed', 'failed'].includes(r.status)).flatMap(r => r.cases.filter(c => ['passed', 'failed'].includes(c.outcome)).map(c => c.id));
            const complete = Object.keys(this.cases).length > 0 && subset(Object.keys(this.cases), conclusive) && applicable.every(r => ['passed', 'failed'].includes(r.status) && !r.statistics.errors && (r.status !== 'failed' || r.statistics.failed > 0));
            const functional = failures ? 'failed' : complete ? 'passed' : 'undetermined';
            if (c.execution_complete !== complete || c.functional_result !== functional)
                this.add('REPORT_RESULT_INCONSISTENT', a.id, 'Report completion and functional result differ from managed observations', 'report');
            if (failures && (c.functional_result === 'passed' || c.outcome === 'passed'))
                this.add('FAILURE_HIDDEN', a.id, 'Report marks failed execution as passed', 'report');
        }
        if (includeKnowledge && !this.by_type.knowledge?.length)
            this.add('KNOWLEDGE_MISSING', 'knowledge', 'Task delivery requires sourced knowledge notes', 'knowledge');
        for (const a of this.by_type.knowledge ?? [])
            for (const entry of a.content.entries)
                for (const ref of entry.source_refs)
                    if (!(Object.hasOwn(this.sources, ref)) && !(Object.hasOwn(this.evidence, ref)))
                        this.add('REFERENCE_MISSING', a.id, 'Knowledge source is unavailable', 'knowledge', { basis: [ref] });
    }
}
/** Immutable-data boundary: all filesystem, signature, schema and binding observations
 * happen here. The returned value is local evaluator input, never an authorization token.
 * Final assess still compares the live snapshot and commits under a task lock. */
export interface CollectedEvaluationInput {
    snapshot: string;
    evaluator: string;
    rules: any;
    facts: any;
    reviewAssurance: any[];
}
export function collectEvaluationInput(w: Workspace): CollectedEvaluationInput {
    const snapshot = w.snapshot(), facts = new Facts(w);
    for (const c of values(facts.cases))
        if (c.implementation.digest)
            facts.observeRef(c.implementation, w.root);
    const reviewAssurance = derivedReviewAssurance(facts), rules = qualityRules(), evaluator = evaluatorIdentity();
    const data = { ...facts, w: { root: w.root, project: w.project, id: w.id, task: w.task, profile: w.profile, binding: w.binding }, reviewed: [...facts.reviewed], validReviews: [...facts.validReviews], collected: true };
    if (w.snapshot() !== snapshot)
        throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed while collecting evaluation inputs');
    return structuredClone({ snapshot, evaluator, rules, facts: data, reviewAssurance });
}
/** Pure claim evaluation over already observed data. No filesystem or subprocess APIs.
 * Inputs remain unchanged so callers can deterministically recompute another claim. */
export function evaluateCollected(input: CollectedEvaluationInput, claim: any): [
    any,
    any
] {
    const data = structuredClone(input), f: Facts = Object.assign(Object.create(Facts.prototype), data.facts);
    for (const key of ['artifacts', 'evidence', 'by_type', 'sources', 'props', 'oracles', 'cases', 'referenceObservations'])
        f[key] = Object.assign(Object.create(null), f[key]);
    f.reviewed = new Set(data.facts.reviewed);
    f.validReviews = new Set(data.facts.validReviews);
    const w = f.w, current = data.snapshot;
    claim = structuredClone(claim);
    if (claim.task_id !== w.task.id || claim.workspace_id !== w.id)
        throw new BugateError('WORKSPACE_MISMATCH', 'Claim belongs to another task/workspace');
    if (!eq(claim.scope_ref, scopeRef(f.scope)))
        f.add('SCOPE_STALE', claim.id, 'Claim does not bind the current scope revision', 'scope');
    if (!eq(claim.plan_ref, planRef(f)))
        f.add('PLAN_STALE', claim.id, 'Claim does not bind the current inventory', 'scope');
    if (!f.scope)
        f.add('SCOPE_MISSING', claim.id, 'A ScopeContract is required', 'scope');
    for (const issue of f.findings)
        if (Object.hasOwn(f.evidence, issue.subject) && !claim.evidence_ids.includes(issue.subject)) {
            issue.blocking = false;
            issue.severity = 'warning';
        }
    if (claim.workspace_snapshot !== current)
        f.add('ASSESSMENT_STALE', claim.id, 'Claim input snapshot differs from current workspace');
    for (const aid of claim.artifact_ids)
        if (!(Object.hasOwn(f.artifacts, aid)))
            f.add('REFERENCE_MISSING', claim.id, 'Claim artifact is unavailable', 'integrity', { basis: [aid] });
    for (const eid of claim.evidence_ids)
        if (!(Object.hasOwn(f.evidence, eid)))
            f.add('REFERENCE_MISSING', claim.id, 'Claim evidence is unavailable', 'integrity', { basis: [eid] });
    if (claim.known_gaps.length || claim.open_questions.length)
        f.add('CLAIM_HAS_GAPS', claim.id, 'Claim declares unresolved gaps', 'integrity', { basis: [...claim.known_gaps, ...claim.open_questions] });
    let dims = data.rules.claim_dimensions[claim.claim_type];
    if (claim.claim_type === 'task_delivery_complete') {
        dims = [...w.task.delivery_criteria];
        if (dims.some(d => ['test_design', 'implementation'].includes(d)))
            dims = [...new Set([...dims, 'business_understanding', 'testability', 'test_design'])];
    }
    if (dims.includes('business_understanding')) {
        f.business();
        if (!dims.includes('test_design'))
            checkScope(f, { plan: false });
    }
    if (dims.includes('execution') && !dims.includes('test_design'))
        checkScope(f);
    if (dims.includes('test_design'))
        f.design();
    if (dims.includes('implementation'))
        f.implementation();
    const runs = dims.includes('execution') ? f.executions(claim) : [];
    if (dims.includes('verification'))
        f.verifyRequirements(claim, runs);
    if (dims.includes('report') || dims.includes('knowledge'))
        f.reports(claim, { includeReport: dims.includes('report'), includeKnowledge: dims.includes('knowledge') });
    const types = new Set<string>();
    if (dims.includes('business_understanding'))
        for (const t of ['business_brief', 'semantic_review', 'domain_model', 'state_model'])
            types.add(t);
    if (dims.includes('test_design'))
        for (const t of ['testability', 'inventory', 'semantic_review', 'adversarial_review', 'dimension_matrix'])
            types.add(t);
    if (dims.includes('report'))
        types.add('execution_report');
    if (dims.includes('knowledge'))
        types.add('knowledge');
    for (const a of values(f.artifacts))
        if (types.has(a.artifact_type) && !claim.artifact_ids.includes(a.id))
            f.add('CLAIM_INPUT_MISSING', claim.id, 'Used artifact is not referenced by claim', 'integrity', { basis: [a.id] });
    const findings = values(Object.fromEntries(f.findings.map(x => [canonical(x).toString(), x]))).sort((a, b) => [a.dimension, a.code, a.subject, a.message].join('\0').localeCompare([b.dimension, b.code, b.subject, b.message].join('\0'), 'en'));
    const semantic = dims.some(d => ['business_understanding', 'test_design', 'execution'].includes(d)), runtime = dims.includes('execution'), blocked = findings.filter(x => x.blocking), inconclusive = blocked.length > 0 && blocked.every(x => ['SEMANTIC_REVIEW_MISSING', 'BUSINESS_SEMANTICS_UNRESOLVED', 'EXECUTION_NOT_RUN', 'EVIDENCE_UNTRUSTED'].includes(x.code)), verdict = inconclusive ? 'inconclusive' : blocked.length ? 'unsatisfied' : 'satisfied';
    function dimension(name: string, applicable: boolean, limitations: string[]) { const problems = findings.filter(x => x.blocking && (name === 'deterministic' ? !['semantic_review', 'execution', 'verification'].includes(x.dimension) : x.dimension === name)); return { status: applicable ? (problems.length ? 'failed' : 'passed') : 'not_applicable', applicable, basis: problems.length ? [...new Set(problems.map(x => x.code + ':' + x.subject))].sort() : applicable ? ['canonical-rules/v2'] : ['claim-type applicability/v2'], limitations }; }
    const advisory = f.advisory, request: any = { advisory_digest: digest(advisory, 'assessment-advisory-view/v1'), apiVersion: API_VERSION, kind: 'AssessmentRequest', id: 'request-' + claim.id, task_id: w.task.id, workspace_id: w.id, binding: w.binding.protocol, profile: w.binding.profile, claim, workspace_snapshot: current, evaluator_version: VERSION, rules_digest: data.evaluator };
    request.request_digest = objectDigest(request);
    const result: any = { review_assurance: data.reviewAssurance, apiVersion: API_VERSION, kind: 'AssessmentResult', id: 'assessment-' + claim.id, task_id: w.task.id, workspace_id: w.id, claim_id: claim.id, claim_type: claim.claim_type, claim_digest: objectDigest(claim), evaluator_version: VERSION, request_digest: request.request_digest, workspace_snapshot: current, verdict, dimensions: { deterministic: dimension('deterministic', true, ['Schema and content hashes establish consistency, not the truth of supplied facts.']), semantic_review: dimension('semantic_review', semantic, ['Source-backed reviewer opinion is not a mathematical proof.']), runtime_validation: dimension('execution', runtime, ['Managed local-process observation; same-permission filesystem owners can alter local evidence.']) }, findings, evidence_ids: claim.evidence_ids, unknowns: [...claim.open_questions, ...claim.known_gaps], scope: claim.claim_type === 'requirement_verified' ? claim.requirement_ids : f.requirements, quality_posture: verdict === 'satisfied' ? 'satisfactory' : verdict === 'inconclusive' ? 'uncertain' : 'needs_improvement' };
    if (runtime && !runs.length)
        result.dimensions.runtime_validation.status = 'not_run';
    if (semantic && findings.some(x => ['SEMANTIC_REVIEW_MISSING', 'SEMANTIC_REVIEW_STALE'].includes(x.code)))
        result.dimensions.semantic_review.status = 'not_run';
    if (runtime && runs.length && findings.some(x => x.dimension === 'verification'))
        result.dimensions.runtime_validation.status = 'failed';
    const observed = runs.filter(r => ['passed', 'failed'].includes(r.status)).flatMap(r => r.cases.filter(c => ['passed', 'failed'].includes(c.outcome)).map(c => c.id));
    const closed = Object.keys(f.cases).length > 0 && subset(Object.keys(f.cases), observed) && !findings.some(x => x.blocking && !['verification', 'report', 'knowledge'].includes(x.dimension));
    function authenticFailure(e: any, historical = false) {
        const ex = e.execution;
        if (e.source_type !== 'host_observed' || !ex || ex.activity_id || ex.status !== 'failed' || !ex.cases.some(c => c.outcome === 'failed'))
            return false;
        const ignored = ['REQUIREMENT_NOT_VERIFIED', ...historical ? ['EXECUTION_INPUTS_CHANGED', 'SCOPE_STALE'] : []];
        return !findings.some(x => x.subject === e.id && !ignored.includes(x.code));
    }
    const failures = runs.some(r => r.cases.some(c => c.outcome === 'failed')) || values(f.evidence).some(e => authenticFailure(e));
    result.acceptance = { execution_complete: runtime ? closed : null, functional_result: runtime ? (failures ? 'failed' : closed ? 'passed' : 'undetermined') : 'not_assessed', requirement_ids: f.requirements, blocking_codes: [...new Set(findings.filter(x => x.blocking).map(x => x.code))].sort(), suggestion_ids: advisory.map(x => x.id), historical_failure_ids: values(f.evidence).filter(e => authenticFailure(e, true)).map(e => e.id), coverage_statement: COVERAGE_STATEMENT };
    return [request, result];
}
export function evaluate(w: Workspace, claim: any): [
    any,
    any
] { validateSchema(claim, 'Claim'); const pair = evaluateCollected(collectEvaluationInput(w), claim); validateSchema(pair[0], 'AssessmentRequest'); validateSchema(pair[1], 'AssessmentResult'); return pair; }
export function assess(task: string, claimFile: string, { expectedSnapshot = undefined }: {
    expectedSnapshot?: string;
} = {}): any {
    const w = new Workspace(task), p = within(w.root, claimFile, true), claim = validateSchema(loadJson(p), 'Claim');
    safeId(claim.id);
    return locked(w.root, () => {
        const before = w.snapshot();
        if (expectedSnapshot && before !== expectedSnapshot)
            throw new BugateError('CONCURRENT_CONFLICT', 'Snapshot changed before assessment');
        const [request, result] = evaluate(w, claim);
        if (w.snapshot() !== before)
            throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed during assessment');
        if (!eq(loadJson(p), claim))
            throw new BugateError('CONCURRENT_CONFLICT', 'Claim changed during assessment');
        const key = request.request_digest.split(':')[1];
        writeJson(w.root, 'claims/' + claim.id + '.json', claim);
        writeJson(w.root, 'assessments/' + key + '.request.json', request, { immutable: true });
        writeJson(w.root, 'assessments/' + key + '.result.json', result, { immutable: true });
        if (w.snapshot() !== before)
            throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed at assessment commit');
        return result;
    });
}
export function validation(task: string): any { const w = new Workspace(task), f = new Facts(w); f.business(); f.design(); return { valid: !f.findings.some(x => x.blocking), workspace_snapshot: w.snapshot(), binding: w.binding, findings: f.findings, limitations: ['Deterministic validation is not execution or business correctness.'] }; }
export function status(task: string): any {
    const w = new Workspace(task), f = new Facts(w);
    for (const issue of f.findings)
        if (Object.hasOwn(f.evidence, issue.subject)) {
            issue.blocking = false;
            issue.severity = 'warning';
        }
    f.business();
    f.design();
    const snap = w.snapshot(), results = [];
    let ready = false;
    for (const file of fs.readdirSync(w.path('claims')).filter(x => x.endsWith('.json')).sort()) {
        try {
            const claim = validateSchema(loadJson(within(w.root, path.join('claims', file), true)), 'Claim'), [, result] = evaluate(w, claim);
            results.push({ claim_id: claim.id, claim_type: claim.claim_type, verdict: result.verdict, fresh: claim.workspace_snapshot === snap, claim_digest: objectDigest(claim), request_digest: result.request_digest, findings: result.findings });
            if (result.verdict === 'satisfied' && claim.claim_type === 'test_design_complete')
                ready = true;
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
            f.add(e.code, file, e.message);
        }
    }
    const quality = !results.length ? 'unclaimed' : results.every(x => x.verdict === 'satisfied') ? 'satisfactory' : 'needs_improvement', witness = filesystemManifest(w);
    if (!eq(witness, filesystemManifest(w)) || w.snapshot() !== snap)
        throw new BugateError('CONCURRENT_CONFLICT', 'Workspace changed during execution-world verification');
    return { scope_ref: scopeRef(f.scope), plan_ref: planRef(f), acceptance_scope: f.scope, suggestions: suggestions(w), coverage_statement: COVERAGE_STATEMENT, task_id: w.task.id, workspace_id: w.id, workspace_snapshot: snap, binding: w.binding, quality_posture: { overall: quality }, implementation_ready: ready, execution_state: executionState(f), findings: f.findings, assessments: results, filesystem_manifest: witness };
}
function executionState(f: Facts) {
    const runs = values(f.evidence).filter(e => e.source_type === 'host_observed' && e.execution && !e.execution.activity_id && !f.findings.some(y => y.subject === e.id)).map(e => e.execution);
    if (!runs.length)
        return 'not_run';
    if (runs.some(r => r.statistics.failed))
        return 'failed';
    if (runs.some(r => ['failed', 'cancelled', 'timeout', 'blocked'].includes(r.status) || r.statistics.tests === 0 || r.statistics.passed + r.statistics.failed + r.statistics.errors === 0))
        return 'blocked';
    return 'passed';
}
