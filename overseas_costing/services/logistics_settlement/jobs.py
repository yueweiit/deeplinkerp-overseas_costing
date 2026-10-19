"""有界后台状态机；清单、失败和游标先持久化，随后才推进阶段。"""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from datetime import datetime, timedelta, timezone

from .matching import match_source, confirm_candidate, refresh_candidate_snapshots
from .model import digest, dumps, parse_source


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def overlap(value):
    return (datetime.fromisoformat(value) - timedelta(hours=24)).isoformat()


def save_job(store, job):
    store.put('job', {'id': job['id'], 'job_key': job['job_key'], 'status': job['status'], 'phase': job['phase'], 'data': dumps(job)})
    return job


def start_job(store, *, mode, actor, start='', end='', now=None, request_key=None):
    if mode not in {'initialize', 'incremental', 'reconcile'}:
        raise ValueError(_('不支持的同步任务'))
    now = now or utcnow()
    # One active job prevents initialization and increment from racing the watermark.
    with store.atomic():
        store.get('state', 'job_lock', lock=True)
        for status in ('queued', 'running', 'paused'):
            jobs = store.find('job', status=status, limit=1)
            if jobs:
                return jobs[0]
        state = store.get('state', 'sync') or {}
        key = digest(mode, request_key or ('initial' if mode == 'initialize' else now), start, end)
        if store.get('job', key):
            return store.get('job', key)
        job = {'id': key, 'job_key': key, 'status': 'queued', 'phase': 'inventory', 'mode': mode, 'actor': actor, 'started_at': now, 'upper': now, 'lower': overlap(state['watermark']) if mode == 'incremental' and state.get('watermark') else '', 'start': start, 'end': end, 'cursor': None, 'round': 0, 'caught_up': False}
        store.put('state', {'id': 'latest_job', 'updated_at': now, 'data': dumps({'job_id': key})})
        return save_job(store, job)


