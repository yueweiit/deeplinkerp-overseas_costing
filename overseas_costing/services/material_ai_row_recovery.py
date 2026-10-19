"""Preview-first recovery for legacy AI whole-table row replacements."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from datetime import datetime

from .logistics_settlement.application import row_meta
from .logistics_settlement.item_metadata import persist_item_meta
from .logistics_settlement.model import digest, dumps, identity
from .logistics_settlement.writer import DERIVED_FIELDS, clean_copy, clone_version_children
from overseas_costing.utils.field_mapper import normalize_unit


POLICY = 'material-row-recovery-1'
PUBLIC_FIELDS = ('material_code','product_name','spec_model','quantity','actual_shipped_qty','unit','shipped_uom',
                 'package_count','net_weight_kg','gross_weight_kg','volume_m3','project_collection')


def _replacement_audit(store, batch_name, current_version):
    rows=[row for row in store.find('audit',binding_id=batch_name,action='ai_rows_adopted')
          if row.get('version')==current_version and row.get('old_version') and row.get('mode')=='replace_all']
    if not rows:
        raise ValueError(_('当前版本没有可恢复的 AI 整表替换记录。'))
    return max(rows,key=lambda row:str(row.get('created_at') or ''))


def _identity_key(row):
    return (identity(row.get('material_code')),identity(row.get('spec_model')),
            normalize_unit(row.get('shipped_uom') or row.get('unit') or row.get('purchase_uom') or ''))


def _matches(current, historical):
    meta=row_meta(current)
    original=meta.get('ai_fill_original_values') or meta.get('settlement_original_values') or {}
    original_name=str(original.get('name') or meta.get('settlement_origin_item') or '')
    if original_name:
        direct=[row for row in historical if str(row.get('name') or '')==original_name]
        if direct:return direct
    stable=str(current.get('stable_line_key') or '')
    if stable:
        direct=[row for row in historical if str(row.get('stable_line_key') or '')==stable]
        if direct:return direct
    key=_identity_key(current)
    return [row for row in historical if key!=('','','') and _identity_key(row)==key]


def _public_row(row, action):
    return {'action':action,**{field:deepcopy(row.get(field)) for field in PUBLIC_FIELDS}}


def _build_preview(store,ledger,batch_name,current_version):
    batch=ledger.get('batch',batch_name) or {}
    version=ledger.get('version',current_version) or {}
    if batch.get('current_version')!=current_version or version.get('batch')!=batch_name:
        raise ValueError(_('当前成本版本已变化，请刷新后重试。'))
    if version.get('status')!='Active' or batch.get('confirm_status')=='Confirmed' or batch.get('writeback_status')=='Success':
        raise ValueError(_('已确认或归档的版本不能恢复物料行。'))
    audit=_replacement_audit(store,batch_name,current_version)
    old_version=str(audit['old_version'])
    historical=sorted(ledger.rows('item',batch=batch_name,version=old_version),
                      key=lambda row:(int(row.get('row_no') or 0),str(row.get('name') or '')))
    current=sorted(ledger.rows('item',batch=batch_name,version=current_version),
                   key=lambda row:(int(row.get('row_no') or 0),str(row.get('name') or '')))
    if not historical or not current:
        raise ValueError(_('历史物料或当前物料为空，无法安全恢复。'))
    matched={};issues=[]
    for row in current:
        candidates=_matches(row,historical)
        if len(candidates)!=1:
            issues.append(f"{row.get('material_code') or row.get('product_name') or row.get('name')}：无法唯一匹配替换前物料。")
            continue
        target=candidates[0]['name']
        if target in matched:
            issues.append(f"{row.get('material_code') or row.get('product_name') or row.get('name')}：多行对应同一历史物料。")
            continue
        matched[target]=row
    rows=[];plan=[]
    for old in historical:
        current_row=matched.get(old['name'])
        source=current_row or old
        action='keep_updated' if current_row else 'restore'
        rows.append(_public_row(source,action))
        plan.append({'action':action,'historical_item':old['name'],
                     'current_item':current_row.get('name') if current_row else ''})
    revision=digest(POLICY,batch_name,current_version,old_version,audit.get('id'),current,historical,plan)
    return {'ok':True,'policy':POLICY,'id':digest(POLICY,'preview',revision),'revision':revision,
            'batch':batch_name,'current_version':current_version,'old_version':old_version,
            'current_count':len(current),'restored_count':sum(1 for row in plan if row['action']=='restore'),
            'before_rows':[_public_row(row,'current') for row in current],'after_rows':rows,'rows':rows,
            'issues':issues,'can_confirm':not issues and bool(plan),'plan':plan,'audit_id':audit.get('id')}


def _state_id(preview_id):
    return digest(POLICY,'preview-state',preview_id)


def _result_id(preview_id):
    return digest(POLICY,'result-state',preview_id)


def preview_recovery(store,ledger,batch_name,current_version):
    preview=_build_preview(store,ledger,str(batch_name),str(current_version))
    store.put('state',{'id':_state_id(preview['id']),'updated_at':datetime.now().isoformat(),
        'data':dumps({key:preview[key] for key in ('id','revision','batch','current_version','old_version','audit_id')})})
    return {key:value for key,value in preview.items() if key not in ('plan','audit_id')}


def confirm_recovery(store,ledger,batch_name,current_version,preview_id,revision,actor):
    existing=store.get('state',_result_id(preview_id))
    if existing:
        return {**existing.get('result',{}),'ok':True,'idempotent':True}
    issued=store.get('state',_state_id(preview_id)) or {}
    if issued.get('revision')!=revision or issued.get('batch')!=batch_name:
        raise ValueError(_('恢复预览已过期，请重新生成。'))
    with store.atomic():
        batch=ledger.get('batch',batch_name,lock=True) or {}
        if batch.get('current_version')!=current_version:
            existing=store.get('state',_result_id(preview_id))
            if existing:return {**existing.get('result',{}),'ok':True,'idempotent':True}
            raise ValueError(_('当前成本版本已变化，本次未恢复。'))
        current=ledger.get('version',current_version,lock=True) or {}
        preview=_build_preview(store,ledger,batch_name,current_version)
        if preview['id']!=preview_id or preview['revision']!=revision or not preview['can_confirm']:
            raise ValueError(_('历史或当前物料已变化，请重新预览。'))
        values=clean_copy(current)
        values.update(version_code='RECOVER-'+datetime.now().strftime('%Y%m%d%H%M%S%f'),version_type='Adjustment',
            status='Active',is_current=1,source_type='Clone',calculated_at=None,summary_snapshot_json='{}',rule_snapshot_json='[]',
            remark='恢复 AI 整表替换前未选物料，保留已更新行')
        recovered_version=ledger.create('version',values)
        copies=clone_version_children({kind:ledger.rows(kind,batch=batch_name,version=current_version)
            for kind in ('item','rule','evidence','component')},recovered_version['name'],ledger.create,ledger.put)
        historical={row['name']:row for row in ledger.rows('item',batch=batch_name,version=preview['old_version'])}
        for row_no,entry in enumerate(preview['plan'],1):
            if entry['action']=='keep_updated':
                name=copies['item'][entry['current_item']]
                ledger.put('item',name,{'row_no':row_no})
                continue
            old=historical[entry['historical_item']]
            item_values=clean_copy(old);item_values.update(version=recovered_version['name'],row_no=row_no,
                stable_line_key=str(old.get('stable_line_key') or 'recovered:'+old['name']),**{field:0 for field in DERIVED_FIELDS})
            meta=row_meta(old);meta['material_row_recovery']={'preview_id':preview_id,'historical_item':old['name'],
                'recovered_at':datetime.now().isoformat(),'recovered_by':str(actor or '')}
            item_values['extra_json']=persist_item_meta(meta)
            ledger.create('item',item_values)
        metadata=row_meta(recovered_version);metadata.pop('ai_row_adoption',None)
        metadata['material_row_recovery']={'preview_id':preview_id,'revision':revision,
            'from_version':current_version,'historical_version':preview['old_version'],
            'restored_count':preview['restored_count'],'actor':str(actor or ''),'confirmed_at':datetime.now().isoformat()}
        ledger.put('version',recovered_version['name'],{'extra_json':dumps(metadata),'calculated_at':None,
            'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
        ledger.put('version',current_version,{'is_current':0})
        count=len(ledger.rows('item',batch=batch_name,version=recovered_version['name']))
        ledger.put('batch',batch_name,{'current_version':recovered_version['name'],'status':'Dirty','confirm_status':'Pending',
            'writeback_status':'Not Started','version_count':len(ledger.rows('version',batch=batch_name)),'item_count':count})
        result={'ok':True,'idempotent':False,'version_name':recovered_version['name'],
                'restored_count':preview['restored_count'],'item_count':count}
        store.audit(batch_name,'material_rows_recovered',actor,preview_id=preview_id,revision=revision,
            old_version=current_version,version=recovered_version['name'],historical_version=preview['old_version'],
            restored_count=preview['restored_count'])
        store.put('state',{'id':_result_id(preview_id),'updated_at':datetime.now().isoformat(),
            'data':dumps({'result':result})})
        return result
