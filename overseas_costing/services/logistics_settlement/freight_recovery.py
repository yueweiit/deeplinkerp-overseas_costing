"""Administrator recovery of one fee application; packing stays independent."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from .application import row_meta,round_fee_rules
from .freight_adoption import context,rule_for,refresh_item_contexts
from .fee_policy import row_scopes
from .model import digest,dumps
from .jobs import pause_job,utcnow
from .writer import mutable_version,locked


def restore_fee_application(store,ledger,batch_name,version_name,application_id,expected_revision,reason,actor):
    if not str(reason).strip():raise ValueError(_('请填写恢复原因'))
    with store.atomic():
        store.get('state','job_lock',lock=True);store.get('state','match_lock',lock=True)
        app=store.get('freight_application',application_id,lock=True)
        batch=ledger.get('batch',batch_name,lock=True) or {}
        if not app or app['batch']!=batch_name or batch.get('current_version')!=version_name:raise ValueError(_('应用记录或当前版本不符'))
        current=context(store,ledger,batch_name,version_name)
        if current['revision']!=expected_revision or app['revision']!=expected_revision:raise ValueError(_('采用记录已变化；只能恢复当前采用记录的前一状态'))
        if locked(batch):raise ValueError(_('批次正在编辑，请结束编辑后恢复'))
        previous=(app.get('before') or {}).get('claims') or []
        for claim in previous:
            store.get('source',claim['source_id'],lock=True)
            occupied=store.find('freight_claim',charge_key=claim['charge_key'])
            if any(row['batch']!=batch_name for row in occupied):raise ValueError(_('原费用现已被其他票占用，不能直接恢复'))
        for status in ('queued','running'):
            for job in store.find('job',status=status):pause_job(store,job['id'])
        store.put('state',{'id':'control','updated_at':utcnow(),'data':dumps({'enabled':False,'recovery_application':application_id})})
        version=mutable_version(ledger,batch);vname=version['name']
        before={'claims':current['claims'],'rules':ledger.rows('rule',batch=batch_name,version=vname),'version':version}
        store.sql('DELETE FROM oc_ls_freight_claim WHERE batch=%s',(batch_name,))
        for claim in previous:
            store.put('freight_claim',{k:claim[k] for k in ('id','batch','logistics_id','source_id','line_id','charge_key')}|{'data':dumps(claim)})
        for rule in before['rules']:
            if 'freight' in row_scopes(rule):ledger.put('rule',rule['name'],{'is_active':0,'is_enabled':0,'is_final':0})
        rules=[rule_for(c) for c in previous];round_fee_rules(rules)
        for rule in rules:
            existing=next((r for r in before['rules'] if r.get('rule_code')==rule['rule_code']),None)
            values={**rule,'batch':batch_name,'version':vname}
            ledger.put('rule',existing['name'],values) if existing else ledger.create('rule',values)
        revision=digest('freight-recovery',application_id,expected_revision)
        meta=row_meta(version);freight=meta.get('freight_settlement') or {}
        freight.update(claims=previous,revision=revision,epoch=revision,recovery_pending=True)
        meta['freight_settlement']=freight
        ledger.put('version',vname,{'extra_json':dumps(meta),'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
        ledger.put('batch',batch_name,{'status':'Dirty','confirm_status':'Pending','is_locked':0})
        ctx=refresh_item_contexts(store,ledger,batch_name,vname)
        recovered={'id':digest('freight-recovery',batch_name,vname,revision),'batch':batch_name,'version':vname,'revision':revision,
            'before':before,'claims':previous,'source_context':ctx,'actor':actor,'reason':str(reason),'applied_at':utcnow(),'recovered_application':application_id}
        store.insert('freight_application',{k:recovered[k] for k in ('id','batch','version','revision')}|{'data':dumps(recovered)})
        store.audit(batch_name,'freight_application_recovered',actor,application_id=application_id,reason=str(reason),version=vname)
        return {'status':'pending','version':vname,'revision':revision,'message':'已恢复指定费用采用前的状态并暂停同步；装箱未改变，请核对后重新采用实际费用'}
