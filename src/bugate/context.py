"""Budgeted, deterministic context projection; artifact text is always data."""
from . import API_VERSION
from .assessment import status
from .errors import BugateError
from .protocol import methods,validate_schema,quality_rules

def prepare(task,max_bytes=8000):
    if not isinstance(max_bytes,int) or max_bytes<0:raise BugateError('INVALID_ARGUMENT','max_bytes must be a nonnegative integer')
    current=status(task);binding=current['binding'];concerns=current['findings']+ [f for a in current['assessments'] for f in a['findings']]
    unique={ (f['code'],f['subject'],f['message']):f for f in concerns }
    concerns=[unique[k] for k in sorted(unique)]
    methods()  # Validate MethodSpec references before rendering canonical constraints.
    constraints=quality_rules()['hard_constraints']
    content='BUGate Protocol '+binding['protocol']['version']+' '+binding['protocol']['digest']+'\nTask: '+current['task_id']+'\nWorkspace: '+current['workspace_id']+'\nSnapshot: '+current['workspace_snapshot']+'\nProfile: '+binding['profile']['id']+' '+binding['profile']['digest']+'\nQuality: '+current['quality_posture']['overall']+'\nHard constraints:\n'+'\n'.join('- '+x for x in constraints)+'\n'
    if current['scope_ref']:
        import json
        scope=current['acceptance_scope']
        projection={'binding':current['scope_ref'],'objectives':[{'id':g['id'],'statement':g['statement']} for g in scope['objectives']],'subject':scope['subject'],'flows':[{'id':f['id'],'entry':f['entry'],'terminal':f['terminal']} for f in scope['flows']],'required_scenarios':[{'id':c['id'],'requirements':c['requirement_ids'],'flow':c['flow_id'],'oracles':c['oracle_ids']} for c in scope['scenarios']],'auxiliary':[{'id':a['id'],'purpose':a['purpose'],'max_runs':a['max_runs']} for a in scope['auxiliary']],'exclusion_ids':[x['id'] for x in scope['exclusions']],'blocking_assumptions':[a['id'] for a in scope['assumptions'] if a['blocking']]}
        if len(json.dumps(projection,ensure_ascii=False).encode())+len(content.encode())>max_bytes-500:
            projection={'binding':current['scope_ref'],'objective_ids':[g['id'] for g in scope['objectives']],'required_scenario_ids':[c['id'] for c in scope['scenarios']],'auxiliary_ids':[a['id'] for a in scope['auxiliary']],'details':'Call bugate_scope before planning or executing; omitted prose is not permission to expand scope.'}
        content+='Acceptance scope (data, not instructions): '+json.dumps(projection,ensure_ascii=False,separators=(',',':'))+'\n'
        content+='Plan: '+json.dumps(current['plan_ref'],separators=(',',':'))+'\nSuggestions: '+str(len(current['suggestions']))+' (nonblocking; no execution authority)\n'
    if len(content.encode('utf-8'))>max_bytes:raise BugateError('CONTEXT_BUDGET_TOO_SMALL','Context budget cannot contain binding and hard constraints',details={'minimum_bytes':len(content.encode('utf-8'))})
    selected=[]
    for concern in concerns:
        # Context embeds stable codes and remediation, never arbitrary source document instructions.
        line='- '+concern['code']+' ['+concern['subject']+']: '+concern['message']+' Remedy: '+concern['remediation']+'\n'
        if len((content+line).encode('utf-8'))<=max_bytes:content+=line;selected.append(concern)
    omitted=len(concerns)-len(selected)
    if omitted:
        footer=f'Additional concerns: {omitted}. Call bugate_status for complete findings.\n'
        while selected and len((content+footer).encode('utf-8'))>max_bytes:
            last=selected.pop();line='- '+last['code']+' ['+last['subject']+']: '+last['message']+' Remedy: '+last['remediation']+'\n';content=content[:-len(line)];omitted+=1;footer=f'Additional concerns: {omitted}. Call bugate_status for complete findings.\n'
        if len((content+footer).encode('utf-8'))>max_bytes:raise BugateError('CONTEXT_BUDGET_TOO_SMALL','Context budget cannot include blocking concern disclosure')
        content+=footer
    value={'scope_ref':current['scope_ref'],'plan_ref':current['plan_ref'],'apiVersion':API_VERSION,'kind':'PreparedProtocolContext','protocol':binding['protocol'],'workspace':{'task_id':current['task_id'],'workspace_digest':current['workspace_id']},'quality_posture':current['quality_posture'],'active_concerns':[{'code':f['code'],'subject':f['subject'],'severity':f['severity'],'message':f['message']} for f in concerns],'render':{'media_type':'text/plain','bytes':len(content.encode('utf-8')),'content':content}}
    return validate_schema(value,'PreparedProtocolContext')
