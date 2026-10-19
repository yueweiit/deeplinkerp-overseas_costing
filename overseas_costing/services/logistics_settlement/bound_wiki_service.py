"""Bounded background refresh and adoption gates for explicitly linked Wiki sheets."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

import hashlib
import uuid

from .jobs import utcnow
from .model import digest,dumps
from .matching import save_binding

CURSOR_ID='bound_wiki_refresh_cursor'
CHANGE_MESSAGE='当前采购支出关联的装箱工作表已更新，请重新预览并采用。'


def _current_pending(store,binding):
    if not binding:
        return {}
    source=store.get('source',binding['expense_id']) or {}
    return {key:value for key,value in (binding.get('wiki_pending') or {}).items()
            if value.get('source_snapshot')==source.get('snapshot') and value.get('source_id')==source.get('id')}


def pending_wiki_issues(store,binding):
    return [CHANGE_MESSAGE] if _current_pending(store,binding) else []


def mark_wiki_changed(store,ledger,bundle,source_id,old_sha,new_sha,actor):
    """Called under the refresh's short source/binding transaction, never over I/O."""
    binding=dict(bundle['binding'])
    context=bundle['context']
    pending=_current_pending(store,binding)
    pending[source_id]={'source_id':context['root_source_id'],'source_snapshot':context['source_snapshot'],
                        'old_sha':old_sha,'new_sha':new_sha,'changed_at':utcnow()}
    binding.update(wiki_pending=pending,revision=int(binding['revision'])+1,application_status='pending',
                   issues=sorted(set([*(binding.get('issues') or []),CHANGE_MESSAGE])))
    save_binding(store,binding)
    # The confirmed version/items remain untouched. The normal apply path creates
    # its one adjustment draft and stops at pending_wiki_issues before adoption.
    ledger.put('batch',bundle['batch']['name'],{'status':'Dirty'})
    store.audit(binding['id'],'wiki_source_changed',actor,source_id=source_id,old_sha=old_sha,new_sha=new_sha,
                source_snapshot=context['source_snapshot'],binding_revision=binding['revision'])
    return binding


def acknowledge_wiki_review(store,binding,evidence,expected_context,*,complete,actor):
    """Clear only the freshly reparsed, fully reviewed current sheet's gate."""
    from overseas_costing.services.packing_source_service import load_bound_wiki_snapshot
    source_id=str(evidence.get('source_id') or '')
    pending=_current_pending(store,binding)
    if not pending or source_id not in pending:
        return
    if not complete or not evidence.get('full_table') or evidence.get('source_kind')!='wiki_sheet':
        raise ValueError(_('已变化的装箱工作表需要重新核对完整资料。'))
    cached=load_bound_wiki_snapshot(expected_context,source_id,store=store) or {}
    sha=cached.get('source_hash')
    expected_hash=hashlib.sha256(f"{sha}|{expected_context['fingerprint']}".encode()).hexdigest()
    if not sha or sha!=pending[source_id]['new_sha'] or evidence.get('source_hash')!=expected_hash:
        raise ValueError(_('装箱工作表已再次刷新，请重新预览后采用。'))
    pending.pop(source_id)
    binding['wiki_pending']=pending
    if not pending:
        binding['issues']=[issue for issue in binding.get('issues') or [] if issue!=CHANGE_MESSAGE]
    store.audit(binding['id'],'wiki_source_reviewed',actor,source_id=source_id,source_hash=sha,
                source_snapshot=expected_context['source_snapshot'])


def record_refresh_health(store,binding,source,source_id,*,error=''):
    key=digest('bound_wiki_refresh_health',binding['id'],source_id)
    prior=store.get('state',key) or {}
    if prior.get('root_source_id')!=source.get('id') or prior.get('source_snapshot')!=source.get('snapshot'):
        prior={}
    now=utcnow()
    store.put('state',{'id':key,'updated_at':now,'data':dumps({
        'binding_id':binding['id'],'sheet':source_id,'root_source_id':source.get('id'),
        'source_snapshot':source.get('snapshot'),'last_checked_at':now,
        'last_success_at':prior.get('last_success_at') if error else now,
        'status':'failed' if error else 'success','last_error':str(error)[:1000]})})


