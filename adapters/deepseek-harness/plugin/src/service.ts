import { Service, type Context } from '@deepseek-ai/cordis';
import type { CommandResult } from './contracts.js';

type CoreCall = (argv: string[], task: string, signal?: AbortSignal) => Promise<CommandResult>;

declare module '@deepseek-ai/cordis' {
  interface Context { bugate: BugateService }
}

/** Native Cordis quality API. The owner fiber registers/removes ctx.bugate.
 * Transport and native provenance signing are deliberately not public methods. */
export class BugateService extends Service {
  readonly status: (task: string, signal?: AbortSignal) => Promise<CommandResult>;
  readonly prepare: (task: string, signal?: AbortSignal) => Promise<CommandResult>;
  readonly assess: (task: string, claim: string, signal?: AbortSignal) => Promise<CommandResult>;
  constructor(ctx: Context, call: CoreCall, maxContextBytes: number) {
    super(ctx, 'bugate');
    // Cordis exposes services through context-tracing proxies. Closures keep the
    // private transport inaccessible without relying on JS private-field brands
    // (which intentionally do not accept a Proxy as the original instance).
    this.status = (task, signal) => call(['status', '--task', task], task, signal);
    this.prepare = (task, signal) => call(['protocol', 'prepare', '--task', task, '--max-bytes', String(maxContextBytes)], task, signal);
    this.assess = (task, claim, signal) => call(['assess', '--task', task, '--claim', claim], task, signal);
  }
}
