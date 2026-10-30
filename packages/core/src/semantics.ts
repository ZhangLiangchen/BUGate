import { subset, intersect, values } from './errors.js';
const PLACEHOLDERS = new Set(['tbd', 'todo', 'pending', 'unknown', '<fill>', '...', '待定', '待补充']);
const QUALITY_TYPES = ['business_brief', 'testability', 'inventory', 'semantic_review', 'adversarial_review', 'domain_model', 'state_model', 'dimension_matrix'];
export function unresolvedFields(value: any, pointer = 'content'): string[] {
    if (Array.isArray(value))
        return value.flatMap((x, i) => unresolvedFields(x, pointer + '/' + i));
    if (value && typeof value === 'object')
        return Object.entries(value).filter(([k]) => !['classification', 'status', 'disposition'].includes(k)).flatMap(([k, v]) => unresolvedFields(v, pointer + '/' + k));
    return typeof value === 'string' && PLACEHOLDERS.has(value.trim().toLowerCase()) ? [pointer] : [];
}
export function checkSourcesAndPlaceholders(f: any) {
    for (const kind of QUALITY_TYPES)
        for (const a of f.by_type[kind] ?? []) {
            if (!a.source_refs.length)
                f.add('SOURCE_PLAN_MISSING', a.id, 'Quality artifact requires a declared source plan', kind);
            for (const p of unresolvedFields(a.content))
                f.add('SEMANTIC_PLACEHOLDER', a.id, 'Required quality value is unresolved', kind, { basis: [p] });
            for (const item of [...a.content.cases ?? [], ...a.content.decisions ?? []])
                for (const src of item.source_refs ?? a.source_refs)
                    if (!(Object.hasOwn(f.sources, src)))
                        f.add('REFERENCE_MISSING', a.id, 'Item source is not declared by Profile', kind, { basis: [src] });
        }
}
export function qualityModels(f: any, phase: string) {
    const requirements = f.w.profile.quality_requirements ?? {}, types = phase === 'business_understanding' ? ['domain_model', 'state_model'] : ['adversarial_review', 'dimension_matrix'];
    for (const k of types)
        if (requirements[k] && !f.by_type[k]?.length)
            f.add('QUALITY_MODEL_MISSING', k, 'Profile-required quality model is absent', phase);
    const ref = (subject, name, target) => {
        if (!(Array.isArray(target) ? target.includes(name) : Object.hasOwn(target, name)))
            f.add('MODEL_REFERENCE_INVALID', subject, 'Quality-model reference is absent', phase, { basis: [name] });
    };
    const objects: any = Object.create(null), states: any = Object.create(null), transitions: any = Object.create(null), invariants: any = Object.create(null), dimensions: any = Object.create(null), adversarial: any = Object.create(null);
    for (const a of f.by_type.domain_model ?? []) {
        f.unique(objects, a.content.objects, a.id);
        f.unique(invariants, a.content.invariants, a.id);
    }
    for (const a of f.by_type.state_model ?? []) {
        f.unique(states, a.content.states, a.id);
        f.unique(transitions, a.content.transitions, a.id);
    }
    if (phase === 'business_understanding') {
        for (const inv of values(invariants)) {
            for (const id of inv.object_ids)
                ref(inv.id, id, objects);
            for (const id of inv.proposition_ids)
                ref(inv.id, id, f.props);
        }
        for (const a of f.by_type.domain_model ?? [])
            for (const r of a.content.relationships)
                for (const field of ['from_object', 'to_object'])
                    ref(a.id, r[field], objects);
        for (const s of values(states))
            ref(s.id, s.object_id, objects);
        for (const t of values(transitions)) {
            for (const field of ['from_state', 'to_state'])
                ref(t.id, t[field], states);
            ref(t.id, t.oracle_id, f.oracles);
        }
    }
    else {
        for (const a of f.by_type.dimension_matrix ?? [])
            f.unique(dimensions, a.content.dimensions, a.id);
        for (const d of values(dimensions)) {
            for (const [field, index] of [['proposition_ids', f.props], ['oracle_ids', f.oracles], ['object_ids', objects], ['transition_ids', transitions], ['included_case_ids', f.cases]])
                for (const name of d[field])
                    ref(d.id, name, index);
            for (const cid of d.included_case_ids) {
                const c = f.cases[cid];
                if (c && (!subset(d.proposition_ids, c.proposition_ids) || !subset(d.oracle_ids, c.oracle_ids) || d.selected_layer !== c.layer))
                    f.add('MODEL_REFERENCE_INVALID', d.id, 'Included case does not implement the modeled dimension', phase, { basis: [cid] });
            }
        }
        for (const a of f.by_type.adversarial_review ?? []) {
            f.unique(adversarial, a.content.findings, a.id);
            const advisory = f.advisory.filter(a => a.status === 'suggested').map(a => a.id);
            for (const r of a.content.residual_risks) {
                if (r.classification === 'in_scope') {
                    if (!(r.risk_ids.length || r.requirement_ids.length) || !subset(r.risk_ids, f.risks) || !subset(r.requirement_ids, f.requirements))
                        f.add('SCOPE_AMBIGUITY', a.id, 'Residual risk has no resolved in-scope reference', phase);
                    f.add('ADVERSARIAL_UNRESOLVED', a.id, 'Unresolved in-scope adversarial risk remains', phase, { basis: [...r.risk_ids, ...r.requirement_ids] });
                }
                else if (!advisory.includes(r.suggestion_id) || r.risk_ids.length || r.requirement_ids.length)
                    f.add('SCOPE_AMBIGUITY', a.id, 'Out-of-scope residual must reference a real non-executable suggestion and no in-scope requirement', phase);
            }
        }
        for (const item of values(adversarial)) {
            for (const [field, index] of [['proposition_ids', f.props], ['oracle_ids', f.oracles], ['risk_ids', f.risks], ['case_ids', f.cases]])
                for (const name of item[field])
                    ref(item.id, name, index);
            if (item.disposition === 'unresolved' && (intersect(item.risk_ids, f.risks) || intersect(item.case_ids, Object.keys(f.cases))))
                f.add('ADVERSARIAL_UNRESOLVED', item.id, 'Adversarial finding requires an evidenced disposition', phase);
            if (item.disposition === 'included') {
                const cases = item.case_ids.filter(id => Object.hasOwn(f.cases, id)).map(id => f.cases[id]);
                for (const field of ['proposition_ids', 'oracle_ids', 'risk_ids'])
                    if (!cases.length || !subset(item[field], cases.flatMap(c => c[field])))
                        f.add('ADVERSARIAL_CASE_UNMAPPED', item.id, 'Included adversarial finding is not absorbed by matching inventory cases', phase, { basis: [field] });
            }
        }
    }
    f.requireReview(types);
}
