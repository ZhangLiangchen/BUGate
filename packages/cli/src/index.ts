#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { API_VERSION, VERSION, PROTOCOL_VERSION, BugateError, protocolIdentity, initialize, prepare, status, validation, assess, recordArtifact, publishArtifact, settleArtifact, inspectHistory, verifyHostContext, recordEvidence, execute, showScope, checkScopeTask, recordRequest, recordScope, recordSuggestion, suggestions, Workspace, authorize, parseJson, loadJson, within, eq, type HostContext } from '@bugate/core';
import { dispatchLifecycle } from '@bugate/install';
function parse(argv: string[]): any {
    const a: any = { case: [], argv: [] };
    const positional: string[] = [];
    const flags = new Set(['json', 'rollback', 'host-observation-stdin']);
    for (let i = 0; i < argv.length; i++) {
        const x = argv[i];
        if (x === '--') {
            a.argv = argv.slice(i + 1);
            break;
        }
        if (x.startsWith('--')) {
            const k = x.slice(2);
            if (flags.has(k)) {
                a[k] = true;
                continue;
            }
            if (!['task', 'project', 'sut-profile', 'max-bytes', 'claim', 'expected-snapshot', 'input', 'expected-digest', 'run-id', 'case', 'report', 'agent-id', 'session-id', 'call-id', 'parent-call-id', 'root-call-id', 'timeout', 'max-output', 'activity-id', 'environment-id', 'expected-scope-digest', 'expected-plan-digest'].includes(k))
                throw new BugateError('INVALID_ARGUMENT', 'Unknown option: ' + x);
            const v = argv[++i];
            if (v === undefined || v.startsWith('--'))
                throw new BugateError('INVALID_ARGUMENT', 'Missing value: ' + x);
            if (k === 'case')
                a.case.push(v);
            else if (Object.hasOwn(a, k))
                throw new BugateError('INVALID_ARGUMENT', 'Duplicate option: ' + x);
            else
                a[k] = v;
        }
        else
            positional.push(x);
    }
    a.command = positional[0];
    a.subcommand = positional[1];
    if (positional.length > 2)
        throw new BugateError('INVALID_ARGUMENT', 'Unexpected positional arguments');
    return a;
}
const required = (a: any, k: string): string => {
    if (!a[k])
        throw new BugateError('INVALID_ARGUMENT', 'Required option: --' + k);
    return a[k];
};
export async function runCommand(argv: string[], hostContext?: HostContext): Promise<{
    result: any;
    exitCode: number;
}> {
    let operation = argv[0] ?? 'unknown', data: any = null, errors: any[] = [], invocation = 'completed', exitCode = 0;
    try {
        if (argv[0] === '--version')
            argv = ['version', ...argv.slice(1)];
        if (['setup', 'remove', 'doctor'].includes(argv[0])) {
            data = await dispatchLifecycle(argv);
            operation = argv[0];
        }
        else {
            const a = parse(argv);
            operation = a.command ?? 'unknown';
            if (a.subcommand)
                operation += '.' + a.subcommand;
            const auth = { caseIds: a.case, activityId: a['activity-id'], environmentId: a['environment-id'], expectedScopeDigest: a['expected-scope-digest'], expectedPlanDigest: a['expected-plan-digest'] };
            if (a.command === 'version') {
                if (a.subcommand)
                    throw new BugateError('INVALID_ARGUMENT', 'Unexpected version argument');
                data = { core_version: VERSION, protocol_version: PROTOCOL_VERSION, api_version: API_VERSION, protocol_digest: protocolIdentity().digest };
            }
            else if (a.command === 'init')
                data = initialize(required(a, 'project'), required(a, 'task'), required(a, 'sut-profile'));
            else {
                const task = required(a, 'task');
                switch (operation) {
                    case 'protocol.prepare':
                        data = prepare(task, a['max-bytes'] === undefined ? 8000 : Number(a['max-bytes']));
                        break;
                    case 'history.inspect':
                        data = inspectHistory(task);
                        break;
                    case 'status':
                        data = status(task);
                        break;
                    case 'validate':
                        data = validation(task);
                        exitCode = data.valid ? 0 : 3;
                        break;
                    case 'assess':
                        data = assess(task, required(a, 'claim'), { expectedSnapshot: a['expected-snapshot'] });
                        exitCode = data.verdict === 'satisfied' ? 0 : 3;
                        break;
                    case 'artifact.record':
                        data = recordArtifact(task, required(a, 'input'), { expectedDigest: a['expected-digest'] });
                        break;
                    case 'artifact.settle':
                        if (!hostContext?.settlement)
                            throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Settlement requires DSH final result');
                        data = settleArtifact(task, hostContext);
                        break;
                    case 'artifact.publish':
                        if (!hostContext?.artifact)
                            throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Inline publication requires a captured DSH tool call');
                        data = publishArtifact(task, hostContext.artifact, { expectedDigest: a['expected-digest'] ?? hostContext.execution?.arguments?.expected_digest, hostContext });
                        break;
                    case 'evidence.record':
                        data = recordEvidence(task, required(a, 'input'));
                        break;
                    case 'scope.show':
                        data = showScope(task);
                        break;
                    case 'scope.check':
                        data = checkScopeTask(task);
                        exitCode = data.valid ? 0 : 3;
                        break;
                    case 'scope.request': {
                        const input = required(a, 'input');
                        if (hostContext) {
                            verifyHostContext(task, 'scope.request', hostContext);
                            const actual = loadJson(within(new Workspace(task).root, input, true)), u = hostContext.user_request;
                            if (!u || actual.message !== u.message || actual.session_id !== u.session_id || actual.source_event_id !== u.source_event_id || actual.origin !== 'user')
                                throw new BugateError('SCOPE_AUTHORITY_INVALID', 'Scope request differs from captured user event');
                        }
                        data = recordRequest(task, input);
                        break;
                    }
                    case 'scope.record':
                        data = recordScope(task, required(a, 'input'), { expectedDigest: a['expected-digest'] });
                        break;
                    case 'suggestion.record':
                        data = recordSuggestion(task, required(a, 'input'));
                        break;
                    case 'suggestion.list':
                        data = suggestions(new Workspace(task));
                        break;
                    case 'execution.authorize':
                        data = authorize(task, auth);
                        exitCode = data.allowed ? 0 : 3;
                        break;
                    case 'execution.run':
                        data = await execute(task, a.argv, { ...auth, runId: required(a, 'run-id'), report: a.report, agentId: a['agent-id'], sessionId: a['session-id'], callId: a['call-id'], parentCallId: a['parent-call-id'], rootCallId: a['root-call-id'], timeout: a.timeout === undefined ? 60 : Number(a.timeout), maxOutput: a['max-output'] === undefined ? 1048576 : Number(a['max-output']) });
                        break;
                    default: throw new BugateError('INVALID_ARGUMENT', 'Unknown command: ' + operation);
                }
            }
        }
    }
    catch (e) {
        if (e instanceof BugateError || e && typeof (e as any).code === 'string' && typeof (e as any).exitCode === 'number') {
            exitCode = (e as any).exitCode;
            errors = [{ code: (e as any).code, message: (e as any).message, details: (e as any).details ?? null }];
        }
        else {
            exitCode = 1;
            errors = [{ code: 'RUNTIME_FAILURE', message: String((e as any)?.message ?? e), details: (e as any)?.name ?? null }];
        }
        invocation = ({ 2: 'invalid_input', 124: 'timeout', 130: 'cancelled' } as any)[exitCode] ?? 'runtime_error';
    }
    return { result: { apiVersion: API_VERSION, kind: 'CommandResult', operation, request_id: randomUUID(), invocation, data, errors }, exitCode };
}
async function main() {
    let hostContext: HostContext | undefined;
    const argv = process.argv.slice(2);
    if (argv.includes('--host-observation-stdin')) {
        try {
            const input = fs.readFileSync(0);
            if (input.length > 8 * 1024 * 1024)
                throw new BugateError('OUTPUT_LIMIT', 'Host observation exceeds input bound');
            const body = parseJson(new TextDecoder('utf8', { fatal: true }).decode(input));
            const parsed = parse(argv);
            hostContext = verifyHostContext(required(parsed, 'task'), parsed.command + '.' + parsed.subcommand, body.context);
        }
        catch (e) {
            const x = e instanceof BugateError ? e : new BugateError('STRUCTURE_INVALID', 'Invalid host observation input');
            process.stdout.write(JSON.stringify({ apiVersion: API_VERSION, kind: 'CommandResult', operation: argv.slice(0, 2).join('.'), request_id: randomUUID(), invocation: 'invalid_input', data: null, errors: [x.asDict()] }) + '\n');
            process.exitCode = x.exitCode;
            return;
        }
    }
    const { result, exitCode } = await runCommand(argv, hostContext);
    process.stdout.write(JSON.stringify(result) + '\n');
    process.exitCode = exitCode;
}
if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url)))
    await main();
