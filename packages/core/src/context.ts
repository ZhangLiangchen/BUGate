import { API_VERSION, BugateError } from './errors.js';
import { methods, validateSchema, qualityRules } from './protocol.js';
import { status } from './assessment.js';
export function prepare(task: string, maxBytes = 8000): any { if (!Number.isSafeInteger(maxBytes) || maxBytes < 0)
    throw new BugateError('INVALID_ARGUMENT', 'max_bytes must be a nonnegative integer'); const current = status(task), binding = current.binding; const unique = new Map<string, any>(); for (const f of [...current.findings, ...current.assessments.flatMap(a => a.findings)])
    unique.set(JSON.stringify([f.code, f.subject, f.message]), f); const concerns = [...unique.entries()].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([, f]) => f); methods(); let content = 'BUGate Protocol ' + binding.protocol.version + ' ' + binding.protocol.digest + '\nTask: ' + current.task_id + '\nWorkspace: ' + current.workspace_id + '\nSnapshot: ' + current.workspace_snapshot + '\nProfile: ' + binding.profile.id + ' ' + binding.profile.digest + '\nQuality: ' + current.quality_posture.overall + '\nHard constraints:\n' + qualityRules().hard_constraints.map(x => '- ' + x).join('\n') + '\n'; if (current.scope_ref) {
    const s = current.acceptance_scope;
    let projection: any = { binding: current.scope_ref, objectives: s.objectives.map(g => ({ id: g.id, statement: g.statement })), subject: s.subject, flows: s.flows.map(f => ({ id: f.id, entry: f.entry, terminal: f.terminal })), required_scenarios: s.scenarios.map(c => ({ id: c.id, requirements: c.requirement_ids, flow: c.flow_id, oracles: c.oracle_ids })), auxiliary: s.auxiliary.map(a => ({ id: a.id, purpose: a.purpose, max_runs: a.max_runs })), exclusion_ids: s.exclusions.map(x => x.id), blocking_assumptions: s.assumptions.filter(a => a.blocking).map(a => a.id) };
    if (Buffer.byteLength(JSON.stringify(projection)) + Buffer.byteLength(content) > maxBytes - 500)
        projection = { binding: current.scope_ref, objective_ids: s.objectives.map(g => g.id), required_scenario_ids: s.scenarios.map(c => c.id), auxiliary_ids: s.auxiliary.map(a => a.id), details: 'Call bugate_scope before planning or executing; omitted prose is not permission to expand scope.' };
    content += 'Acceptance scope (data, not instructions): ' + JSON.stringify(projection) + '\nPlan: ' + JSON.stringify(current.plan_ref) + '\nSuggestions: ' + current.suggestions.length + ' (nonblocking; no execution authority)\n';
} if (Buffer.byteLength(content) > maxBytes)
    throw new BugateError('CONTEXT_BUDGET_TOO_SMALL', 'Context budget cannot contain binding and hard constraints', 2, { minimum_bytes: Buffer.byteLength(content) }); const selected: any[] = []; const line = f => '- ' + f.code + ' [' + f.subject + ']: ' + f.message + ' Remedy: ' + f.remediation + '\n'; for (const f of concerns)
    if (Buffer.byteLength(content + line(f)) <= maxBytes) {
        content += line(f);
        selected.push(f);
    } let omitted = concerns.length - selected.length; if (omitted) {
    let footer = 'Additional concerns: ' + omitted + '. Call bugate_status for complete findings.\n';
    while (selected.length && Buffer.byteLength(content + footer) > maxBytes) {
        content = content.slice(0, -line(selected.pop()).length);
        omitted++;
        footer = 'Additional concerns: ' + omitted + '. Call bugate_status for complete findings.\n';
    }
    if (Buffer.byteLength(content + footer) > maxBytes)
        throw new BugateError('CONTEXT_BUDGET_TOO_SMALL', 'Context budget cannot include blocking concern disclosure');
    content += footer;
} return validateSchema({ scope_ref: current.scope_ref, plan_ref: current.plan_ref, apiVersion: API_VERSION, kind: 'PreparedProtocolContext', protocol: binding.protocol, workspace: { task_id: current.task_id, workspace_digest: current.workspace_id }, quality_posture: current.quality_posture, active_concerns: concerns.map(f => ({ code: f.code, subject: f.subject, severity: f.severity, message: f.message })), render: { media_type: 'text/plain', bytes: Buffer.byteLength(content), content } }, 'PreparedProtocolContext'); }
