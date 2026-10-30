import { createPublicKey, verify } from 'node:crypto';
import { BugateError, eq } from './errors.js';
import { canonical, contentDigest, loadJson } from './protocol.js';
import { Workspace, safeId } from './workspace.js';
/** Local native-host key pin. Public CLI has no key-registration operation. This is
 * not protection against a filesystem owner who replaces the public-key pin. */
export function verifyHostContext(task: string | Workspace, operation: string, context: any): any {
    const w = typeof task === 'string' ? new Workspace(task) : task;
    try {
        const authentication = context?.authentication;
        if (!authentication)
            throw new Error('Native signature is missing');
        const { signature, ...unsigned } = authentication;
        if (unsigned.operation !== operation || unsigned.task_root !== w.root || unsigned.workspace_id !== w.id)
            throw new Error('Native signature belongs to another operation or task');
        const body = { ...context };
        delete body.authentication;
        if (unsigned.context_digest !== contentDigest(canonical(body)))
            throw new Error('Native payload bytes changed');
        const id = safeId(unsigned.authority_id), pin = loadJson(w.path('.bugate/host-authorities/' + id + '.json', true));
        if (pin.version !== 1 || pin.host !== 'deepseek-harness' || pin.authority_id !== id || id !== 'authority-' + contentDigest(pin.public_key).split(':')[1])
            throw new Error('Native public-key pin is invalid');
        if (context.host !== 'deepseek-harness' || typeof signature !== 'string' || !verify(null, canonical({ version: 1, ...unsigned }), createPublicKey(pin.public_key), Buffer.from(signature, 'base64')))
            throw new Error('Native signature cannot be verified');
        return context;
    }
    catch (e) {
        throw new BugateError('REVIEW_PROVENANCE_MISSING', 'Native host provenance requires a signature from a pinned DSH authority', 2, { reason: String((e as any)?.message ?? e) });
    }
}
export function observedContext(context: any): any { const copy = structuredClone(context); delete copy.authentication; return copy; }
export function receiptContext(w: Workspace, r: any, operation: string): any { return verifyHostContext(w, operation, { ...r.host_context, authentication: r.authentication }); }
