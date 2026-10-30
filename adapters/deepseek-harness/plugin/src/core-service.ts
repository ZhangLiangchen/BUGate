import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { posix, win32 } from 'node:path';
import { canonical, contentDigest } from '@bugate/core';
import { fileURLToPath } from 'node:url';
import type { Context } from '@deepseek-ai/cordis';
import type {} from '@deepseek-ai/dsh-fs';
import type {} from '@deepseek-ai/dsh-subprocess';
import type { SubprocessHandle } from '@deepseek-ai/dsh-subprocess';
import { BridgeError, json, object, string, type CommandResult } from './contracts.js';

export interface CoreServiceConfig { nodeExecutable: string; coreEntrypoint?: string; timeoutMs: number; maxOutputBytes: number; graceMs: number }

/** Calls the installed core in the providers' execution world; no local fallback. */
export class NodeCoreService {
  private readonly live = new Set<SubprocessHandle>();
  private readonly disposed = new AbortController();
  private readonly authority = generateKeyPairSync('ed25519');
  private readonly publicKey = this.authority.publicKey.export({ type: 'spki', format: 'pem' }).toString();
  private readonly authorityId = 'authority-' + createHash('sha256').update(this.publicKey).digest('hex');
  private readonly pinned = new Set<string>();
  constructor(private readonly ctx: Context, private readonly config: CoreServiceConfig) {}

