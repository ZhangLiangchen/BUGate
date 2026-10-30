import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { XMLParser, XMLValidator } from 'fast-xml-parser';
import { API_VERSION, BugateError, eq, values } from './errors.js';
import { contentDigest, objectDigest, loadJson, validateSchema } from './protocol.js';
import { Workspace, within, locked, writeJson, atomicWrite, safeId, executionInputManifest } from './workspace.js';
import { authorize, activeScope, scopeRef, planRef, AuthorizationOptions } from './scope.js';
import { Facts } from './assessment.js';
export const EVIDENCE_LIMITATIONS = ['Local managed process observation; not tamper-proof against a same-permission filesystem owner.', 'A framework result does not prove the correctness of its business oracle.'];
export function recordEvidence(task: string, inputFile: string): any {
    const w = new Workspace(task), v = loadJson(within(w.root, inputFile, true));
    if (!v || typeof v !== 'object' || Array.isArray(v))
        throw new BugateError('STRUCTURE_INVALID', 'Evidence input must be an object');
    if (v.source_type === 'host_observed')
        throw new BugateError('EVIDENCE_SOURCE_FORBIDDEN', 'Imported evidence cannot claim host_observed provenance');
    for (const ref of v.references ?? [])
        if (ref?.path && ref.digest == null)
            ref.digest = contentDigest(fs.readFileSync(within(w.root, ref.path, true)));
    if (!(Object.hasOwn(v, 'digest')))
        v.digest = objectDigest(v);
    validateSchema(v, 'Evidence');
    if (v.task_id !== w.task.id || v.workspace_id !== w.id)
        throw new BugateError('WORKSPACE_MISMATCH', 'Evidence is for another task/workspace');
    if (v.digest !== objectDigest(v))
        throw new BugateError('OBJECT_DIGEST_MISMATCH', 'Evidence digest does not match content');
    for (const ref of v.references)
        if (contentDigest(fs.readFileSync(within(w.root, ref.path, true))) !== ref.digest)
            throw new BugateError('REFERENCE_DIGEST_MISMATCH', 'Evidence reference differs');
    locked(w.root, () => writeJson(w.root, 'evidence/' + safeId(v.id) + '.json', v, { immutable: true }));
    return v;
}
const emptyStats = () => ({ tests: 0, passed: 0, failed: 0, errors: 0, skipped: 0, xfail: 0 });
export function parseJUnit(p: string, mapping: Record<string, string>): [
    any,
    any[],
    any
] {
    if (!fs.existsSync(p) || !fs.statSync(p).isFile())
        return [null, [], emptyStats()];
    if (fs.statSync(p).size > 8 * 1024 * 1024)
        throw new BugateError('OUTPUT_LIMIT', 'JUnit report exceeds 8 MiB', 1);
    const bytes = fs.readFileSync(p), xml = new TextDecoder('utf8', { fatal: true }).decode(bytes);
    if (/<!DOCTYPE|<!ENTITY/i.test(xml) || XMLValidator.validate(xml) !== true)
        throw new BugateError('TEST_REPORT_INVALID', 'Invalid or entity-bearing JUnit report', 1);
    const parsed = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_', parseAttributeValue: false, parseTagValue: false, processEntities: true }).parse(xml);
    const keys = Object.keys(parsed).filter(k => !k.startsWith('?'));
    if (keys.length !== 1 || !['testsuite', 'testsuites'].includes(keys[0]))
        throw new BugateError('TEST_REPORT_INVALID', 'Expected a JUnit testsuite/testsuites root', 1);
    const all: any[] = [], suites: any[] = [];
    function visit(node: any) {
        if (!node || typeof node !== 'object')
            return;
        for (const [k, v] of Object.entries(node)) {
            const list = Array.isArray(v) ? v : [v];
            if (k === 'testcase')
                for (const t of list)
                    all.push(t);
            if (['testsuite', 'testsuites'].includes(k))
                for (const s of list) {
                    suites.push(s);
                    visit(s);
                }
        }
    }
    visit(parsed);
    const stats = emptyStats(), cases: any[] = [], seen = new Set<string>();
    for (const t of all) {
        const selector = [t['@_classname'] ?? '', t['@_name'] ?? ''].filter(Boolean).join('.');
        if (!selector || seen.has(selector))
            throw new BugateError('TEST_REPORT_INVALID', 'Missing or duplicate JUnit test selector', 1);
        seen.add(selector);
        let outcome = 'passed';
        if (Object.hasOwn(t, 'failure'))
            outcome = 'failed';
        else if (Object.hasOwn(t, 'error'))
            outcome = 'error';
        else if (Object.hasOwn(t, 'skipped')) {
            const s = t.skipped ?? {};
            outcome = (String(s['@_type'] ?? '') + ' ' + String(s['@_message'] ?? '')).toLowerCase().includes('xfail') ? 'xfail' : 'skipped';
        }
        stats.tests++;
        stats[outcome === 'error' ? 'errors' : outcome]++;
        if (Object.keys(mapping).length && !(Object.hasOwn(mapping, selector)))
            throw new BugateError('TEST_REPORT_UNPLANNED', 'Report contains a selector outside the authorized requested cases', 1, selector);
        if (Object.hasOwn(mapping, selector))
            cases.push({ id: mapping[selector], selector, outcome });
    }
    function testcases(node: any): any[] {
        if (!node || typeof node !== 'object')
            return [];
        let out: any[] = [];
        for (const [k, v] of Object.entries(node)) {
            const list = Array.isArray(v) ? v : [v];
            if (k === 'testcase')
                out.push(...list);
            if (['testsuite', 'testsuites'].includes(k))
                for (const s of list)
                    out.push(...testcases(s));
        }
        return out;
    }
    for (const suite of suites) {
        if (!suite || typeof suite !== 'object')
            continue;
        const children = testcases(suite), actual = { tests: children.length, failures: children.filter(t => Object.hasOwn(t, 'failure')).length, errors: children.filter(t => Object.hasOwn(t, 'error')).length, skipped: children.filter(t => Object.hasOwn(t, 'skipped')).length };
        for (const [key, count] of Object.entries(actual))
            if (Object.hasOwn(suite, '@_' + key)) {
                const s = String(suite['@_' + key]);
                if (!/^[+-]?\d+$/.test(s) || !Number.isSafeInteger(Number(s)))
                    throw new BugateError('TEST_REPORT_INVALID', 'JUnit aggregate is not an integer', 1);
                if (Number(s) !== count)
                    throw new BugateError('TEST_REPORT_INVALID', 'JUnit aggregate contradicts testcase observations', 1, { field: key, declared: Number(s), observed: count });
            }
    }
    return [{ path: p, digest: contentDigest(bytes) }, cases, stats];
}
export interface ExecuteOptions extends AuthorizationOptions {
    runId: string;
    report?: string;
    agentId?: string;
    sessionId?: string;
    callId?: string;
    parentCallId?: string;
    rootCallId?: string;
    timeout?: number;
    maxOutput?: number;
    signal?: AbortSignal;
}
export async function execute(task: string, argv: string[], options: ExecuteOptions): Promise<any> {
    const { runId, caseIds = [], report = null, agentId = 'cli', sessionId = 'cli', callId = null, parentCallId = null, rootCallId = null, timeout = 60, maxOutput = 1048576, activityId = null, environmentId = null, expectedScopeDigest = null, expectedPlanDigest = null, signal } = options;
    const w = new Workspace(task);
    safeId(runId);
    if (!argv.length || !argv.every(x => typeof x === 'string' && x && !x.includes('\0')))
        throw new BugateError('INVALID_ARGUMENT', 'Execution argv must be a non-empty array of strings');
    if (!Number.isFinite(timeout) || timeout <= 0 || timeout > 3600 || !Number.isSafeInteger(maxOutput) || maxOutput < 1 || maxOutput > 16 * 1024 * 1024)
        throw new BugateError('INVALID_ARGUMENT', 'Execution bounds are invalid');
    const eid = 'run-' + runId, destination = 'evidence/' + eid + '.json', intent = '.bugate/executions/' + runId + '.json', command = { argv, case_ids: caseIds, report, workspace_id: w.id, activity_id: activityId, environment_id: environmentId, expected_scope_digest: expectedScopeDigest, expected_plan_digest: expectedPlanDigest };
    function prior() {
        if (!fs.existsSync(w.path(intent)))
            return null;
        const p = w.read(intent);
        if (!eq(p.command, command))
            throw new BugateError('CONCURRENT_CONFLICT', 'Run id was used for a different execution');
        if (fs.existsSync(w.path(destination)))
            return validateSchema(w.read(destination), 'Evidence');
        throw new BugateError(p.state === 'failed_no_effect' ? 'EXECUTION_FAILED_NO_EFFECT' : 'EXECUTION_OUTCOME_UNKNOWN', 'Prior execution has no completed evidence; retain original run id and diagnose before retry', 1);
    }
    const completed = locked(w.root, prior);
    if (completed)
        return completed;
    const admission = authorize(task, { caseIds, activityId, environmentId, expectedScopeDigest, expectedPlanDigest });
    if (!admission.allowed)
        throw new BugateError(admission.findings.find(f => f.blocking).code, 'Execution is not authorized for the current scope and plan', 2, admission);
    const implementations: any = Object.create(null), mapping: any = Object.create(null), facts = new Facts(w);
    for (const cid of caseIds) {
        const c = facts.cases[cid];
        if (!c)
            throw new BugateError('REFERENCE_MISSING', 'Execution case is absent from inventory', 2, cid);
        const actual = contentDigest(fs.readFileSync(within(w.root, c.implementation.path, true)));
        if (actual !== c.implementation.digest)
            throw new BugateError('REFERENCE_DIGEST_MISMATCH', 'Implementation changed before execution', 2, cid);
        implementations[c.implementation.path] = actual;
        if (Object.hasOwn(mapping, c.test_selector))
            throw new BugateError('DUPLICATE_ID', 'Multiple cases share a JUnit selector');
        mapping[c.test_selector] = cid;
    }
    let reportPath = report ? within(w.root, report) : null;
    if (reportPath && fs.existsSync(reportPath))
        throw new BugateError('TEST_REPORT_EXISTS', 'A run must produce a fresh report path; refusing pre-existing report');
    let snapshot = '';
    const raced = locked(w.root, () => {
        const p = prior();
        if (p)
            return p;
        if (!eq(scopeRef(activeScope(w)), admission.scope_ref))
            throw new BugateError('SCOPE_STALE', 'Scope changed between authorization and execution');
        if (!eq(planRef(new Facts(w)), admission.plan_ref))
            throw new BugateError('PLAN_STALE', 'Plan changed between authorization and execution');
        if (activityId) {
            const auxiliary = activeScope(w).auxiliary.find(a => a.id === activityId), ledger = '.bugate/activity-runs/' + admission.scope_ref.digest.split(':')[1] + '/' + safeId(activityId);
            if (fs.existsSync(w.path(ledger)) && fs.readdirSync(w.path(ledger)).filter(x => x.endsWith('.json')).length >= auxiliary.max_runs)
                throw new BugateError('DIAGNOSTIC_LIMIT_REACHED', 'Activity budget was consumed concurrently');
            writeJson(w.root, ledger + '/' + runId + '.json', { run_id: runId, activity_id: activityId, scope_ref: admission.scope_ref }, { immutable: true });
        }
        snapshot = w.snapshot();
        writeJson(w.root, intent, { command, input_snapshot: snapshot }, { immutable: true });
        return null;
    });
    if (raced)
        return raced;
    const inputManifest = executionInputManifest(w), started = new Date().toISOString(), buffers: {
        stdout: Buffer[];
        stderr: Buffer[];
    } = { stdout: [], stderr: [] };
    let outputSize = 0, stopReason: string | null = null, proc: any, exitCode: number | null = null, killTimer: any, deadline: any, drainTimer: any, finishDrain: (() => void) | undefined;
    let processError: any = null;
    function kill(sig: NodeJS.Signals) {
        if (!proc?.pid)
            return;
        try {
            if (process.platform === 'win32')
                proc.kill(sig);
            else
                process.kill(-proc.pid, sig);
        }
        catch (e) {
            if ((e as any).code !== 'ESRCH')
                throw e;
        }
    }
    function stop(reason: string) {
        if (stopReason)
            return;
        stopReason = reason;
        kill('SIGTERM');
        killTimer = setTimeout(() => kill('SIGKILL'), 500);
        if (!drainTimer)
            drainTimer = setTimeout(() => { proc?.stdout?.destroy(); proc?.stderr?.destroy(); finishDrain?.(); }, 1500);
    }
    const cancel = () => stop('cancelled');
    process.on('SIGTERM', cancel);
    process.on('SIGINT', cancel);
    signal?.addEventListener('abort', cancel, { once: true });
    try {
        await new Promise<void>(resolve => {
            finishDrain = resolve;
            proc = spawn(argv[0], argv.slice(1), { cwd: w.root, stdin: 'ignore', stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32' } as any);
            proc.once('error', (e: any) => { processError = e; resolve(); });
            for (const stream of ['stdout', 'stderr'] as const)
                proc[stream].on('data', (chunk: Buffer) => {
                    const remaining = Math.max(0, maxOutput - outputSize), taken = chunk.subarray(0, remaining);
                    buffers[stream].push(taken);
                    outputSize += taken.length;
                    if (chunk.length > remaining)
                        stop('output_limit');
                });
            proc.once('exit', (code: number | null, sig: NodeJS.Signals | null) => {
                exitCode = code ?? -(os.constants.signals[sig ?? 'SIGTERM'] ?? 15);
                if (!drainTimer)
                    drainTimer = setTimeout(() => {
                        if (!stopReason)
                            stopReason = 'drain_incomplete';
                        proc.stdout.destroy();
                        proc.stderr.destroy();
                        resolve();
                    }, 1000);
            });
            proc.once('close', (code: number | null, sig: NodeJS.Signals | null) => { exitCode = code ?? -(os.constants.signals[sig ?? 'SIGTERM'] ?? 15); resolve(); });
            deadline = setTimeout(() => stop('timeout'), timeout * 1000);
            if (signal?.aborted)
                stop('cancelled');
        });
    }
    finally {
        clearTimeout(deadline);
        clearTimeout(killTimer);
        clearTimeout(drainTimer);
        process.off('SIGTERM', cancel);
        process.off('SIGINT', cancel);
        signal?.removeEventListener('abort', cancel);
        if (proc?.pid && proc.exitCode === null && proc.signalCode === null)
            kill('SIGKILL');
    }
    if (processError) {
        locked(w.root, () => writeJson(w.root, intent, { command, input_snapshot: snapshot, state: 'failed_no_effect' }));
        throw new BugateError('DEPENDENCY_MISSING', 'Cannot start execution process', 1, String(processError));
    }
    const finished = new Date().toISOString(), refs: any = {};
    locked(w.root, () => {
        for (const stream of ['stdout', 'stderr'] as const) {
            const bytes = Buffer.concat(buffers[stream]), rel = 'logs/' + runId + '.' + stream + '.log';
            atomicWrite(w.root, rel, bytes, { immutable: true });
            refs[stream] = { path: rel, digest: contentDigest(bytes) };
        }
    });
    let reportRef = null, cases: any[] = [], stats = emptyStats(), reportError: string | null = null;
    if (reportPath) {
        try {
            reportPath = within(w.root, report!);
            [reportRef, cases, stats] = parseJUnit(reportPath, mapping);
            if (reportRef)
                reportRef.path = path.relative(w.root, reportPath).split(path.sep).join('/');
        }
        catch (e) {
            if (!(e instanceof BugateError))
                throw e;
            reportError = e.code;
        }
    }
    const resultStatus = stopReason === 'cancelled' ? 'cancelled' : stopReason === 'timeout' ? 'timeout' : stopReason || reportError ? 'blocked' : exitCode || stats.failed || stats.errors ? 'failed' : 'passed';
    const ex = { scope_ref: admission.scope_ref, plan_ref: admission.plan_ref, activity_id: activityId, environment_id: admission.environment_id, run_id: runId, agent_id: agentId, session_id: sessionId, call_id: callId ?? runId, parent_call_id: parentCallId, root_call_id: rootCallId ?? callId ?? runId, argv, cwd: w.root, started_at: started, finished_at: finished, status: resultStatus, exit_code: exitCode, stdout: refs.stdout, stderr: refs.stderr, report: reportRef, statistics: stats, requested_case_ids: caseIds, cases, input_snapshot: snapshot, input_manifest: inputManifest, implementation_digests: { ...implementations }, collector: 'bugate.execution/v1' };
    const value: any = { apiVersion: API_VERSION, kind: 'Evidence', id: eid, task_id: w.task.id, workspace_id: w.id, source_type: 'host_observed', evidence_type: report ? 'test_execution' : 'runtime_probe', description: 'Managed execution capture' + (stopReason || reportError ? ' (' + (stopReason ?? reportError) + ')' : ''), references: [refs.stdout, refs.stderr, ...reportRef ? [reportRef] : []], applicability: { run_id: runId, current_task: true, limitations: EVIDENCE_LIMITATIONS }, execution: ex };
    value.digest = objectDigest(value);
    validateSchema(value);
    locked(w.root, () => { writeJson(w.root, '.bugate/observations/' + eid + '.json', { evidence_digest: value.digest, workspace_id: w.id, collector: 'bugate.execution/v1' }, { immutable: true }); writeJson(w.root, destination, value, { immutable: true }); });
    if (stopReason === 'timeout')
        throw new BugateError('EXECUTION_TIMEOUT', 'Managed process timed out and was terminated', 124, { evidence_id: eid });
    if (stopReason === 'cancelled')
        throw new BugateError('EXECUTION_CANCELLED', 'Managed process was cancelled and terminated', 130, { evidence_id: eid });
    if (stopReason === 'drain_incomplete')
        throw new BugateError('EXECUTION_OUTCOME_UNKNOWN', 'Process exited but inherited output did not drain within the bound; escaped descendants are not claimed terminated', 1, { evidence_id: eid });
    if (stopReason === 'output_limit')
        throw new BugateError('OUTPUT_LIMIT', 'Managed process exceeded output budget and was terminated', 1, { evidence_id: eid });
    if (reportError)
        throw new BugateError(reportError, 'Managed execution produced an invalid framework report', 1, { evidence_id: eid });
    return value;
}