def run_step(store, archive, job_id, *, logistics_codes, now=None, apply_source=None, prepare_source=None, freight_mode=False):
    now = now or utcnow()
    with store.atomic():
        job = store.get('job', job_id, lock=True)
        if job['status'] in {'completed', 'partial', 'paused', 'failed'}:
            return job
        job['status'] = 'running'
        try:
            if job['phase'] == 'inventory':
                read_page = archive.inventory_page if job['mode'] == 'reconcile' else archive.page
                page = read_page(cursor=job['cursor'], limit=200, lower=job['lower'], upper=job['upper'], start=job['start'], end=job['end'])
                for raw in page['items']:
                    sid = digest(raw.get('corp_id'), raw.get('process_instance_id'))
                    iid = digest(job_id, sid)
                    values = {'id': iid, 'job_id': job_id, 'source_id': sid, 'status': 'pending', 'data': dumps({'raw': raw, 'round': job['round'], 'attempts': 0})}
                    if job['mode'] == 'reconcile':
                        existing = store.get('source', sid)
                        if existing and existing.get('archive_revision') == raw.get('archive_revision'):
                            job['unchanged_count'] = job.get('unchanged_count', 0) + 1
                        else:
                            values['data'] = dumps({'raw': raw, 'inventory_only': True, 'round': job['round'], 'attempts': 0})
                            store.put('job_item', values)
                    else:
                        store.put('job_item', values)
                    manifest_id = digest(job_id, sid, job['round'])
                    if not store.get('manifest', manifest_id):
                        store.insert('manifest', {'id': manifest_id, 'job_id': job_id, 'source_id': sid, 'round_no': job['round'], 'data': dumps({'raw': raw})})
                job['cursor'] = page['next_cursor']
                if not page['has_more']:
                    job['phase'] = 'load'
            elif job['phase'] == 'load':
                # One approval may download/parse several attachments. Return after
                # it finishes so the worker commits progress before starting another.
                items = store.find('job_item', job_id=job_id, status='pending', limit=1)
                references = [i for i in items if i.get('inventory_only')]
                fetched = {}
                if references:
                    pairs = [(i['raw']['corp_id'], i['raw']['process_instance_id']) for i in references]
                    fetched = {digest(r['corp_id'], r['process_instance_id']): r for r in archive.get_sources(pairs)}
                for item in items:
                    try:
                        with store.atomic():
                            if item.get('inventory_only'):
                                if item['source_id'] not in fetched:
                                    raise ValueError(_('来源暂时无法读取，保留原状态待重试'))
                                item['raw'] = fetched[item['source_id']]
                                item['inventory_only'] = False
                            parsed = parse_source(item['raw'], logistics_codes=logistics_codes)
                            previous = store.get('source', parsed['id'])
                            # Resume old unfiltered manifests without enriching or
                            # importing unrelated purchases. Keep adoption revocations.
                            tracked = previous and (previous['kind'] in {'logistics', 'expense'} or store.find('binding', expense_id=parsed['id'], limit=1))
                            if (parsed['kind'] == 'unclassified' or parsed['invalid']) and not tracked:
                                item.update(status='excluded', error='', attempts=item.get('attempts', 0) + 1)
                                store.put('job_item', {k: item[k] for k in ('id', 'job_id', 'source_id', 'status')} | {'data': dumps(item)})
                                continue
                            if prepare_source:
                                parsed = parse_source(prepare_source(item['raw']), logistics_codes=logistics_codes)
                            saved = store.ingest(parsed)
                            item['match_changed'] = item.get('force_match', False) or not previous or previous['match_hash'] != saved['match_hash']
                            if item['match_changed']:
                                from .matching import invalidate_source_candidates
                                invalidate_source_candidates(store, saved['id'])
                            else:
                                refresh_candidate_snapshots(store, saved['id'])
                            item['cost_changed'] = not previous or previous['cost_hash'] != saved['cost_hash']
                            item['status'] = 'loaded'
                            item['source_id'] = saved['id']
                            item['error'] = ''
                    except Exception as exc:
                        item['status'], item['error'] = 'failed', str(exc)
                    item['attempts'] = item.get('attempts', 0) + 1
                    store.put('job_item', {k: item[k] for k in ('id', 'job_id', 'source_id', 'status')} | {'data': dumps(item)})
                if not store.count('job_item', job_id=job_id, status='pending'):
                    job['phase'] = 'match'
            elif job['phase'] == 'match':
                for item in store.find('job_item', job_id=job_id, status='loaded', limit=200):
                    try:
                        with store.atomic():
                            if freight_mode:
                                from .freight_matching import match_changed_source
                                match_changed_source(store,item['source_id'])
                            elif item.get('match_changed'):
                                candidates = match_source(store, item['source_id'])
                                for candidate in candidates:
                                    if candidate['method'] == 'explicit' and candidate['status'] == 'pending':
                                        confirm_candidate(store, candidate['id'], candidate['revision'], 'source-explicit-link')
                            if apply_source:
                                apply_source(item['source_id'])
                            item['status'], item['error'] = 'done', ''
                    except Exception as exc:
                        item['status'], item['error'] = 'failed', str(exc)
                    store.put('job_item', {k: item[k] for k in ('id', 'job_id', 'source_id', 'status')} | {'data': dumps(item)})
                if store.count('job_item', job_id=job_id, status='pending'):
                    job['phase'] = 'load'
                elif not store.count('job_item', job_id=job_id, status='loaded'):
                    if job['mode'] == 'initialize' and job['round'] == 0:
                        job.update(phase='inventory', lower=overlap(job['started_at']), upper=now, cursor=None, round=1)
                    else:
                        failed = store.count('job_item', job_id=job_id, status='failed')
                        job.update(status='partial' if failed else 'completed', phase='finished', failed_count=failed, completed_at=now, caught_up=not failed and job['mode'] != 'reparse')
                        # Durable failures remain retryable even after checkpoint advancement.
                        previous_state = store.get('state', 'sync') or {}
                        if job['mode'] != 'reparse':
                            store.put('state', {'id': 'sync', 'updated_at': now, 'data': dumps({'watermark': max(job['upper'], previous_state.get('watermark') or ''), 'job_id': job_id, 'last_success': now if not failed else previous_state.get('last_success'), 'failed_count': failed})})
            job['processed_count'] = store.count('job_item', job_id=job_id, status='done')
            job['item_count'] = store.count('job_item', job_id=job_id)
            job['excluded_count'] = store.count('job_item', job_id=job_id, status='excluded')
            job['last_step_at'] = now
        except Exception as exc:
            job.update(status='failed', error=str(exc), last_step_at=now)
        return save_job(store, job)


