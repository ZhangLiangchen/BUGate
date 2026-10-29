import type { Context } from '@deepseek-ai/cordis';
import type {} from '@deepseek-ai/dsh-fs';
import type {} from '@deepseek-ai/dsh-subprocess';
import type { SubprocessHandle } from '@deepseek-ai/dsh-subprocess';
import { BridgeError, json, object, string, type CommandResult } from './contracts.js';

export interface BridgeConfig { pythonExecutable: string; timeoutMs: number; maxOutputBytes: number; graceMs: number }

/** Calls the installed core in the providers' execution world; no local fallback. */
export class PythonBridge {
  private readonly live = new Set<SubprocessHandle>();
  private readonly disposed = new AbortController();
  constructor(private readonly ctx: Context, private readonly config: BridgeConfig) {}

  async call(argv: readonly string[], cwd: string, signal?: AbortSignal): Promise<CommandResult> {
    const deadline = AbortSignal.timeout(this.config.timeoutMs);
    const combined = AbortSignal.any([deadline, this.disposed.signal, ...(signal ? [signal] : [])]);
    let handle: SubprocessHandle | undefined;
    try {
      combined.throwIfAborted();
      const target = await this.ctx.fs.resolve(cwd, { signal: combined });
      const processCwd = this.ctx.fs.processPath(target);
      const command = this.config.pythonExecutable;
      const mapped = command.includes('/') || command.includes('\\') ? this.ctx.fs.processPathFromHostPath(command) : command;
      if (!mapped) throw new BridgeError('RUNTIME_UNSUPPORTED', 'Python installation is not mapped into this filesystem execution world');
      const executable = await this.ctx.subprocess.resolveExecutable(mapped, {}, combined);
      handle = this.ctx.subprocess.spawn({ argv: [executable, '-m', 'bugate', ...argv], cwd: processCwd,
        stdio: { stdin: 'ignore', stdout: { maxBytes: this.config.maxOutputBytes }, stderr: { maxBytes: this.config.maxOutputBytes } },
        graceMs: this.config.graceMs, signal: combined, env: { PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' } });
      this.live.add(handle);
      const outcome = await handle.done;
      if (combined.aborted) throw combined.reason;
      const stdout = handle.collected.stdout?.readFrom(0);
      const stderr = handle.collected.stderr?.readFrom(0);
      if (!stdout || stdout.lossy || stderr?.lossy || Buffer.byteLength(stdout.text) + Buffer.byteLength(stderr?.text ?? '') > this.config.maxOutputBytes) throw new BridgeError('PYTHON_OUTPUT_LIMIT', 'Core response exceeded configured output limit');
      let parsed: unknown;
      try { parsed = JSON.parse(stdout.text); }
      catch { throw new BridgeError('PYTHON_INVALID_RESPONSE', 'Core stdout must contain exactly one JSON result'); }
      const result = object(parsed);
      if (result.apiVersion !== 'bugate.io/v2' || result.kind !== 'CommandResult' || !Array.isArray(result.errors)) throw new BridgeError('PYTHON_INVALID_RESPONSE', 'Core envelope is incompatible');
      const invocation = string(result.invocation, 'invocation');
      if (!['completed', 'invalid_input', 'runtime_error', 'cancelled', 'timeout'].includes(invocation)) throw new BridgeError('PYTHON_INVALID_RESPONSE', 'Unknown invocation result');
      if (![0, 1, 2, 3, 124, 130].includes(outcome.exitCode ?? -1)) throw new BridgeError('PYTHON_EXIT_ERROR', `Core exited unexpectedly: ${outcome.exitCode}/${outcome.signal}`);
      const expectedInvocation = new Map([[0, 'completed'], [3, 'completed'], [2, 'invalid_input'], [1, 'runtime_error'], [124, 'timeout'], [130, 'cancelled']]);
      if (expectedInvocation.get(outcome.exitCode ?? -1) !== invocation) throw new BridgeError('PYTHON_INVALID_RESPONSE', 'Exit code and invocation disagree');
      const expectedOperation = ['protocol', 'evidence', 'execution', 'artifact'].includes(argv[0] ?? '') ? `${argv[0]}.${argv[1]}` : argv[0];
      if (result.operation !== expectedOperation) throw new BridgeError('PYTHON_INVALID_RESPONSE', 'Response operation differs from invoked command');
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
        throw new BridgeError(deadline.aborted && !signal?.aborted && !this.disposed.signal.aborted ? 'PYTHON_TIMEOUT' : 'PYTHON_CANCELLED', drained ? 'Managed core invocation stopped and drained' : 'Managed core invocation stopped; provider could not confirm complete drain');
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
