import { randomUUID } from 'node:crypto';
import type { JsonValue } from '@deepseek-ai/dsh-util-values';

export interface CommandError { code: string; message: string; details: JsonValue }
export interface CommandResult {
  apiVersion: 'bugate.io/v2'; kind: 'CommandResult'; operation: string; request_id: string;
  invocation: 'completed' | 'invalid_input' | 'runtime_error' | 'cancelled' | 'timeout';
  data: JsonValue; errors: CommandError[];
}
export interface PreparedContext {
  apiVersion: string; kind: string;
  protocol: { id: string; version: string; digest: string };
  workspace: { task_id: string; workspace_digest?: string | null };
  scope_ref?: { id: string; revision: number; digest: string } | null;
  plan_ref?: { id: string; digest: string } | null;
  quality_posture: Record<string, string>;
  active_concerns: { code: string; subject?: string; severity?: string; message?: string }[];
  render: { media_type: string; bytes: number; content: string };
}
export class BridgeError extends Error {
  constructor(readonly code: string, message: string) { super(message); this.name = 'BugateBridgeError'; }
}
export function failure(operation: string, error: unknown): CommandResult {
  const code = error instanceof BridgeError ? error.code : 'RUNTIME_ERROR';
  return { apiVersion: 'bugate.io/v2', kind: 'CommandResult', operation, request_id: randomUUID(),
    invocation: code === 'CORE_CANCELLED' ? 'cancelled' : code === 'CORE_TIMEOUT' ? 'timeout' : 'runtime_error',
    data: null, errors: [{ code, message: error instanceof Error ? error.message : String(error), details: {} }] };
}
export function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new BridgeError('SCHEMA_INVALID', 'Expected a JSON object');
  return value as Record<string, unknown>;
}
export function string(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) throw new BridgeError('SCHEMA_INVALID', `${label} must be a non-empty string`);
  return value;
}
export function json(value: unknown): JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map(json);
  if (typeof value === 'object') return Object.fromEntries(Object.entries(object(value)).map(([k, v]) => [k, json(v)]));
  throw new BridgeError('CORE_INVALID_RESPONSE', 'Response is not lossless JSON');
}
