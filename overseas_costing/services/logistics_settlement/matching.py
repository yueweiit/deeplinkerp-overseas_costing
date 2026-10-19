"""证据明确的候选匹配与双向唯一关联；金额不是匹配条件。"""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from .model import digest, dumps


def related_expenses(store, logistics):
    ids = {r['source_id'] for r in store.find('reference', corp=logistics['corp'], target_instance=logistics['instance'])}
    for token_type, token in logistics['identifiers']:
        ids.update(r['source_id'] for r in store.find('identifier', corp=logistics['corp'], token=token, token_type=token_type))
    return [s for id in sorted(ids) if (s := store.get('source', id)) and s['kind'] == 'expense' and not s['invalid']]


def match_source(store, source_id):
    source = store.get('source', source_id)
    if not source:
        return []
    if source['kind'] == 'unclassified' or source['invalid']:
        invalidate_source_candidates(store, source_id)
        return []
    if source['kind'] == 'logistics':
        results = []
        # Indexed lookup also handles expenses arriving before logistics.
        for expense in related_expenses(store, source):
            results.extend(match_source(store, expense['id']))
        return results
    if store.find('binding', expense_id=source_id):
        return []
    evidence = {}
    for token_type, token in source['identifiers']:
        for ident in store.find('identifier', corp=source['corp'], token=token):
            other = store.get('source', ident['source_id'])
            if other['kind'] == 'logistics' and not other['invalid'] and ident['token_type'] == token_type:
                evidence.setdefault(other['id'], []).append({'type': token_type, 'value': token})
    for instance in source['related']:
        for other in store.find('source', corp=source['corp'], instance=instance):
            if other['kind'] == 'logistics' and not other['invalid']:
                evidence.setdefault(other['id'], []).append({'type': 'explicit', 'value': instance})
    results = []
    for logistics_id, proof in evidence.items():
        logistics = store.get('source', logistics_id)
        key = digest(logistics_id, source_id)
        old = store.get('candidate', key)
        evidence_hash = digest(source['match_hash'], logistics['match_hash'], proof)
        if old and old['status'] == 'rejected' and old['evidence_hash'] == evidence_hash:
            continue
        method = 'explicit' if any(p['type'] == 'explicit' for p in proof) else 'identifier' if any(p['type'] != 'container' for p in proof) else 'container'
        occupied = bool(store.find('binding', logistics_id=logistics_id))
        competitors = [s for s in related_expenses(store, logistics) if s['id'] != source_id]
        status = 'conflict' if len(evidence) > 1 or competitors or occupied or source['invalid'] or logistics['invalid'] else 'pending'
        item = {'id': key, 'logistics_id': logistics_id, 'expense_id': source_id, 'status': status, 'method': method, 'evidence': proof, 'evidence_hash': evidence_hash, 'logistics_snapshot': logistics['snapshot'], 'expense_snapshot': source['snapshot'], 'revision': digest(logistics['snapshot'], source['snapshot'], evidence_hash), 'reason': '多候选、已占用或审批无效' if status == 'conflict' else ''}
        store.put('candidate', {'id': key, 'logistics_id': logistics_id, 'expense_id': source_id, 'status': status, 'data': dumps(item)})
        results.append(item)
    for logistics_id in evidence:
        logistics = store.get('source', logistics_id)
        competitors = sorted(s['id'] for s in related_expenses(store, logistics))
        if len(competitors) > 1:
            for existing in store.find('candidate', logistics_id=logistics_id):
                if existing['status'] == 'pending':
                    existing.update(status='conflict', reason='一票物流命中多张支出，请逐项核对')
                    existing['revision'] = digest(existing['revision'], competitors)
                    save_candidate(store, existing)
    live_ids = {digest(id, source_id) for id in evidence}
    for old in store.find('candidate', expense_id=source_id):
        if old['id'] not in live_ids and old['status'] not in {'confirmed', 'rejected'}:
            old['status'] = 'stale'
            save_candidate(store, old)
    return results


def save_candidate(store, candidate):
    store.put('candidate', {k: candidate[k] for k in ('id', 'logistics_id', 'expense_id', 'status')} | {'data': dumps(candidate)})


def save_binding(store, binding):
    store.put('binding', {k: binding[k] for k in ('id', 'logistics_id', 'expense_id')} | {'data': dumps(binding)})


