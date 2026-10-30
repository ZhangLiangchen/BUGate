export class BugateError extends Error {
    constructor(public code: string, message: string, public exitCode = 2, public details: any = null) { super(message); this.name = 'BugateError'; }
    get exit_code() { return this.exitCode; }
    asDict() { return { code: this.code, message: this.message, details: this.details }; }
}
export type JsonObject = Record<string, any>;
export const API_VERSION = 'bugate.io/v2';
export const VERSION = '2.2.0-dev.1';
export const PROTOCOL_VERSION = VERSION;
export const eq = (a: any, b: any): boolean => JSON.stringify(sort(a)) === JSON.stringify(sort(b));
function sort(v: any): any { return Array.isArray(v) ? v.map(sort) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sort(v[k])])) : v; }
export const subset = (a: Iterable<any>, b: Iterable<any>) => { const s = new Set(b); return [...a].every(x => s.has(x)); };
export const sameSet = (a: Iterable<any>, b: Iterable<any>) => subset(a, b) && subset(b, a);
export const difference = (a: Iterable<any>, b: Iterable<any>) => { const s = new Set(b); return [...new Set(a)].filter(x => !s.has(x)).sort(); };
export const intersect = (a: Iterable<any>, b: Iterable<any>) => { const s = new Set(b); return [...a].some(x => s.has(x)); };
export const values = (o: Record<string, any>): any[] => Object.values(o);
