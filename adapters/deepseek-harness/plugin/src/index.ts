import { canonical, contentDigest } from '@bugate/core';
import { createHash, randomUUID } from 'node:crypto';
import { posix, win32 } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import type { Context } from '@deepseek-ai/cordis';
import type { Agent } from '@deepseek-ai/dsh-agent';
import { createUserMessage, isAgentLoopRequest, type UserMessage } from '@deepseek-ai/dsh-llm';
import type {} from '@deepseek-ai/dsh-fs';
import type {} from '@deepseek-ai/dsh-skill';
import type {} from '@deepseek-ai/dsh-subprocess';
import type {} from '@deepseek-ai/dsh-system-prompt';
import type {} from '@deepseek-ai/dsh-working-directory';
import type { ToolDefinition, ToolExecution, ToolExecutionToken, JsonSchemaNode } from '@deepseek-ai/dsh-tools';
import { Ajv2020, type ValidateFunction } from 'ajv/dist/2020.js';
import { NodeCoreService } from './core-service.js';
import { BugateService } from './service.js';
export { BugateService } from './service.js';
import type {} from '@deepseek-ai/dsh-experimental-agent-team';
import type {} from '@deepseek-ai/dsh-session';
import { BridgeError, failure, json, object, string, type CommandResult, type PreparedContext } from './contracts.js';

export const name = 'bugate';
export const inject = ['agents', 'tools', 'systemPrompt', 'fs', 'subprocess', 'skills', 'workingDirectory', 'llm', 'agentTeams', 'sessions'];
export interface ExecutionCommand {
  argv: string[]; caseIds: string[]; report?: string;
  purpose?: 'test' | 'exploration' | 'auxiliary' | 'diagnostic'; activityId?: string; environmentId?: string; timeoutSeconds?: number;
}
// Rework is produced by this plugin, never by the human. DSH source kinds are
// merge-extensible and preserved in its durable session log.
declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap { 'bugate-rework': { kind: 'bugate-rework' } }
}
export interface Config {
  nodeExecutable?: string; coreEntrypoint?: string; projectRoot?: string; taskPath?: string;
  mode?: 'advisory' | 'managed'; maxContextBytes?: number; timeoutMs?: number;
  maxOutputBytes?: number; graceMs?: number; maxRework?: number; maxReworkMs?: number;
  executionCommands?: Record<string, ExecutionCommand>;
}
interface State {
  task: string; prepared?: PreparedContext; error?: CommandResult; generation: number;
  pendingClaim?: string; attempts: number; reworkStart: number; lastGap?: string;
}
interface FileWitness { path: string; digest: string; bytes: number }
interface FilesystemManifest { version: number; task_root: string; task_files: FileWitness[]; task_directories: string[]; sources: (FileWitness & { id: string })[]; excluded_runtime_files: { exact: string[]; patterns: string[] }; excluded_runtime_directories: string[]; limits: { max_files: number; max_directories: number; max_total_bytes: number } }
interface Ticket { generation: number; epoch: number; task: string; allowed: boolean; reason?: string; scopeDigest?: string; planDigest?: string }
const envelopeSchema: JsonSchemaNode = {
  type: 'object', additionalProperties: false,
  required: ['apiVersion', 'kind', 'operation', 'request_id', 'invocation', 'data', 'errors'],
  properties: {
    apiVersion: { type: 'string', const: 'bugate.io/v2' }, kind: { type: 'string', const: 'CommandResult' },
    operation: { type: 'string' }, request_id: { type: 'string' }, invocation: { type: 'string', enum: ['completed', 'invalid_input', 'runtime_error', 'cancelled', 'timeout'] },
    data: {}, errors: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['code', 'message', 'details'], properties: { code: { type: 'string' }, message: { type: 'string' }, details: {} } } },
  },
};
function paths(path: string) { return /^[A-Za-z]:[\\/]/u.test(path) ? win32 : posix; }
function digest(value: string): string { return `sha256:${createHash('sha256').update(value).digest('hex')}`; }
function positive(value: number | undefined, fallback: number, label: string): number {
  const result = value ?? fallback;
  if (!Number.isSafeInteger(result) || result < 1) throw new BridgeError('CONFIG_INVALID', `${label} must be a positive integer`);
  return result;
}