def reject_candidate(store, candidate_id, revision, actor, reason):
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        candidate = store.get('candidate', candidate_id, lock=True)
        if not candidate or candidate['revision'] != revision or candidate['status'] == 'confirmed':
            raise ValueError(_('候选已变化，请刷新'))
        candidate.update(status='rejected', rejected_by=actor, rejection_reason=reason)
        save_candidate(store, candidate)
        store.audit('', 'reject_candidate', actor, candidate_id=candidate_id, reason=reason)


def validate_sources(store, candidate):
    logistics = store.get('source', candidate['logistics_id'])
    expense = store.get('source', candidate['expense_id'])
    if logistics['snapshot'] != candidate['logistics_snapshot'] or expense['snapshot'] != candidate['expense_snapshot']:
        raise ValueError(_('来源已变化，请重新预览'))
    if expense['invalid'] or logistics['invalid'] or expense['kind'] != 'expense' or logistics['kind'] != 'logistics' or expense['corp'] != logistics['corp']:
        raise ValueError(_('来源无效或业务类型不符'))
    return logistics, expense


def confirm_candidate(store, candidate_id, revision, actor, *, resolve=False, reason=''):
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        candidate = store.get('candidate', candidate_id, lock=True)
        if not candidate or candidate['revision'] != revision:
            raise ValueError(_('匹配预览已变化，请刷新'))
        for id in sorted([candidate['logistics_id'], candidate['expense_id']]):
            store.get('source', id, lock=True)
        logistics = store.get('source', candidate['logistics_id'])
        expense = store.get('source', candidate['expense_id'])
        existing = store.find('binding', expense_id=expense['id'])
        if existing and existing[0]['logistics_id'] == logistics['id'] and candidate['status'] == 'confirmed':
            return existing[0]
        allowed = candidate['status'] == 'pending' or (resolve and reason.strip() and candidate['status'] == 'conflict')
        if not allowed or existing or store.find('binding', logistics_id=logistics['id']):
            raise ValueError(_('关联冲突：每票物流和物流支出只能各关联一次'))
        validate_sources(store, candidate)
        current_targets = eligible_logistics(store, expense)
        if candidate['method'] not in {'manual', 'deepseek'} and logistics['id'] not in current_targets:
            raise ValueError(_('匹配标识已变化，请重新匹配'))
        ambiguous = len(current_targets) > 1 or any(s['id'] != expense['id'] for s in related_expenses(store, logistics)) or any(c['expense_id'] != expense['id'] and c['status'] in {'pending','conflict'} for c in store.find('candidate', logistics_id=logistics['id']))
        ambiguous = ambiguous or any(c['logistics_id'] != logistics['id'] and c['status'] in {'pending', 'conflict'}
                                     for c in store.find('candidate', expense_id=expense['id']))
        if ambiguous and not (resolve and reason.strip()):
            raise ValueError(_('关联冲突：新增了其他支出候选，请逐项核对后确认'))
        binding = {'id': digest('binding', logistics['id']), 'logistics_id': logistics['id'], 'expense_id': expense['id'], 'actor': actor, 'revision': 1, 'status': 'bound', 'application_status': 'pending', 'candidate_id': candidate_id}
        store.insert('binding', {'id': binding['id'], 'logistics_id': logistics['id'], 'expense_id': expense['id'], 'data': dumps(binding)})
        candidate['status'] = 'confirmed'
        store.put('candidate', {'id': candidate_id, 'logistics_id': logistics['id'], 'expense_id': expense['id'], 'status': 'confirmed', 'data': dumps(candidate)})
        store.audit(binding['id'], 'confirm', actor, candidate_id=candidate_id, reason=reason)
        return binding