def refresh_bound_wiki_round(store,ledger,*,refresh=None,limit=20,scan_limit=50):
    """One durable round, advancing past failed sheets and unrelated bindings."""
    from overseas_costing.services.effective_logistics_source import explicit_wiki_sources
    from overseas_costing.services.packing_source_service import refresh_bound_wiki_snapshot
    refresh=refresh or refresh_bound_wiki_snapshot
    limit=max(1,min(20,int(limit)));scan_limit=max(1,min(50,int(scan_limit)))
    token=uuid.uuid4().hex
    with store.atomic():
        store.get('state','job_lock',lock=True)
        cursor=store.get('state',CURSOR_ID,lock=True) or {}
        cursor.update(token=token,status='running',last_started_at=utcnow())
        store.put('state',{'id':CURSOR_ID,'updated_at':utcnow(),'data':dumps(cursor)})
    store.commit()  # Standalone scheduler work: no source locks span archive I/O.
    result={'checked':0,'failed':0,'changed':0,'scanned':0}
    binding_cursor=cursor.get('binding_cursor');sheet_cursor=cursor.get('sheet_cursor')
    bindings=[]
    if binding_cursor and sheet_cursor:
        current=store.get('binding',binding_cursor)
        if current: bindings.append(current)
    if len(bindings)<scan_limit:
        bindings+=store.find('binding',after=binding_cursor,limit=scan_limit-len(bindings))

    def progress(binding_id,sheet_id,*,finished=False):
        with store.atomic():
            store.get('state','job_lock',lock=True)
            current=store.get('state',CURSOR_ID,lock=True) or {}
            if current.get('token')!=token:
                return False
            current.update(binding_cursor=binding_id,sheet_cursor=sheet_id,status='idle' if finished else 'running',
                           last_checked_at=utcnow(),last_result=result)
            store.put('state',{'id':CURSOR_ID,'updated_at':utcnow(),'data':dumps(current)})
        store.commit()
        return True

    for binding in bindings:
        result['scanned']+=1
        source=store.get('source',binding['expense_id']) or {}
        mapping=store.find('batch_map',source_id=binding['logistics_id'],limit=1)
        ids=sorted(explicit_wiki_sources(source)) if mapping and source.get('kind')=='expense' and source.get('approved') and not source.get('invalid') else []
        if binding['id']==binding_cursor and sheet_cursor:
            ids=[source_id for source_id in ids if source_id>sheet_cursor]
        for source_id in ids:
            control=store.get('state','control')
            if control and not control.get('enabled'):
                current=store.get('state',CURSOR_ID) or {}
                progress(current.get('binding_cursor'),current.get('sheet_cursor'),finished=True)
                return {**result,'paused':True}
            error=''
            try:
                refreshed=refresh(mapping[0]['batch'],source_id,store=store,ledger=ledger,actor='wiki-sync')
                result['changed']+=bool(refreshed.get('changed'))
            except Exception as exc:
                error=str(exc)[:1000]
                result['failed']+=1
            result['checked']+=1
            record_refresh_health(store,binding,source,source_id,error=error)
            if not progress(binding['id'],source_id,finished=result['checked']>=limit):
                return {**result,'superseded':True}
            if result['checked']>=limit:
                return result
        if not progress(binding['id'],None):
            return {**result,'superseded':True}
    # Wrap only after reaching the end of the bounded binding inventory.
    progress(None if len(bindings)<scan_limit else bindings[-1]['id'],None,finished=True)
    return result


def scheduled_refresh_bound_wiki():
    from .runtime import enabled
    if not enabled():
        return {'skipped':True,'message':'物流结算同步未启用或已暂停'}
    from .store import Store
    from .ledger import FrappeLedger
    return refresh_bound_wiki_round(Store.frappe(),FrappeLedger())
