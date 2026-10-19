"""Durable matching for one logistics source. Never binds or scans other logistics bodies."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import uuid
from datetime import datetime, timedelta, timezone
from decimal import InvalidOperation

from . import ai_matching as ai
from .jobs import utcnow
from .model import digest, dumps
from .matching import eligible_logistics, save_candidate

POLICY = 'batch-settlement-match-1'


def candidates(store, logistics):
    result = []
    for c in store.find('candidate', logistics_id=logistics['id']):
        expense = store.get('source', c['expense_id'])
        if (c['status'] in {'pending', 'conflict'} and expense and not expense['invalid']
                and expense['kind'] == 'expense' and expense['corp'] == logistics['corp']
                and c['logistics_snapshot'] == logistics['snapshot'] and c['expense_snapshot'] == expense['snapshot']):
            result.append(c)
    return result


def prepare(store, logistics_id):
    logistics = store.get('source', logistics_id)
    if not logistics or logistics['kind'] != 'logistics' or logistics['invalid']:
        raise ValueError(_('当前国际物流来源缺失或已失效，请先核对来源'))
    bindings = store.find('binding')
    occupied = {b['expense_id'] for b in bindings}
    expenses = [e for e in store.find('source', kind='expense', corp=logistics['corp'])
                if not e['invalid'] and e['id'] not in occupied]
    expense_ids = {e['id'] for e in expenses}
    relevant = [c for c in store.find('candidate') if c['expense_id'] in expense_ids]
    rejected = [c for c in relevant if c['logistics_id'] == logistics_id and c['status'] == 'rejected']
    # Own generated candidates do not invalidate their own job. Rejections and other
    # logistics candidate conflicts are inputs, so concurrent human decisions fence AI.
    dependencies = [(c['id'], c['status'], c['revision']) for c in relevant
                    if c['logistics_id'] != logistics_id and c['status'] in {'pending', 'conflict', 'confirmed'} or c in rejected]
    key = digest(POLICY, logistics['snapshot'], [(e['id'], e['snapshot']) for e in expenses],
                 [(b['id'], b['expense_id'], b['revision']) for b in bindings
                  if b['expense_id'] in expense_ids or b['logistics_id'] == logistics_id], dependencies)
    return {'logistics': logistics, 'expenses': expenses, 'fingerprint': key,
            'rejected_pairs': [[c['expense_id'], logistics_id] for c in rejected]}


def public(job, *, cached=False):
    return {**{k: job.get(k) for k in ('id', 'status', 'stage', 'total', 'processed', 'recommended',
            'no_match', 'failed', 'error', 'message', 'started_at', 'finished_at', 'model', 'approval_no')},
            'cached': cached, 'policy': POLICY}


def status(store, logistics_id):
    if store.find('binding', logistics_id=logistics_id):
        return {'status': 'bound', 'cached': True, 'message': '已保存关联，直接读取采购支出'}
    pointer = store.get('state', digest('batch-match-pointer', logistics_id)) or {}
    job = store.get('state', pointer.get('job_id', '')) or {}
    if not job:
        return {'status': 'not_started', 'cached': False}
    try:
        valid = prepare(store, logistics_id)['fingerprint'] == job['fingerprint']
    except ValueError as exc:
        return {**public(job), 'status': 'unavailable', 'error': str(exc)}
    if not valid:
        return {**public(job), 'status': 'stale', 'message': '本票来源或采购支出池已更新，需重新匹配'}
    active = {c['expense_id'] for c in candidates(store, store.get('source', logistics_id))}
    expected = {eid for eid, result in job.get('results', {}).items() if result['outcome'] == 'recommended'}
    if job['status'] == 'completed' and (expected - active or job.get('recommended') and not active):
        return {**public(job), 'status': 'stale', 'message': '保存的推荐候选已失效，需重新核对匹配'}
    if job['status'] in {'queued', 'running'} and str(job.get('started_at') or '') < (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat():
        return {**public(job), 'status': 'failed', 'error': '本票匹配超时，可重试；已完成结果会保留'}
    return public(job, cached=job['status'] == 'completed')


def start(store, logistics_id, actor):
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        current = status(store, logistics_id)
        if current['status'] == 'bound' or current.get('cached'):
            return current
        if (current['status'] in {'queued', 'running'} and str(current.get('started_at') or '') >
                (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()):
            return current
        prepared = prepare(store, logistics_id)
        jid = digest(POLICY, logistics_id, prepared['fingerprint'])
        previous = store.get('state', jid) or {}
        active = {c['expense_id'] for c in candidates(store, prepared['logistics'])}
        job = {'id': jid, 'logistics_id': logistics_id, 'fingerprint': prepared['fingerprint'],
               'approval_no': prepared['logistics']['approval_no'], 'status': 'queued', 'stage': 'rules',
               'actor': actor, 'started_at': utcnow(), 'total': len(prepared['expenses']),
               'processed': 0, 'recommended': 0, 'no_match': 0, 'failed': 0,
               'results': {eid: r for eid, r in previous.get('results', {}).items()
                           if r['outcome'] != 'recommended' or eid in active}}
        # Imported global candidates with current snapshots are already durable evidence.
        existing = candidates(store, prepared['logistics'])
        if current['status'] == 'not_started' and existing and (
                len(existing) == 1 and existing[0]['status'] == 'pending' and existing[0]['method'] != 'container'
                or all(any(p.get('type') == 'deepseek' for p in c['evidence']) for c in existing)):
            job.update(status='completed', stage='saved', recommended=len(existing), finished_at=utcnow(),
                       results={c['expense_id']: {'outcome': 'recommended', 'response': None} for c in existing},
                       message='已读取本票保存的匹配候选，请核对后确认关联')
        ai.save(store, job)
        ai.save(store, {'id': digest('batch-match-pointer', logistics_id), 'job_id': jid})
        return public(job, cached=job['status'] == 'completed')


def rule_pass(store, prepared):
    logistics = prepared['logistics']
    lid = logistics['id']
    for e in prepared['expenses']:
        if [e['id'], lid] in prepared['rejected_pairs']:
            continue
        proof = [{'type': t, 'value': token} for t, token in e['identifiers']
                 if [t, token] in logistics['identifiers'] or (t, token) in logistics['identifiers']]
        if logistics['instance'] in e['related']:
            proof.append({'type': 'explicit', 'value': logistics['instance']})
        if not proof:
            continue
        old = store.get('candidate', digest(lid, e['id']))
        if old and old in candidates(store, logistics):
            continue
        method = 'explicit' if any(p['type'] == 'explicit' for p in proof) else 'identifier' if any(p['type'] != 'container' for p in proof) else 'container'
        conflicting = bool(eligible_logistics(store, e) - {lid})
        evidence_hash = digest(e['match_hash'], logistics['match_hash'], proof)
        save_candidate(store, {'id': digest(lid, e['id']), 'logistics_id': lid, 'expense_id': e['id'],
            'status': 'conflict' if conflicting else 'pending', 'method': method, 'evidence': proof,
            'evidence_hash': evidence_hash, 'logistics_snapshot': logistics['snapshot'], 'expense_snapshot': e['snapshot'],
            'revision': digest(logistics['snapshot'], e['snapshot'], evidence_hash),
            'reason': '同一采购支出命中其他物流，请核对' if conflicting else ''})
    normalize_conflicts(store, logistics)
    return candidates(store, logistics)


def normalize_conflicts(store, logistics):
    current = candidates(store, logistics)
    for c in current:
        others = [o for o in store.find('candidate', expense_id=c['expense_id'])
                  if o['logistics_id'] != logistics['id'] and o['status'] in {'pending', 'conflict'}]
        if len(current) > 1 or others or eligible_logistics(store, store.get('source', c['expense_id'])) - {logistics['id']}:
            if c['status'] != 'conflict':
                c.update(status='conflict', reason='一对一匹配存在其他候选，请逐项核对', revision=digest(c['revision'], 'conflict'))
                save_candidate(store, c)


def source_summary(source):
    result = ai.summary(source)
    # Only this source's cached evidence. No downloads, arbitrary URLs or other
    # logistics bodies are assembled into this request.
    raw = source.get('raw') or {}
    comments = [c for key in ('comments', 'operationRecords') for c in (raw.get(key) or [])
                if isinstance(raw.get(key), list) and isinstance(c, dict)]
    result['comments'] = [ai.safe_text(c.get('text') or c.get('content') or c.get('comment') or c.get('remark'), 400)
                          for c in comments][-20:]
    result['documents'] = [{'name': ai.safe_text(d.get('file_name'), 150),
                            'text': ai.safe_text((d.get('preview') or {}).get('text_excerpt'), 1000)}
                           for d in source.get('documents', [])[:15]]
    return result


def groups_for(prepared, expenses):
    base = {'logistics': [source_summary(prepared['logistics'])], 'rejected_pairs': prepared['rejected_pairs']}
    rows = []
    for expense in expenses:
        summary = source_summary(expense)
        if rows and (len(rows) >= 30 or len(dumps({**base, 'expenses': rows + [summary]})) > 120_000):
            yield {**base, 'expenses': rows}
            rows = []
        rows.append(summary)
    if rows:
        yield {**base, 'expenses': rows}


def needs_ai(store, logistics_id, expense):
    active = [c for c in store.find('candidate', expense_id=expense['id'])
              if c['status'] in {'pending', 'conflict'} and c['expense_snapshot'] == expense['snapshot']
              and (other := store.get('source', c['logistics_id'])) and not other['invalid']
              and c['logistics_snapshot'] == other['snapshot']]
    return not (len(active) == 1 and active[0]['logistics_id'] != logistics_id and
                active[0]['status'] == 'pending' and active[0]['method'] != 'container'
                and len(eligible_logistics(store, expense)) <= 1)


def run(store, job_id, call_model, model=''):
    with store.atomic():
        store.get('state', 'match_lock', lock=True)
        job = store.get('state', job_id, lock=True)
        if not job or job['status'] != 'queued':
            return public(job or {})
        prepared = prepare(store, job['logistics_id'])
        if prepared['fingerprint'] != job['fingerprint'] or store.find('binding', logistics_id=job['logistics_id']):
            job.update(status='stale', finished_at=utcnow()); ai.save(store, job)
            return public(job)
        existing = rule_pass(store, prepared)
        clear = (not job['results'] and len(existing) == 1 and existing[0]['status'] == 'pending'
                 and existing[0]['method'] in {'explicit', 'identifier', 'manual'})
        if clear:
            job.update(status='completed', stage='rules', recommended=1, finished_at=utcnow(), message='规则已找到本票候选，等待确认')
            ai.save(store, job)
        else:
            job.update(status='running', stage='deepseek', claim=uuid.uuid4().hex, model=model, failed=0, error=None)
            ai.save(store, job)
    store.commit()
    if clear:
        return public(job)
    try:
        todo = [e for e in prepared['expenses'] if e['id'] not in job['results'] and
                [e['id'], job['logistics_id']] not in prepared['rejected_pairs'] and needs_ai(store, job['logistics_id'], e)]
        for group in groups_for(prepared, todo):
            # Validate before each paid chunk as well as after it.
            if status(store, job['logistics_id'])['status'] not in {'running'}:
                job.update(status='stale'); break
            # End the preflight read view before network I/O. Under MariaDB
            # REPEATABLE READ the post-call fence must observe a fresh snapshot.
            store.commit()
            response = call_model(ai.messages(group))
            with store.atomic():
                store.get('state', 'match_lock', lock=True)
                current = store.get('state', job_id, lock=True)
                if current.get('claim') != job['claim']:
                    return public(current)
                if prepare(store, job['logistics_id'])['fingerprint'] != job['fingerprint'] or store.find('binding', logistics_id=job['logistics_id']):
                    job.update(status='stale'); break
                proposals = response.get('matches', []) if isinstance(response, dict) else []
                for e in group['expenses']:
                    rows = [p for p in proposals if isinstance(p, dict) and p.get('expense_id') == e['id']] if isinstance(proposals, list) else []
                    try:
                        if len(rows) != 1:
                            raise ValueError(_('AI 未唯一返回此采购支出的结果'))
                        outcome = ai.adopt_suggestion(store, job, group, e['id'], rows[0])
                        if outcome == 'no_match':
                            previous = store.get('candidate', digest(job['logistics_id'], e['id']))
                            if previous and previous['method'] == 'deepseek' and previous['status'] in {'pending', 'conflict'}:
                                previous.update(status='stale', reason='本轮 AI 未推荐该组合')
                                save_candidate(store, previous)
                        job['results'][e['id']] = {'outcome': outcome, 'response': rows[0]}
                    except (ValueError, InvalidOperation) as exc:
                        job['failed'] += 1; job['error'] = str(exc)
                normalize_conflicts(store, prepared['logistics'])
                ai.save(store, job)
            store.commit()
        if job['status'] != 'stale':
            job.update(status='partial' if job['failed'] else 'completed')
        job.update(processed=len(job['results']), recommended=sum(r['outcome'] == 'recommended' for r in job['results'].values()),
                   no_match=sum(r['outcome'] == 'no_match' for r in job['results'].values()), finished_at=utcnow())
    except Exception as exc:
        job.update(status='failed', error=str(exc)[:500], finished_at=utcnow())
    with store.atomic():
        current = store.get('state', job_id, lock=True)
        if current.get('claim') != job['claim']:
            return public(current)
        ai.save(store, job)
        store.audit('', 'batch_matching', job['actor'], job_id=job_id, logistics_id=job['logistics_id'], status=job['status'])
    store.commit()
    return public(job)