def retry_job(store, job_id):
    with store.atomic():
        store.get('state', 'job_lock', lock=True)
        job = store.get('job', job_id, lock=True)
        if not job:
            raise ValueError(_('任务不存在'))
        for status in ('queued', 'running', 'paused'):
            if any(other['id'] != job_id for other in store.find('job', status=status)):
                raise ValueError(_('其他同步任务正在进行，请先完成当前任务再重试'))
        if job['status'] not in {'partial', 'failed', 'paused'}:
            return job
        for item in store.find('job_item', job_id=job_id, status='failed'):
            item.update(status='pending', force_match=True)
            store.put('job_item', {k: item[k] for k in ('id', 'job_id', 'source_id', 'status')} | {'data': dumps(item)})
        if job['phase'] == 'finished' or (job['phase'] == 'match' and store.count('job_item', job_id=job_id, status='pending')):
            job['phase'] = 'load'
        job['status'] = 'queued'
        return save_job(store, job)


def pause_job(store, job_id):
    with store.atomic():
        job = store.get('job', job_id, lock=True)
        if job['status'] in {'queued', 'running'}:
            job['status'] = 'paused'
            save_job(store, job)
        return job


def start_reparse_job(store, source_ids, *, actor):
    from .model import PARSER_VERSION
    source_ids = sorted(set(source_ids))
    if not source_ids or len(source_ids) > 200:
        raise ValueError(_('每次重新解析必须明确选择 1 至 200 个来源'))
    key = digest('reparse', PARSER_VERSION, source_ids)
    with store.atomic():
        store.get('state', 'job_lock', lock=True)
        existing = store.get('job', key)
        if existing:
            return existing
        if any(store.count('job', status=s) for s in ('queued','running','paused')):
            raise ValueError(_('请先完成或结束当前同步任务'))
        now = utcnow()
        job = {'id':key, 'job_key':key, 'status':'queued', 'phase':'load', 'mode':'reparse', 'actor':actor,
               'started_at':now, 'upper':now, 'lower':'', 'start':'', 'end':'', 'round':0, 'caught_up':False}
        for sid in source_ids:
            source = store.get('source', sid)
            if not source:
                raise ValueError(_('指定来源不存在'))
            raw = {'corp_id':source['corp'], 'process_instance_id':source['instance'], 'process_code':source['process_code'],
                   'business_id':source['approval_no'], 'status':source['status'], 'result':source.get('approval_result'),
                   'deleted_at':source.get('deleted_at'), 'updated_at':source['source_updated_at'],
                   'raw_payload':source['raw'], 'attachments':source['attachments'], 'archive_revision':source.get('archive_revision')}
            store.insert('job_item', {'id':digest(key,sid), 'job_id':key, 'source_id':sid, 'status':'pending', 'data':dumps({'raw':raw,'round':0,'attempts':0})})
            store.insert('manifest', {'id':digest(key,sid,0), 'job_id':key, 'source_id':sid, 'round_no':0, 'data':dumps({'raw':raw})})
        store.put('state', {'id':'latest_job', 'updated_at':now, 'data':dumps({'job_id':key})})
        return save_job(store, job)