def replace_binding(store, binding_id, expected_revision, candidate_id, candidate_revision, actor, reason, *, on_replace=None):
    if not reason.strip():
        raise ValueError(_('更正关联必须填写原因'))
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        binding = store.get('binding', binding_id, lock=True)
        candidate = store.get('candidate', candidate_id, lock=True)
        if not binding or binding['revision'] != int(expected_revision) or not candidate or candidate['revision'] != candidate_revision:
            raise ValueError(_('关联或候选已变化，请刷新'))
        if candidate['logistics_id'] != binding['logistics_id']:
            raise ValueError(_('候选不属于当前物流'))
        for id in sorted({binding['logistics_id'], binding['expense_id'], candidate['expense_id']}):
            store.get('source', id, lock=True)
        validate_sources(store, candidate)
        if store.find('binding', expense_id=candidate['expense_id']):
            raise ValueError(_('新采购支出已经被占用'))
        old = dict(binding)
        binding.update(expense_id=candidate['expense_id'], revision=binding['revision'] + 1,
                       candidate_id=candidate_id, actor=actor, application_status='pending', coverage=None, negative_confirmed=False, source_snapshot=None, applied_hash=None)
        if on_replace:
            on_replace(old, binding)
        save_binding(store, binding)
        old_candidate = store.get('candidate', old['candidate_id'])
        if old_candidate:
            old_candidate.update(status='rejected', rejection_reason=reason, rejected_by=actor)
            save_candidate(store, old_candidate)
        candidate['status'] = 'confirmed'
        save_candidate(store, candidate)
        store.audit(binding_id, 'replace', actor, old=old, new=binding, reason=reason)
        return binding


def refresh_candidate_snapshots(store, source_id):
    # A non-identifier edit updates previews without a new all-source search.
    candidates = {c['id']: c for field in ('logistics_id', 'expense_id') for c in store.find('candidate', **{field: source_id})}
    for candidate in candidates.values():
        if candidate['status'] in {'confirmed', 'rejected', 'stale'}:
            continue
        logistics = store.get('source', candidate['logistics_id'])
        expense = store.get('source', candidate['expense_id'])
        changed = candidate['logistics_snapshot'] != logistics['snapshot'] or candidate['expense_snapshot'] != expense['snapshot']
        if changed and candidate['method'] == 'deepseek':
            candidate.update(status='stale', reason='来源资料已变化，需要重新分析 AI 匹配依据')
            save_candidate(store, candidate)
            continue
        if changed and any(e.get('type') == 'deepseek' for e in candidate['evidence']):
            candidate['evidence'] = [e for e in candidate['evidence'] if e.get('type') != 'deepseek']
            candidate['reason'] = '规则候选待核对；旧 AI 分析已失效'
        candidate.update(logistics_snapshot=logistics['snapshot'], expense_snapshot=expense['snapshot'])
        candidate['revision'] = digest(logistics['snapshot'], expense['snapshot'], candidate['evidence_hash'])
        if logistics['invalid'] or expense['invalid'] or expense['kind'] != 'expense':
            candidate.update(status='stale', reason='审批已失效或来源类别不符，不再参与匹配')
        save_candidate(store, candidate)


def eligible_logistics(store, expense):
    ids = set()
    for token_type, token in expense['identifiers']:
        ids.update(r['source_id'] for r in store.find('identifier', corp=expense['corp'], token=token, token_type=token_type))
    for instance in expense['related']:
        ids.update(s['id'] for s in store.find('source', corp=expense['corp'], instance=instance))
    return {id for id in ids if (s := store.get('source', id)) and s['kind'] == 'logistics' and not s['invalid']}


def invalidate_source_candidates(store, source_id):
    candidates = {c['id']: c for field in ('logistics_id', 'expense_id') for c in store.find('candidate', **{field: source_id})}
    for candidate in candidates.values():
        if candidate['status'] not in {'confirmed', 'rejected'}:
            candidate.update(status='stale', reason='匹配标识已变化，等待重新匹配')
            save_candidate(store, candidate)


def configure_binding(store, binding, *, coverage=None, negative_confirmed=None, actor):
    from .application import SCOPES
    changed = {}
    expense = store.get('source', binding['expense_id'], lock=True)
    if coverage is not None:
        if not isinstance(coverage, list) or not coverage or set(coverage) - SCOPES:
            raise ValueError(_('费用范围无效'))
        values = sorted(set(coverage))
        if values != binding.get('coverage') or expense['cost_hash'] != binding.get('coverage_cost_hash'):
            changed['coverage'] = values
            changed['coverage_cost_hash'] = expense['cost_hash']
    if negative_confirmed is not None and (bool(negative_confirmed) != bool(binding.get('negative_confirmed')) or (negative_confirmed and binding.get('negative_cost_hash') != expense['cost_hash'])):
        changed['negative_confirmed'] = bool(negative_confirmed)
        changed['negative_cost_hash'] = expense['cost_hash'] if negative_confirmed else None
    if changed:
        before = {key: binding.get(key) for key in changed}
        binding.update(changed, revision=binding['revision'] + 1)
        store.audit(binding['id'], 'application_options', actor, before=before, choices=changed)
        save_binding(store, binding)
    return binding
