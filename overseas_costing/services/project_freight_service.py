"""Server-issued project weight policy and exact two-level freight allocation."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import re
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from overseas_costing.services.shipment_cost_service import object_json, number


def policy_from_fee(fee):
    return object_json(fee.get('scope_value_json')).get('project_allocation')


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.get('method') != 'PROJECT_GROSS_WEIGHT':
        raise ValueError(_('项目分摊规则无效。'))
    projects = policy.get('projects')
    if not isinstance(projects, list) or len(projects) < 2 or any(not isinstance(p,str) or not p.strip() for p in projects) or len(set(projects)) != len(projects):
        raise ValueError(_('项目分摊清单无效。'))
    if not policy.get('source_refs'):
        raise ValueError(_('项目分摊缺少可信来源。'))
    return policy


def approval_project_policy(source):
    fields = source.get('form_fields') or {}
    texts = fields.values() if isinstance(fields,dict) else fields
    for value in texts:
        text = str(value)
        if not re.search(r'重量\s*占比', text):
            continue
        matches = re.findall(r'([^\n\r]+?项目)\s*([\d,.]+)\s*kg\b', text, re.I)
        projects, weights = [], {}
        for label, weight in matches:
            label = label.strip()
            parsed = number(weight.replace(',',''))
            if parsed is not None and parsed > 0 and label not in weights:
                projects.append(label); weights[label] = str(parsed)
        if len(projects) >= 2:
            return {'method':'PROJECT_GROSS_WEIGHT', 'projects':projects, 'source_weights_kg':weights,
                'source_refs':[{'source_id':source.get('source_id'), 'approval_no':source.get('approval_no'),
                                'source_hash':source.get('source_hash'), 'field':'物流报价/项目重量占比'}]}
    return None


def largest_remainder(amount, values, precision=2):
    quantum = Decimal(1).scaleb(-precision)
    total = abs(amount).quantize(quantum, rounding=ROUND_HALF_UP) if precision == 6 else abs(amount).quantize(quantum)
    denominator = sum(values.values(), Decimal('0'))
    exact = {key: total * value / denominator for key,value in values.items()}
    amounts = {key:value.quantize(quantum, rounding=ROUND_DOWN) for key,value in exact.items()}
    order = sorted(values, key=lambda key: (-(exact[key]-amounts[key]),key))
    for key in order[:int((total-sum(amounts.values()))/quantum)]:
        amounts[key] += quantum
    return {key: -value if amount < 0 else value for key, value in amounts.items()}


def allocate_projects(policy, amount, items, precision=2):
    try:
        validate_policy(policy)
    except ValueError:
        return {'status':'BLOCKED','code':'PROJECT_POLICY_INVALID','allocations':{}}
    groups = {}
    for row in items:
        project = str(row.get('project_collection') or '').strip()
        weight = number(row.get('gross_weight_kg'))
        if not project or project not in policy['projects']:
            return {'status':'BLOCKED','code':'PROJECT_MEMBERSHIP_REQUIRED','allocations':{}}
        if weight is None or weight < 0:
            return {'status':'BLOCKED','code':'PROJECT_WEIGHT_REQUIRED','allocations':{}}
        key = str(row.get('stable_line_key') or row.get('name') or '')
        groups.setdefault(project,{})[key] = weight
    weights = {project:sum(values.values()) for project,values in groups.items()}
    if set(weights) != set(policy['projects']) or any(weight <= 0 for weight in weights.values()):
        return {'status':'BLOCKED','code':'PROJECT_WEIGHT_REQUIRED','allocations':{}}
    project_amounts = largest_remainder(amount,weights,precision)
    allocations = {}
    for project,values in groups.items():
        allocations.update({k:format(v,f'.{precision}f') for k,v in largest_remainder(project_amounts[project],values,precision).items()})
    return {'status':'ALLOCATED','allocations':allocations,'allocated_total':format(sum(project_amounts.values()),f'.{precision}f'),
        'amount':str(amount),'basis':'project_gross_weight','preferred_basis':'project_gross_weight','fallback_reason':'',
        'scope_type':'ALL_ITEMS','project_allocations':{p:format(v,f'.{precision}f') for p,v in project_amounts.items()},
        'project_weights_kg':{p:str(v) for p,v in weights.items()}}