/** Registers real DSH services. Quality decisions remain in the shared TypeScript core. */
export async function apply(ctx: Context, config: Config = {}): Promise<void> {
  const maxContextBytes = positive(config.maxContextBytes, 8000, 'maxContextBytes');
  const maxRework = positive(config.maxRework, 2, 'maxRework');
  const maxReworkMs = positive(config.maxReworkMs, 120000, 'maxReworkMs');
  const bridge = new NodeCoreService(ctx, {
    nodeExecutable: config.nodeExecutable ?? 'node', coreEntrypoint: config.coreEntrypoint, timeoutMs: positive(config.timeoutMs, 30000, 'timeoutMs'),
    maxOutputBytes: positive(config.maxOutputBytes, 1048576, 'maxOutputBytes'), graceMs: positive(config.graceMs, 1000, 'graceMs'),
  });
  const states = new Map<Agent, State>();
  const tickets = new Map<ToolExecutionToken, Ticket>();
  const epochs = new Map<string, number>();
  const leases = new Map<string, ToolExecutionToken>();
  const callTasks = new Map<ToolExecutionToken, string>();
  const callIds = new Map<ToolExecutionToken, string>();
  const bindingDigests = new Map<string, string>();
  const refreshQueues = new Map<string, Promise<void>>();
  const operations = new Set<Promise<unknown>>();
  const capturedRequests = new Set<string>();
  const settledOutputs = new Set<string>();
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  const validators = new Map<string, ValidateFunction>();
  const commands = config.executionCommands ?? {};
  for (const [id, command] of Object.entries(commands)) {
    if (!id || !Array.isArray(command.argv) || command.argv.length === 0 || command.argv.some(x => typeof x !== 'string') || !Array.isArray(command.caseIds) || command.caseIds.some(x => typeof x !== 'string') || (command.report !== undefined && typeof command.report !== 'string') || (!command.activityId && typeof command.report !== 'string')) throw new BridgeError('CONFIG_INVALID', `Invalid execution command ${id}`);
    if (command.activityId && command.caseIds.length) throw new BridgeError('CONFIG_INVALID', `Auxiliary command ${id} cannot claim acceptance cases`);
    if (command.purpose && command.purpose !== 'test' && !command.activityId) throw new BridgeError('CONFIG_INVALID', `Auxiliary/diagnostic command ${id} requires a ScopeContract activityId`);
  }

  async function read(path: string, signal?: AbortSignal): Promise<string> {
    return new TextDecoder('utf-8', { fatal: true }).decode(await ctx.fs.readBytes(await ctx.fs.resolve(path, { signal }), signal, 1048576));
  }
  async function exists(path: string, signal?: AbortSignal): Promise<boolean> {
    return (await ctx.fs.stat(await ctx.fs.resolve(path, { signal }), signal)) !== undefined;
  }
  function resource(relative: string): string {
    const mapped = ctx.fs.processPathFromHostPath(fileURLToPath(new URL(`../resources/${relative}`, import.meta.url)));
    if (!mapped) throw new BridgeError('RUNTIME_UNSUPPORTED', 'BUGate resources are not mapped into the filesystem execution world');
    return mapped;
  }
  const schemaDir = await ctx.fs.resolve(resource('protocol/schemas'));
  const schemas: Record<string, unknown>[] = [];
  for (const entry of await ctx.fs.listDir(schemaDir)) {
    if (entry.type !== 'file' || !entry.name.endsWith('.schema.json')) continue;
    const schema = object(JSON.parse(await read(ctx.fs.processPath(entry.target))));
    ajv.addSchema(schema);
    schemas.push(schema);
  }
  for (const schema of schemas) {
    const kind = object(schema.properties ?? {}).kind;
    if (kind && typeof kind === 'object') {
      const name = object(kind).const;
      if (typeof name === 'string') validators.set(name, ajv.compile(schema));
    }
  }
  const preparedSchemaValidator = validators.get('PreparedProtocolContext');
  if (!preparedSchemaValidator) throw new BridgeError('RESOURCE_MISSING', 'PreparedProtocolContext schema is missing');
  const preparedValidator = preparedSchemaValidator as ValidateFunction<PreparedContext>;
  const skill = await read(resource('SKILL.md'));
  const projection = object(JSON.parse(await read(resource('projection.json'))));
  const compatibility = object(JSON.parse(await read(resource('compatibility.json'))));
  string(compatibility.core_version, 'compatibility.core_version');
  string(compatibility.protocol_version, 'compatibility.protocol_version');
  if (projection.skill_digest !== digest(skill)) throw new BridgeError('PROJECTION_STALE', 'Bundled Skill differs from its generated projection digest');
  const protocolFiles: Record<string, string> = {};
  async function indexResources(directory: string, prefix = ''): Promise<void> {
    for (const entry of await ctx.fs.listDir(await ctx.fs.resolve(directory))) {
      const relative = prefix + entry.name;
      const path = ctx.fs.processPath(entry.target);
      if (entry.type === 'directory') await indexResources(path, relative + '/');
      else if (entry.type === 'file') protocolFiles[relative] = digest(await read(path));
      else throw new BridgeError('RESOURCE_INVALID', 'Protocol resources must be regular files and directories');
    }
  }
  await indexResources(resource('protocol'));
  const bundleDigest = digest('protocol-bundle/v1\0' + JSON.stringify(Object.fromEntries(Object.entries(protocolFiles).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))));
  if (bundleDigest !== projection.protocol_digest) throw new BridgeError('PROJECTION_STALE', 'Bundled protocol resources differ from their generated projection digest');
  ctx.skills.register({ name: 'bugate', description: 'Evidence-first business understanding, test design, execution and scoped completion assessment.', source: 'bundled', content: skill,
    resourceBase: { kind: 'directory', path: resource('protocol') } });

  function checked(result: CommandResult): CommandResult {
    const envelopeValidator = validators.get('CommandResult');
    if (envelopeValidator && !envelopeValidator(result)) throw new BridgeError('CORE_INVALID_RESPONSE', ajv.errorsText(envelopeValidator.errors));
    if (result.data && typeof result.data === 'object' && !Array.isArray(result.data)) {
      const kind = result.data.kind;
      if (typeof kind === 'string') {
        const validator = validators.get(kind);
        if (validator && !validator(result.data)) throw new BridgeError('CORE_INVALID_RESPONSE', ajv.errorsText(validator.errors));
      }
    }
    return result;
  }
  async function call(argv: string[], task: string, signal?: AbortSignal, hostContext?: Record<string, unknown>): Promise<CommandResult> {
    const pending = bridge.call([...argv, '--json'], task, signal, hostContext).then(checked);
    operations.add(pending);
    try { return await pending; } finally { operations.delete(pending); }
  }
  const service = new BugateService(ctx, (argv, task, signal) => call(argv, task, signal), maxContextBytes);
  async function verifyFilesystem(task: string, value: unknown, signal?: AbortSignal): Promise<void> {
    const validate = ajv.getSchema('https://bugate.io/v2/schemas/command_result.schema.json#/$defs/filesystem_manifest') as ValidateFunction<FilesystemManifest> | undefined;
    if (!validate || !validate(value)) throw new BridgeError('CORE_INVALID_RESPONSE', 'Core filesystem witness is missing or invalid');
    const witness = value;
    const root = await ctx.fs.resolve(task, { signal });
    if (ctx.fs.processPath(root) !== witness.task_root) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Filesystem and subprocess providers resolve different task roots');
    const expected = new Map(witness.task_files.map(file => [file.path, file]));
    const observedFiles: string[] = [], observedDirectories: string[] = [];
    const patterns = witness.excluded_runtime_files.patterns.map(pattern => new RegExp(pattern, 'u'));
    let totalBytes = 0;
    async function checkFile(target: Awaited<ReturnType<typeof ctx.fs.resolve>>, file: FileWitness): Promise<void> {
      let bytes: Uint8Array;
      try { bytes = await ctx.fs.readBytes(target, signal, Math.min(file.bytes + 1, witness.limits.max_total_bytes + 1)); }
      catch { throw new BridgeError('RUNTIME_UNSUPPORTED', `Filesystem cannot read subprocess-world file: ${file.path}`); }
      totalBytes += bytes.byteLength;
      if (totalBytes > witness.limits.max_total_bytes || bytes.byteLength !== file.bytes || `sha256:${createHash('sha256').update(bytes).digest('hex')}` !== file.digest) throw new BridgeError('RUNTIME_UNSUPPORTED', `Filesystem and subprocess file bytes differ: ${file.path}`);
    }
    async function walk(directory: Awaited<ReturnType<typeof ctx.fs.resolve>>, prefix = ''): Promise<void> {
      for (const entry of await ctx.fs.listDir(directory, signal)) {
        if (entry.name === '.' || entry.name === '..' || /[\\/]/u.test(entry.name) || !ctx.fs.contains(root, entry.target)) throw new BridgeError('PATH_ESCAPE', 'Filesystem witness contains an unsafe path');
        const relative = prefix + entry.name;
        if (entry.type === 'directory') {
          if (witness.excluded_runtime_directories.includes(relative)) continue;
          observedDirectories.push(relative);
          if (observedDirectories.length > witness.limits.max_directories) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Filesystem directory set exceeds the core witness bound');
          await walk(entry.target, relative + '/');
        } else if (entry.type === 'file') {
          if (witness.excluded_runtime_files.exact.includes(relative) || patterns.some(pattern => pattern.test(relative))) continue;
          observedFiles.push(relative);
          const file = expected.get(relative);
          if (!file || observedFiles.length + witness.sources.length > witness.limits.max_files) throw new BridgeError('RUNTIME_UNSUPPORTED', `Filesystem has a file absent from the subprocess witness: ${relative}`);
          await checkFile(entry.target, file);
        } else throw new BridgeError('PATH_ESCAPE', `Filesystem witness does not permit symbolic links or special files: ${relative}`);
      }
    }
    await walk(root);
    if (!isDeepStrictEqual(observedFiles.sort(), [...expected.keys()].sort()) || !isDeepStrictEqual(observedDirectories.sort(), [...witness.task_directories].sort())) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Filesystem and subprocess providers expose different task file or directory sets');
    for (const source of witness.sources) {
      const target = await ctx.fs.resolve(source.path, { signal });
      if ((await ctx.fs.lstat(source.path, {}, signal))?.type !== 'file') throw new BridgeError('PATH_ESCAPE', 'Profile source must be a regular file in the configured world');
      await checkFile(target, source);
    }
  }
  async function contained(task: string, file: string, signal?: AbortSignal): Promise<string> {
    const root = await ctx.fs.resolve(task, { signal });
    const target = await ctx.fs.resolve(file, { cwd: ctx.fs.processPath(root), signal });
    if (!ctx.fs.contains(root, target)) throw new BridgeError('PATH_ESCAPE', 'Requested file is outside the bound task');
    const path = ctx.fs.processPath(target);
    const stat = await ctx.fs.lstat(file, { cwd: task }, signal);
    if (stat?.type === 'symlink') throw new BridgeError('PATH_ESCAPE', 'Symbolic link inputs are not accepted');
    return path;
  }
  async function discover(agent: Agent, signal?: AbortSignal): Promise<string | undefined> {
    const cwd = ctx.workingDirectory.get(agent.session);
    if (!cwd) return undefined;
    const initial = await ctx.fs.resolve(cwd, { signal });
    if (config.projectRoot) {
      const project = await ctx.fs.resolve(config.projectRoot, { signal });
      if (!ctx.fs.contains(project, initial)) return undefined;
    }
    if (config.taskPath) {
      const path = await contained(config.projectRoot ?? cwd, config.taskPath, signal);
      if (!await exists(paths(path).join(path, '.bugate/protocol.lock.json'), signal)) throw new BridgeError('BINDING_MISSING', 'Configured task has no exact protocol binding');
      return path;
    }
    let cursor = ctx.fs.processPath(initial);
    for (let depth = 0; depth < 64; depth++) {
      const p = paths(cursor);
      if (await exists(p.join(cursor, '.bugate/protocol.lock.json'), signal)) return cursor;
      if (await exists(p.join(cursor, 'task.json'), signal)) {
        const taskDocument = object(JSON.parse(await read(p.join(cursor, 'task.json'), signal)));
        if (taskDocument.kind === 'TestTask' && taskDocument.apiVersion === 'bugate.io/v2') throw new BridgeError('BINDING_MISSING', 'A BUGate task lost its exact protocol binding');
      }
      const tasksPath = p.join(cursor, 'test-task');
      if (await exists(tasksPath, signal)) {
        const candidates: string[] = [];
        for (const entry of await ctx.fs.listDir(await ctx.fs.resolve(tasksPath, { signal }), signal)) {
          if (entry.type !== 'directory') continue;
          const candidate = ctx.fs.processPath(entry.target);
          if (await exists(p.join(candidate, '.bugate/protocol.lock.json'), signal)) candidates.push(candidate);
        }
        if (candidates.length > 1) throw new BridgeError('TASK_AMBIGUOUS', `Select taskPath explicitly: ${JSON.stringify(candidates)}`);
        if (candidates.length === 1) return candidates[0];
      }
      if (config.projectRoot && cursor === ctx.fs.processPath(await ctx.fs.resolve(config.projectRoot, { signal }))) break;
      const parent = p.dirname(cursor);
      if (parent === cursor) break;
      cursor = parent;
    }
    return undefined;
  }
  async function settleOutputs(agent: Agent, task: string, signal?: AbortSignal): Promise<void> {
    const events = agent.session.snapshotEvents();
    const outputCalls = new Set(events.flatMap(event => event.type === 'assistant/message' && event.seq >= agent.session.inheritedEventCount ? event.data.message.content.flatMap(block => block.type === 'tool-call' && block.name === 'bugate_publish_artifact' ? [String(block.id)] : []) : []));
    const results = events.filter(event => event.type === 'tool/result' && event.seq >= agent.session.inheritedEventCount && outputCalls.has(String(event.data.message.toolCallId)) && !settledOutputs.has(String(agent.session.id) + ':' + event.seq));
    if (!results.length) return;
    if (!await ctx.sessions.flush(agent.session)) throw new BridgeError('HOST_PROVENANCE_REQUIRED', 'Review settlement requires durable DSH session persistence');
    for (const event of results) {
      if (event.type !== 'tool/result') continue;
      signal?.throwIfAborted();
      const result = await call(['artifact', 'settle', '--task', task], task, signal, { host: 'deepseek-harness', host_version: '0.2.2-alpha.2', settlement: { session_id: String(agent.session.id), call_id: String(event.data.message.toolCallId), result_event: event } });
      if (result.invocation !== 'completed') throw new BridgeError('REVIEW_NOT_COMPLETED', JSON.stringify(result.errors));
      settledOutputs.add(String(agent.session.id) + ':' + event.seq);
    }
  }
  async function refresh(agent: Agent, signal?: AbortSignal): Promise<State | undefined> {
    const previous = states.get(agent);
    if (previous) { previous.prepared = undefined; previous.generation++; }
    let task: string | undefined;
    try { task = await discover(agent, signal); }
    catch (error) { if (previous) previous.error = failure('protocol.prepare', error); throw error; }
    if (!task) { states.delete(agent); return undefined; }
    await settleOutputs(agent, task, signal);
    // The provider may implement createIfAbsent through a temporary directory.
    // Serialize our own full task refresh/pin writes so sibling hydration never
    // mistakes that owned atomic staging directory for a different execution world.
    const predecessor = refreshQueues.get(task);
    let release: () => void = () => undefined;
    const completion = new Promise<void>(resolve => { release = resolve; });
    refreshQueues.set(task, completion);
    try {
    if (predecessor) await predecessor;
    signal?.throwIfAborted();
    const state = states.get(agent) ?? { task, generation: 0, attempts: 0, reworkStart: 0 };
    states.set(agent, state);
    state.task = task;
    const startingEpoch = epochs.get(task) ?? 0;
    state.prepared = undefined;
    state.generation++;
    try {
      const handshake = await call(['version'], task, signal);
      if (handshake.invocation !== 'completed') throw new BridgeError('DEPENDENCY_MISSING', JSON.stringify(handshake.errors));
      const version = object(handshake.data);
      if (version.core_version !== compatibility.core_version || version.protocol_version !== compatibility.protocol_version) throw new BridgeError('VERSION_MISMATCH', 'Installed core/protocol differs from adapter compatibility target');
      if (version.protocol_digest !== projection.protocol_digest) throw new BridgeError('PROJECTION_STALE', 'Bundled Skill/Schema projection differs from the installed canonical protocol');
      const lock = await read(paths(task).join(task, '.bugate/protocol.lock.json'), signal);
      let filesystemBinding: Record<string, unknown>;
      try { filesystemBinding = object(JSON.parse(lock)); }
      catch { throw new BridgeError('SCHEMA_INVALID', 'Filesystem provider returned invalid protocol binding JSON'); }
      const bindingValidator = validators.get('ProtocolBinding');
      if (!bindingValidator || !bindingValidator(filesystemBinding)) throw new BridgeError('SCHEMA_INVALID', 'Filesystem provider returned an invalid ProtocolBinding');
      const currentBinding = digest(lock);
      const priorBinding = bindingDigests.get(task);
      if (priorBinding && priorBinding !== currentBinding) throw new BridgeError('BINDING_DIGEST_MISMATCH', 'Binding changed during a live adapter scope; restart after explicit migration');
      const sessionBindingPath = paths(task).join(task, '.bugate', 'runtime', 'deepseek-harness', `dsh-session-${createHash('sha256').update(String(agent.id)).digest('hex')}.json`);
      const sessionBinding = JSON.stringify({ session_id: String(agent.id), task, binding_digest: currentBinding });
      if (await exists(sessionBindingPath, signal)) {
        if (await read(sessionBindingPath, signal) !== sessionBinding) throw new BridgeError('BINDING_DIGEST_MISMATCH', 'Persisted session binding differs; cold resume cannot silently change protocol or Profile');
      }
      const result = await service.prepare(task, signal);
      if (result.invocation !== 'completed' || !preparedValidator(result.data)) throw new BridgeError('CONTEXT_UNAVAILABLE', JSON.stringify(result.errors));
      const value = result.data;
      const current = await service.status(task, signal);
      if (current.invocation !== 'completed') throw new BridgeError('CONTEXT_UNAVAILABLE', JSON.stringify(current.errors));
      const coreStatus = object(current.data);
      if (!isDeepStrictEqual(coreStatus.binding, filesystemBinding) || coreStatus.task_id !== value.workspace.task_id || coreStatus.workspace_id !== value.workspace.workspace_digest) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Filesystem and subprocess providers disagree about the exact task binding; configure one execution world');
      await verifyFilesystem(task, coreStatus.filesystem_manifest, signal);
      if (await read(paths(task).join(task, '.bugate/protocol.lock.json'), signal) !== lock) throw new BridgeError('CONCURRENT_CONFLICT', 'Task binding changed during context preparation');
      if (Buffer.byteLength(value.render.content, 'utf8') !== value.render.bytes || value.render.bytes > maxContextBytes) throw new BridgeError('CONTEXT_BUDGET', 'Prepared context UTF-8 byte count is invalid');
      if (value.protocol.digest !== version.protocol_digest) throw new BridgeError('BINDING_DIGEST_MISMATCH', 'Prepared protocol digest differs from installed core');
      if (!await exists(sessionBindingPath, signal)) {
        try { await ctx.fs.writeText(await ctx.fs.resolve(sessionBindingPath, { signal }), sessionBinding, { kind: 'createIfAbsent' }, signal); }
        catch (error) { if (await read(sessionBindingPath, signal) !== sessionBinding) throw new BridgeError('CONCURRENT_CONFLICT', error instanceof Error ? error.message : String(error)); }
      }
      if ((epochs.get(task) ?? 0) !== startingEpoch) throw new BridgeError('CONCURRENT_CONFLICT', 'Task changed during context preparation; retry with current facts');
      bindingDigests.set(task, currentBinding);
      state.prepared = value;
      state.error = undefined;
      return state;
    } catch (error) { state.error = failure('protocol.prepare', error); throw error; }
    } finally { release(); if (refreshQueues.get(task) === completion) refreshQueues.delete(task); }
  }
  async function requireState(agent: Agent | undefined, signal?: AbortSignal): Promise<State> {
    if (!agent) throw new BridgeError('TASK_IDENTITY_REQUIRED', 'BUGate tools require a host-owned Agent identity');
    const state = await refresh(agent, signal);
    if (!state) throw new BridgeError('BINDING_MISSING', 'This Agent is outside a bound BUGate task');
    return state;
  }

  async function captureRequests(agent: Agent, messages: readonly UserMessage[], signal?: AbortSignal): Promise<void> {
    // DSH 0.2.2-alpha.2 tags the initial model-authored subagent prompt as
    // source.kind=user. Its persisted origin disambiguates delegation; treating
    // that prompt as human authorization would permit scope forgery by spawn.
    if (agent.session.header.origin === 'subagent') return;
    const task = await discover(agent, signal);
    if (!task) return;
    for (const message of messages) {
      if (message.source.kind !== 'user') continue;
      const text = message.content.filter(block => block.type === 'text').map(block => block.text).join('\n');
      if (!text.trim()) continue;
      const id = `dsh-request-${createHash('sha256').update(`${agent.session.id}:${message.id}`).digest('hex').slice(0, 32)}`;
      const key = `${task}:${id}`;
      if (capturedRequests.has(key)) continue;
      const input = paths(task).join(task, '.bugate', 'runtime', 'deepseek-harness', `${id}.json`);
      const receipt = JSON.stringify({ id, message: text, origin: 'user', session_id: String(agent.session.id), source_event_id: String(message.id) });
      if (!await exists(input, signal)) {
        try { await ctx.fs.writeText(await ctx.fs.resolve(input, { signal }), receipt, { kind: 'createIfAbsent' }, signal); }
        catch (error) { if (await read(input, signal) !== receipt) throw new BridgeError('CONCURRENT_CONFLICT', error instanceof Error ? error.message : String(error)); }
      } else if (await read(input, signal) !== receipt) throw new BridgeError('SCOPE_REQUEST_CONFLICT', 'Host request identity already has different content');
      await ctx.sessions.flush(agent.session);
      const result = await call(['scope', 'request', '--task', task, '--input', input], task, signal, { host: 'deepseek-harness', host_version: '0.2.2-alpha.2', user_request: { session_id: String(agent.session.id), source_event_id: String(message.id), message: text, origin: 'user' } });
      if (result.invocation !== 'completed') throw new BridgeError('SCOPE_REQUEST_CAPTURE_FAILED', JSON.stringify(result.errors));
      capturedRequests.add(key);
    }
  }

  function authorizationArgs(command: ExecutionCommand, task: string): string[] {
    const argv = ['execution', 'authorize', '--task', task];
    if (command.activityId) argv.push('--activity-id', command.activityId);
    if (command.environmentId) argv.push('--environment-id', command.environmentId);
    for (const caseId of command.caseIds) argv.push('--case', caseId);
    return argv;
  }
  async function authorize(command: ExecutionCommand, task: string, signal?: AbortSignal): Promise<{ scopeDigest: string; planDigest?: string }> {
    const result = await call(authorizationArgs(command, task), task, signal);
    if (result.invocation !== 'completed') throw new BridgeError('SCOPE_ACTIVITY_DENIED', JSON.stringify(result.errors));
    const value = object(result.data);
    if (value.allowed !== true) throw new BridgeError('SCOPE_ACTIVITY_DENIED', JSON.stringify(value.findings ?? value));
    return { scopeDigest: string(object(value.scope_ref).digest, 'scope_ref.digest'), ...(value.plan_ref ? { planDigest: string(object(value.plan_ref).digest, 'plan_ref.digest') } : {}) };
  }

  ctx.systemPrompt.section({ name: 'bugate:bootstrap', order: 850, interpolate: false, text: ({ agent }) => agent && states.has(agent) ? 'BUGate is enabled. Use the exact bound protocol and current task facts. Completion claims require current scoped assessment; tool success is not test success.' : '' });
  ctx.systemPrompt.context({ name: 'bugate:protocol', order: 850, required: true, interpolate: false, text: ({ agent }) => {
    const state = agent ? states.get(agent) : undefined;
    return state?.prepared?.render.content ?? (state?.error ? `BUGate context unavailable: ${JSON.stringify(state.error.errors)}. Previous quality conclusions are invalid. Repair the binding or runtime.` : '');
  } });
  ctx.on('agent/created', async ({ agent, signal }) => {
    const messages = agent.session.snapshotEvents().flatMap(event => event.type === 'user/message' && event.seq >= agent.session.inheritedEventCount ? [event.data] : []);
    await captureRequests(agent, messages, signal);
    await refresh(agent, signal); return undefined;
  });
  ctx.on('agent/pre-step', async ({ agent, messages, signal }, next) => { await captureRequests(agent, messages, signal); await refresh(agent, signal); return next(); });
  ctx.on('agent/request', async ({ agent, signal }, next) => {
    const request = await next();
    await refresh(agent, signal);
    return request;
  });
  // This real DSH waterfall runs after request admission and before the model.
  // It never edits the immutable request. A race after the awaited refresh is
  // rejected, so a model cannot receive missing or stale governance context.
  ctx.on('llm/stream', async function* (options, next) {
    if (isAgentLoopRequest(options)) {
      const entry = [...states.entries()].find(([agent]) => agent.session.id === options.sessionId);
      if (entry) {
        const state = entry[1];
        // The published agent-loop index omits its runtime-context source augmentation.
        // Compare the documented runtime tag as text, without asserting a false union type.
        const current = options.messages.findLast(message => String(message.source?.kind) === 'runtime-context');
        const text = current?.content.filter(block => block.type === 'text').map(block => block.text).join('\n') ?? '';
        if (!state.prepared || !text.includes(state.prepared.render.content)) throw new BridgeError('CONTEXT_INVALIDATED', 'Task facts changed during model admission; no model request was sent. Retry after current protocol preparation.');
      }
    }
    yield* next();
  });
  ctx.on('agent/disposed', ({ agent }) => { states.delete(agent); });

  function managed(exec: ToolExecution): boolean {
    return ['write', 'edit', 'str_replace_editor', 'bash', 'bugate_run_test', 'bugate_assess', 'bugate_record_evidence', 'bugate_record_artifact', 'bugate_publish_artifact', 'bugate_record_scope', 'bugate_record_suggestion'].includes(exec.name);
  }
  ctx.on('tools/pre-execute', async (exec, next) => {
    callIds.set(exec.token, `${exec.agent?.id ?? 'agentless'}:${exec.callId}`);
    if (!exec.agent || !managed(exec) || config.mode === 'advisory') return next();
    const task = await discover(exec.agent, exec.signal);
    if (!task) return next();
    callTasks.set(exec.token, task);
    const epoch = epochs.get(task) ?? 0;
    if (leases.has(task)) {
      tickets.set(exec.token, { task, epoch, generation: states.get(exec.agent)?.generation ?? -1, allowed: false, reason: 'CONCURRENT_CONFLICT: another managed operation owns this task; retry after it settles' });
      return next();
    }
    leases.set(task, exec.token);
    try {
      const state = await requireState(exec.agent, exec.signal);
      const args = object(exec.arguments);
      let needsDesign = true;
      let authorization: { scopeDigest: string; planDigest?: string } | undefined;
      if (exec.name === 'bash') throw new BridgeError('UNCLASSIFIED_MANAGED_WRITE', 'Use a configured bugate_run_test exploration command; arbitrary shell writes are outside the managed policy');
      if (exec.name === 'bugate_assess' || exec.name === 'bugate_record_evidence' || exec.name === 'bugate_record_artifact' || exec.name === 'bugate_publish_artifact' || exec.name === 'bugate_record_scope' || exec.name === 'bugate_record_suggestion') {
        needsDesign = false;
      } else if (exec.name === 'bugate_run_test') {
        const command = commands[string(args.command_id, 'command_id')];
        if (!command) throw new BridgeError('COMMAND_NOT_CONFIGURED', 'Unknown host-configured command');
        authorization = await authorize(command, task, exec.signal);
        needsDesign = !command.activityId;
      } else {
        if (exec.name === 'str_replace_editor' && args.command === 'view') needsDesign = false;
        const target = await contained(task, string(args.file_path ?? args.path, 'file_path'), exec.signal);
        const relative = paths(task).relative(task, target).replaceAll('\\', '/');
        if (/^(artifacts|claims|evidence-input|\.bugate\/drafts)\//u.test(relative)) needsDesign = false;
        else if (!/^(implementation|tests)\//u.test(relative) && needsDesign) throw new BridgeError('PATH_NOT_MANAGED', 'Writes must target task artifacts, claims, evidence-input, .bugate/drafts, implementation or tests');
      }
      if (needsDesign) {
        const status = await service.status(task, exec.signal);
        if (status.invocation !== 'completed' || object(status.data).implementation_ready !== true) throw new BridgeError('DESIGN_NOT_ACCEPTED', 'A current satisfied test_design_complete claim is required before implementation or governed tests');
      }
      tickets.set(exec.token, { task, epoch, generation: state.generation, allowed: true, ...authorization });
    } catch (error) {
      tickets.set(exec.token, { task, epoch, generation: states.get(exec.agent)?.generation ?? -1, allowed: false, reason: error instanceof Error ? error.message : String(error) });
    }
    return next();
  });
  ctx.tools.guard(exec => {
    if (!exec.agent || !states.has(exec.agent) || !managed(exec) || config.mode === 'advisory') return undefined;
    const ticket = tickets.get(exec.token);
    if (!ticket?.allowed) return `BUGate: ${ticket?.reason ?? 'missing awaited assessment ticket'}`;
    if (leases.get(ticket.task) !== exec.token || (epochs.get(ticket.task) ?? 0) !== ticket.epoch) return 'BUGate: task changed or its managed-operation lease was lost; retry after refresh';
    if (ticket.generation !== states.get(exec.agent)?.generation) return 'BUGate: assessment changed before dispatch; retry after refresh';
    return undefined;
  });
  // The result event only invalidates synchronous state. Critical evidence is
  // awaited inside execution run, before its tool result is returned.
  ctx.on('tools/result', (exec) => {
    const ticket = tickets.get(exec.token);
    tickets.delete(exec.token);
    callIds.delete(exec.token);
    const task = callTasks.get(exec.token);
    callTasks.delete(exec.token);
    if (task && leases.get(task) === exec.token) {
      leases.delete(task);
      if (!ticket?.allowed) return;
      epochs.set(task, (epochs.get(task) ?? 0) + 1);
      for (const state of states.values()) if (state.task === task) { state.prepared = undefined; state.error = failure('protocol.prepare', new BridgeError('CONTEXT_INVALIDATED', 'A managed task operation settled; refresh current facts before relying on prior quality conclusions')); state.generation++; }
    }
  });

  function register(toolName: string, description: string, fields: Record<string, JsonSchemaNode>, required: string[], run: (args: Record<string, unknown>, exec: ToolExecution, state: State) => Promise<CommandResult>): void {
    const parameters = { type: 'object' as const, additionalProperties: false, properties: fields, required };
    const validateArguments = ajv.compile(parameters);
    const definition: ToolDefinition = { name: toolName, description, parameters,
      output: { schema: envelopeSchema, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        try {
          if (!validateArguments(args)) throw new BridgeError('SCHEMA_INVALID', ajv.errorsText(validateArguments.errors));
          return json(await run(object(args), exec, await requireState(exec.agent, exec.signal)));
        }
        catch (error) { return json(failure(toolName, error)); }
      } };
    ctx.tools.register(definition);
  }
  register('bugate_prepare', 'Prepare the current exact-bound quality protocol context.', {}, [], async (_args, _exec, state) => ({ apiVersion: 'bugate.io/v2', kind: 'CommandResult', operation: 'protocol.prepare', request_id: randomUUID(), invocation: 'completed', data: json(state.prepared), errors: [] }));
  register('bugate_status', 'Read derived quality facts and assessment freshness.', {}, [], async (_args, exec, state) => service.status(state.task, exec.signal));
  register('bugate_validate', 'Validate current task structure, references and deterministic rules.', {}, [], async (_args, exec, state) => call(['validate', '--task', state.task], state.task, exec.signal));
  register('bugate_scope', 'Read the active acceptance scope, its source receipts and history. Scope is the finite user objective, not a default test matrix.', {}, [], async (_args, exec, state) => call(['scope', 'show', '--task', state.task], state.task, exec.signal));
  register('bugate_check_scope', 'Check the current scope and plan references, conflicts and source gaps; structural validity does not establish semantic necessity.', {}, [], async (_args, exec, state) => call(['scope', 'check', '--task', state.task], state.task, exec.signal));
  register('bugate_record_scope', 'Record a source-backed acceptance scope draft or an explicitly sourced revision. User authorization must reference host-captured requests; an agent statement of consent is not authority.', { input: { type: 'string' }, expected_digest: { type: 'string' } }, ['input'], async (args, exec, state) => {
    const input = await contained(state.task, string(args.input, 'input'), exec.signal);
    const argv = ['scope', 'record', '--task', state.task, '--input', input];
    if (args.expected_digest !== undefined) argv.push('--expected-digest', string(args.expected_digest, 'expected_digest'));
    const result = await call(argv, state.task, exec.signal);
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  register('bugate_record_suggestion', 'Record a non-executable out-of-scope suggestion. Suggestions neither authorize tests nor block acceptance completion; do not expand them into test plans.', { input: { type: 'string' } }, ['input'], async (args, exec, state) => {
    const input = await contained(state.task, string(args.input, 'input'), exec.signal);
    const result = await call(['suggestion', 'record', '--task', state.task, '--input', input], state.task, exec.signal);
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  register('bugate_assess', 'Assess a persisted task-local claim; submit=true asks for bounded completion rework.', { claim: { type: 'string' }, submit: { type: 'boolean' } }, ['claim'], async (args, exec, state) => {
    const claim = await contained(state.task, string(args.claim, 'claim'), exec.signal);
    const result = await service.assess(state.task, claim, exec.signal);
    if (args.submit === true) { state.pendingClaim = claim; state.attempts = 0; state.reworkStart = Date.now(); state.lastGap = undefined; }
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  async function observedExecution(exec: ToolExecution): Promise<Record<string, unknown>> {
    if (!exec.agent) throw new BridgeError('HOST_PROVENANCE_REQUIRED', 'A live Team member must execute the output tool');
    const agent = exec.agent;
    const membership = ctx.agentTeams.membership(agent);
    const member = ctx.agentTeams.listMembers(agent).find(row => String(row.id) === String(agent.session.id));
    if (!member || (member.status !== 'running' && member.status !== 'inactive')) throw new BridgeError('HOST_PROVENANCE_REQUIRED', 'The executing Agent is not an active official Team member');
    const events = agent.session.snapshotEvents();
    const event = events.findLast(value => value.type === 'assistant/message' && value.seq >= agent.session.inheritedEventCount && !value.data.interrupted && value.data.message.content.some(block => block.type === 'tool-call' && String(block.id) === String(exec.callId) && block.name === exec.name));
    if (!event || event.type !== 'assistant/message') throw new BridgeError('HOST_PROVENANCE_REQUIRED', 'No actual model tool output in this member session matches the execution');
    const block = event.data.message.content.find(value => value.type === 'tool-call' && String(value.id) === String(exec.callId));
    if (!block || block.type !== 'tool-call' || !isDeepStrictEqual(JSON.parse(block.arguments), exec.arguments)) throw new BridgeError('HOST_PROVENANCE_REQUIRED', 'Observed model arguments differ from dispatched output');
    const sessionEvent = { seq: event.seq, time: event.time, turn: event.data.turn, step: event.data.step, tool_call: { ...block, arguments: JSON.parse(block.arguments), arguments_json: block.arguments } };
    const messages = events.flatMap(value => {
      if (value.type !== 'user/message' || value.seq < agent.session.inheritedEventCount || String(value.data.source.kind) !== 'agent-message') return [];
      const source = value.data.source as unknown as Record<string, unknown>;
      if (typeof source.senderSessionId !== 'string') return [];
      return [{ message_id: String(value.data.id), sender_session_id: source.senderSessionId, content_digest: contentDigest(canonical(value.data.content)), content: value.data.content, event_seq: value.seq }];
    });
    // Ordinary Agent inbox acceptance is not a persistence barrier. Commit the
    // original model output and received-message observations before core receipt publication.
    await ctx.sessions.flush(agent.session);
    exec.signal.throwIfAborted();
    return { host: 'deepseek-harness', host_version: '0.2.2-alpha.2', execution: {
      agent_id: String(agent.id), session_id: String(agent.session.id), team_id: String(membership.id), member_name: membership.name, member_role: membership.role,
      context_mode: membership.role === 'lead' ? 'lead' : member.context,
      parent_session_id: agent.session.header.parentSession ? String(agent.session.header.parentSession) : null,
      inherited_event_count: agent.session.inheritedEventCount, call_id: String(exec.callId), root_call_id: String(exec.rootCallId), tool_name: exec.name,
      arguments_digest: contentDigest(canonical(exec.arguments)), arguments: exec.arguments,
      session_event_digest: contentDigest(canonical(sessionEvent)), session_event: sessionEvent,
    }, messages };
  }
  register('bugate_publish_artifact', 'Publish this actual Team member tool output as a quality artifact. Inline content and exact live Team execution are durably observed; quality authorization additionally awaits final host tool-result settlement. File imports and names cannot establish authorship or independent review.', { artifact: { type: 'object' }, expected_digest: { type: 'string' } }, ['artifact'], async (args, exec, state) => {
    const observation = await observedExecution(exec);
    const artifact = object(args.artifact);
    // The core authoring operation supplies computed binding/digest fields.
    if (artifact.kind !== 'Artifact' || artifact.apiVersion !== 'bugate.io/v2') throw new BridgeError('SCHEMA_INVALID', 'Inline output must be an Artifact draft');
    observation.artifact = artifact;
    const argv = ['artifact', 'publish', '--task', state.task];
    if (args.expected_digest !== undefined) argv.push('--expected-digest', string(args.expected_digest, 'expected_digest'));
    const result = await call(argv, state.task, exec.signal, observation);
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  register('bugate_record_artifact', 'Validate and atomically register a task-local draft; the shared core computes content digests and enforces compare-and-swap updates.', { input: { type: 'string' }, expected_digest: { type: 'string' } }, ['input'], async (args, exec, state) => {
    const input = await contained(state.task, string(args.input, 'input'), exec.signal);
    const argv = ['artifact', 'record', '--task', state.task, '--input', input];
    if (args.expected_digest !== undefined) argv.push('--expected-digest', string(args.expected_digest, 'expected_digest'));
    const result = await call(argv, state.task, exec.signal);
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  register('bugate_record_evidence', 'Import task-local material; reported execution fields cannot become host observations.', { input: { type: 'string' } }, ['input'], async (args, exec, state) => {
    const input = await contained(state.task, string(args.input, 'input'), exec.signal);
    const result = await call(['evidence', 'record', '--task', state.task, '--input', input], state.task, exec.signal);
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  register('bugate_run_test', 'Run a host-configured required scenario or a scope-declared auxiliary/diagnostic activity. The core checks the exact current scope and plan and durably preserves actual failures before returning.', { command_id: { type: 'string' } }, ['command_id'], async (args, exec, state) => {
    const command = commands[string(args.command_id, 'command_id')];
    if (!command) throw new BridgeError('COMMAND_NOT_CONFIGURED', 'Command must be configured by the host');
    const ticket = tickets.get(exec.token);
    const authorization = ticket?.scopeDigest ? { scopeDigest: ticket.scopeDigest, planDigest: ticket.planDigest } : await authorize(command, state.task, exec.signal);
    const report = command.report ? await contained(state.task, command.report, exec.signal) : undefined;
    const runId = `dsh-${createHash('sha256').update(`${exec.agent?.id}:${exec.rootCallId}:${exec.callId}`).digest('hex').slice(0, 32)}`;
    const argv = ['execution', 'run', '--task', state.task, '--run-id', runId, '--agent-id', String(exec.agent?.id), '--session-id', String(exec.agent?.session.id), '--call-id', callIds.get(exec.token) ?? String(exec.callId), '--root-call-id', String(exec.rootCallId), '--timeout', String(command.timeoutSeconds ?? 20)];
    if (report) argv.push('--report', report);
    const parent = exec.parent ? callIds.get(exec.parent) : undefined;
    if (exec.parent && !parent) throw new BridgeError('CALL_LINEAGE_MISSING', 'Nested parent identity is unavailable');
    if (parent) argv.push('--parent-call-id', parent);
    argv.push('--expected-scope-digest', authorization.scopeDigest);
    if (authorization.planDigest) argv.push('--expected-plan-digest', authorization.planDigest);
    if (command.activityId) argv.push('--activity-id', command.activityId);
    if (command.environmentId) argv.push('--environment-id', command.environmentId);
    for (const caseId of command.caseIds) argv.push('--case', caseId);
    const result = checked(await bridge.call([...argv, '--json', '--', ...command.argv], state.task, exec.signal));
    if (exec.agent) await refresh(exec.agent, exec.signal);
    return result;
  });
  ctx.on('agent/turn-stopping', async ({ agent, signal }) => {
    const task = await discover(agent, signal);
    if (task && !signal.aborted) await settleOutputs(agent, task, signal);
    const state = states.get(agent);
    if (!state?.pendingClaim || signal.aborted) return;
    await Promise.all([...operations]);
    const result = await service.assess(state.task, state.pendingClaim, signal);
    const data = result.data && typeof result.data === 'object' ? object(result.data) : {};
    if (data.verdict === 'satisfied') { state.pendingClaim = undefined; return; }
    const gap = digest(JSON.stringify([result.errors, data.findings ?? []]));
    if (state.attempts >= maxRework || Date.now() - state.reworkStart >= maxReworkMs || state.lastGap === gap || signal.aborted) { state.pendingClaim = undefined; return; }
    state.attempts++; state.lastGap = gap;
    agent.steer(createUserMessage({ source: { kind: 'bugate-rework' }, content: [{ type: 'text', text: `BUGate completion claim is not satisfied. Repair only missing, invalid or inconsistent evidence within the current acceptance scope, or report BLOCKED/INCOMPLETE. Out-of-scope suggestions do not require execution or user decisions. Preserve real failures; execution completion does not mean requirements passed. Rework ${state.attempts}/${maxRework}: ${JSON.stringify(result)}` }] }));
  });
  ctx.effect(() => async () => { await bridge.dispose(); await Promise.allSettled([...operations]); states.clear(); tickets.clear(); callIds.clear(); bindingDigests.clear(); refreshQueues.clear(); epochs.clear(); leases.clear(); callTasks.clear(); capturedRequests.clear(); settledOutputs.clear(); });
}
