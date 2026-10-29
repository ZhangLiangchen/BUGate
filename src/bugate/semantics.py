"""Native v2 quality checks over typed artifacts; no file-name or host workflow dialect."""
from __future__ import annotations

# Exact unresolved markers only. Quoted requirements containing these words remain data.
PLACEHOLDERS = frozenset(('tbd', 'todo', 'pending', 'unknown', '<fill>', '...', '待定', '待补充'))
QUALITY_TYPES = ('business_brief', 'testability', 'inventory', 'semantic_review',
                 'adversarial_review', 'domain_model', 'state_model', 'dimension_matrix')


def unresolved_fields(value, pointer='content'):
    """Status enums describe uncertainty; prose placeholders cannot satisfy quality."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in ('classification', 'status', 'disposition'):
                yield from unresolved_fields(child, pointer + '/' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from unresolved_fields(child, pointer + '/' + str(index))
    elif isinstance(value, str) and value.strip().lower() in PLACEHOLDERS:
        yield pointer


def check_sources_and_placeholders(facts):
    for kind in QUALITY_TYPES:
        for artifact in facts.by_type.get(kind, []):
            if not artifact['source_refs']:
                facts.add('SOURCE_PLAN_MISSING', artifact['id'], 'Quality artifact requires a declared source plan', kind)
            for pointer in unresolved_fields(artifact['content']):
                facts.add('SEMANTIC_PLACEHOLDER', artifact['id'], 'Required quality value is unresolved', kind, basis=[pointer])
            for item in artifact['content'].get('cases', []) + artifact['content'].get('decisions', []):
                # Per-item sources may narrow the artifact-level plan; neither is guessed.
                for source in item.get('source_refs', artifact['source_refs']):
                    if source not in facts.sources:
                        facts.add('REFERENCE_MISSING', artifact['id'], 'Item source is not declared by Profile', kind, basis=[source])


def quality_models(facts, phase):
    """Optional richer modeling becomes mandatory only through a stricter Profile."""
    requirements = facts.w.profile.get('quality_requirements', {})
    types = ('domain_model', 'state_model') if phase == 'business_understanding' else ('adversarial_review', 'dimension_matrix')
    for kind in types:
        if requirements.get(kind) and not facts.by_type.get(kind):
            facts.add('QUALITY_MODEL_MISSING', kind, 'Profile-required quality model is absent', phase)

    def ref(subject, name, target):
        if name not in target:
            facts.add('MODEL_REFERENCE_INVALID', subject, 'Quality-model reference is absent', phase, basis=[name])

    objects = {}; states = {}; transitions = {}; invariants = {}; dimensions = {}; adversarial = {}
    for artifact in facts.by_type.get('domain_model', []):
        facts.unique(objects, artifact['content']['objects'], artifact['id'])
        facts.unique(invariants, artifact['content']['invariants'], artifact['id'])
    for artifact in facts.by_type.get('state_model', []):
        facts.unique(states, artifact['content']['states'], artifact['id'])
        facts.unique(transitions, artifact['content']['transitions'], artifact['id'])
    if phase == 'business_understanding':
        for invariant in invariants.values():
            for oid in invariant['object_ids']:ref(invariant['id'], oid, objects)
            for pid in invariant['proposition_ids']:ref(invariant['id'], pid, facts.props)
        for artifact in facts.by_type.get('domain_model', []):
            for relationship in artifact['content']['relationships']:
                for field in ('from_object', 'to_object'):ref(artifact['id'], relationship[field], objects)
        for state in states.values():ref(state['id'], state['object_id'], objects)
        for transition in transitions.values():
            for field in ('from_state', 'to_state'):ref(transition['id'], transition[field], states)
            ref(transition['id'], transition['oracle_id'], facts.oracles)
    else:
        for artifact in facts.by_type.get('dimension_matrix', []):
            facts.unique(dimensions, artifact['content']['dimensions'], artifact['id'])
        for dimension in dimensions.values():
            for field, index in [('proposition_ids', facts.props), ('oracle_ids', facts.oracles), ('object_ids', objects), ('transition_ids', transitions), ('included_case_ids', facts.cases)]:
                for name in dimension[field]:ref(dimension['id'], name, index)
            for cid in dimension['included_case_ids']:
                case = facts.cases.get(cid)
                if case and (not set(dimension['proposition_ids']) <= set(case['proposition_ids']) or not set(dimension['oracle_ids']) <= set(case['oracle_ids']) or dimension['selected_layer'] != case['layer']):
                    facts.add('MODEL_REFERENCE_INVALID', dimension['id'], 'Included case does not implement the modeled dimension', phase, basis=[cid])
        for artifact in facts.by_type.get('adversarial_review', []):
            facts.unique(adversarial, artifact['content']['findings'], artifact['id'])
            from .scope import suggestions
            advisory_ids={a['id'] for a in suggestions(facts.w) if a['status']=='suggested'}
            for residual in artifact['content']['residual_risks']:
                if residual['classification']=='in_scope':
                    if not (residual['risk_ids'] or residual['requirement_ids']) or not set(residual['risk_ids'])<=set(facts.risks) or not set(residual['requirement_ids'])<=set(facts.requirements):
                        facts.add('SCOPE_AMBIGUITY',artifact['id'],'Residual risk has no resolved in-scope reference',phase)
                    facts.add('ADVERSARIAL_UNRESOLVED',artifact['id'],'Unresolved in-scope adversarial risk remains',phase,basis=residual['risk_ids']+residual['requirement_ids'])
                elif residual['suggestion_id'] not in advisory_ids or residual['risk_ids'] or residual['requirement_ids']:
                    facts.add('SCOPE_AMBIGUITY',artifact['id'],'Out-of-scope residual must reference a real non-executable suggestion and no in-scope requirement',phase)
        for item in adversarial.values():
            for field, index in [('proposition_ids', facts.props), ('oracle_ids', facts.oracles), ('risk_ids', facts.risks), ('case_ids', facts.cases)]:
                for name in item[field]:ref(item['id'], name, index)
            in_scope=bool(set(item['risk_ids'])&set(facts.risks) or set(item['case_ids'])&set(facts.cases))
            if item['disposition'] == 'unresolved' and in_scope:
                facts.add('ADVERSARIAL_UNRESOLVED', item['id'], 'Adversarial finding requires an evidenced disposition', phase)
            if item['disposition'] == 'included':
                cases = [facts.cases[cid] for cid in item['case_ids'] if cid in facts.cases]
                for field in ('proposition_ids', 'oracle_ids', 'risk_ids'):
                    covered = {name for case in cases for name in case[field]}
                    if not cases or not set(item[field]) <= covered:
                        facts.add('ADVERSARIAL_CASE_UNMAPPED', item['id'], 'Included adversarial finding is not absorbed by matching inventory cases', phase, basis=[field])
    facts.require_review(types)