  async call(argv: readonly string[], cwd: string, signal?: AbortSignal, hostContext?: Record<string, unknown>): Promise<CommandResult> {
    const deadline = AbortSignal.timeout(this.config.timeoutMs);
    const combined = AbortSignal.any([deadline, this.disposed.signal, ...(signal ? [signal] : [])]);
    let handle: SubprocessHandle | undefined;
    try {
      combined.throwIfAborted();
      const target = await this.ctx.fs.resolve(cwd, { signal: combined });
      const processCwd = this.ctx.fs.processPath(target);
      const command = this.config.nodeExecutable;
      const mapped = command.includes('/') || command.includes('\\') ? this.ctx.fs.processPathFromHostPath(command) : command;
      if (!mapped) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Node installation is not mapped into this filesystem execution world');
      const executable = await this.ctx.subprocess.resolveExecutable(mapped, {}, combined);
      const entry = this.config.coreEntrypoint ?? fileURLToPath(import.meta.resolve('@bugate/cli'));
      const mappedEntry = this.ctx.fs.processPathFromHostPath(entry);
      if (!mappedEntry) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Installed Node CLI is not mapped into the filesystem execution world');
      let observed = hostContext;
      if (hostContext) {
        const paths = /^[A-Za-z]:[\\/]/u.test(processCwd) ? win32 : posix;
        const taskTarget = await this.ctx.fs.resolve(paths.join(processCwd, 'task.json'), { signal: combined });
        const task = object(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(await this.ctx.fs.readBytes(taskTarget, combined, 1048576))));
        const workspaceId = string(task.workspace_id, 'task.workspace_id');
        const pinPath = paths.join(processCwd, '.bugate', 'host-authorities', this.authorityId + '.json');
        const pin = JSON.stringify({ version: 1, host: 'deepseek-harness', authority_id: this.authorityId, public_key: this.publicKey });
        if (!this.pinned.has(processCwd)) {
          const pinTarget = await this.ctx.fs.resolve(pinPath, { signal: combined });
          try { await this.ctx.fs.writeText(pinTarget, pin, { kind: 'createIfAbsent' }, combined); }
          catch (error) { if (new TextDecoder().decode(await this.ctx.fs.readBytes(pinTarget, combined, 1048576)) !== pin) throw error; }
          this.pinned.add(processCwd);
        }
        const operation = ['protocol', 'evidence', 'execution', 'artifact', 'scope', 'suggestion'].includes(argv[0] ?? '') ? argv[0] + '.' + argv[1] : argv[0];
        const authentication = { authority_id: this.authorityId, operation, task_root: processCwd, workspace_id: workspaceId, context_digest: contentDigest(canonical(hostContext)) };
        observed = { ...hostContext, authentication: { ...authentication, signature: sign(null, canonical({ version: 1, ...authentication }), this.authority.privateKey).toString('base64') } };
      }
      const commandArgv = [executable, mappedEntry, ...argv, ...(hostContext ? ['--host-observation-stdin'] : [])];
      handle = this.ctx.subprocess.spawn({ argv: commandArgv, cwd: processCwd,
        stdio: { stdin: hostContext ? { data: JSON.stringify({ context: observed }) } : 'ignore', stdout: { maxBytes: this.config.maxOutputBytes }, stderr: { maxBytes: this.config.maxOutputBytes } },
        graceMs: this.config.graceMs, signal: combined, env: {} });
      this.live.add(handle);
      const outcome = await handle.done;
      if (combined.aborted) throw combined.reason;
      const stdout = handle.collected.stdout?.readFrom(0);
      const stderr = handle.collected.stderr?.readFrom(0);
      if (!stdout || stdout.lossy || stderr?.lossy || Buffer.byteLength(stdout.text) + Buffer.byteLength(stderr?.text ?? '') > this.config.maxOutputBytes) throw new BridgeError('CORE_OUTPUT_LIMIT', 'Core response exceeded configured output limit');
      let parsed: unknown;
      try { parsed = JSON.parse(stdout.text); }
      catch { throw new BridgeError('CORE_INVALID_RESPONSE', 'Core stdout must contain exactly one JSON result'); }
      const result = object(parsed);
      if (result.apiVersion !== 'bugate.io/v2' || result.kind !== 'CommandResult' || !Array.isArray(result.errors)) throw new BridgeError('CORE_INVALID_RESPONSE', 'Core envelope is incompatible');
      const invocation = string(result.invocation, 'invocation');
      if (!['completed', 'invalid_input', 'runtime_error', 'cancelled', 'timeout'].includes(invocation)) throw new BridgeError('CORE_INVALID_RESPONSE', 'Unknown invocation result');
      if (![0, 1, 2, 3, 124, 130].includes(outcome.exitCode ?? -1)) throw new BridgeError('CORE_EXIT_ERROR', `Core exited unexpectedly: ${outcome.exitCode}/${outcome.signal}`);
      const expectedInvocation = new Map([[0, 'completed'], [3, 'completed'], [2, 'invalid_input'], [1, 'runtime_error'], [124, 'timeout'], [130, 'cancelled']]);
      if (expectedInvocation.get(outcome.exitCode ?? -1) !== invocation) throw new BridgeError('CORE_INVALID_RESPONSE', 'Exit code and invocation disagree');
      const expectedOperation = ['protocol', 'evidence', 'execution', 'artifact', 'scope', 'suggestion'].includes(argv[0] ?? '') ? `${argv[0]}.${argv[1]}` : argv[0];
      if (result.operation !== expectedOperation) throw new BridgeError('CORE_INVALID_RESPONSE', 'Response operation differs from invoked command');
      string(result.request_id, 'request_id');
      for (const value of result.errors) { const e = object(value); string(e.code, 'error code'); string(e.message, 'error message'); }
      json(parsed);
      // Preserve the complete process value for canonical JSON Schema validation
      // by the adapter; never discard unknown fields before that validation.
      return parsed as CommandResult;
    } catch (error) {
      if (combined.aborted) {
        handle?.terminate();
        let drained = true;
        if (handle) { try { drained = await handle.waitForExit(AbortSignal.timeout(this.config.graceMs * 3)); } catch { drained = false; } }
        throw new BridgeError(deadline.aborted && !signal?.aborted && !this.disposed.signal.aborted ? 'CORE_TIMEOUT' : 'CORE_CANCELLED', drained ? 'Managed core invocation stopped and drained' : 'Managed core invocation stopped; provider could not confirm complete drain');
      }
      if (error instanceof BridgeError) throw error;
      throw new BridgeError('DEPENDENCY_MISSING', error instanceof Error ? error.message : String(error));
    } finally { if (handle) this.live.delete(handle); }
  }

  async dispose(): Promise<void> {
    this.disposed.abort();
    await Promise.all([...this.live].map(async handle => { handle.terminate(); await handle.done.catch(() => undefined); await handle.waitForExit(AbortSignal.timeout(this.config.graceMs * 3)); }));
    this.live.clear();
  }
}
