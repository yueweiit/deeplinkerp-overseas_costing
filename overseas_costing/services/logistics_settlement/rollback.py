"""Operator recovery by application beforeimage; historical versions remain frozen."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from decimal import Decimal
from .application import row_meta
from .jobs import utcnow, pause_job
from .matching import save_binding
from .model import dumps
from .item_metadata import persist_item_meta
from .writer import clean_copy, GOODS_FIELDS, DERIVED_FIELDS, PACKING_FIELDS, application_context, locked, mutable_version, has_material_supplements


def origin(item):
    return row_meta(item).get('settlement_origin_item') or item['name']


def restore_source_metadata(evidence, target):
    # The chosen application owns the final source facts; retain newer packing evidence.
    metadata = {k: v for k, v in row_meta(evidence).items()
                if not k.startswith('settlement_') or k.startswith('settlement_packing_') or k == 'settlement_origin_item'}
    metadata.update({k: v for k, v in row_meta(target).items()
                     if k.startswith('settlement_') and not k.startswith('settlement_packing_') and k != 'settlement_origin_item'})
    return metadata


def restore_application(store, ledger, application_id, expected_revision, reason, actor):
    if not str(reason).strip():
        raise ValueError(_('请填写回退原因'))
    with store.atomic():
        # Stop jobs before taking source locks, matching the worker's lock order.
        store.get('state', 'job_lock', lock=True)
        for status in ('queued','running'):
            for job in store.find('job', status=status):
                pause_job(store, job['id'])
        store.get('state', 'match_lock', lock=True)
        application = store.get('application', application_id, lock=True)
        if not application or application['status'] != 'applied':
            raise ValueError(_('应用记录不存在或不可恢复'))
        binding = store.get('binding', application['binding_id'], lock=True)
        if not binding or binding['revision'] != int(expected_revision):
            raise ValueError(_('当前关联已变化，请刷新后选择应用记录'))
        if application.get('expense_id') != binding['expense_id']:
            raise ValueError(_('此应用属于已经更正的旧关联；请先按更正流程恢复关联'))
        store.get('source', binding['expense_id'], lock=True)
        store.get('source', binding['logistics_id'], lock=True)
        batch = application_context(store, ledger, binding)
        if not batch or locked(batch):
            raise ValueError(_('批次正在编辑或尚未对应，请结束编辑后回退'))
        version = mutable_version(ledger, batch)
        current_items = ledger.rows('item', batch=batch['name'], version=version['name'])
        before = application.get('before') or {}
        targets = {origin(i): i for i in before.get('items', [])}
        restored = set()
        for item in current_items:
            meta = row_meta(item)
            if meta.get('settlement_binding_id') != binding['id']:
                continue
            key = origin(item)
            target = targets.get(key)
            if target:
                applied = meta.get('settlement_applied_values') or {}
                values = {field: target.get(field) for field in GOODS_FIELDS if str(item.get(field) or '') == str(applied.get(field) or '')}
                if 'quantity' in values and meta.get('goods_value_source') == 'derived_quantity_unit_price':
                    values['goods_value'] = str(Decimal(str(values['quantity'] or 0)) * Decimal(str(item.get('unit_price') or 0)))
                meta = restore_source_metadata(item, target)
                meta['settlement_rollback_application'] = application_id
                meta['settlement_packing_review'] = True
                values.update(extra_json=persist_item_meta(meta), **{field:0 for field in DERIVED_FIELDS})
                ledger.put('item',item['name'],values)
                restored.add(key)
            elif meta.get('settlement_created') and not has_material_supplements(item):
                ledger.delete('item',item['name'])
            else:
                meta.pop('settlement_cargo',None)
                meta.pop('settlement_valuation',None)
                meta.update(settlement_packing_review=True, settlement_rollback_application=application_id)
                ledger.put('item',item['name'],{'extra_json':persist_item_meta(meta), 'manual_override_flag':1, **{field:0 for field in DERIVED_FIELDS}})
        existing_origins = {origin(i) for i in ledger.rows('item', batch=batch['name'], version=version['name'])}
        for key,target in targets.items():
            if key in existing_origins:
                continue
            evidence = (binding.get('retired_items') or {}).get(key) or target
            values = clean_copy(evidence)
            values.update({field:target.get(field) for field in GOODS_FIELDS if field not in {'quantity','unit'}})
            meta = restore_source_metadata(evidence, target)
            meta.update(settlement_origin_item=key, settlement_rollback_application=application_id, settlement_packing_review=True)
            values.update(version=version['name'], extra_json=persist_item_meta(meta), **{field:0 for field in DERIVED_FIELDS})
            ledger.create('item',values)
        # Prior final fees may be restored for review; initial estimates stay disabled.
        for rule in ledger.rows('rule',batch=batch['name'],version=version['name']):
            if rule.get('source_binding_id') == binding['id']:
                ledger.put('rule',rule['name'],{'is_final':0,'is_active':0,'is_enabled':0})
        for rule in before.get('rules',[]):
            if rule.get('is_final') and rule.get('source_binding_id') == binding['id']:
                ledger.create('rule',{**clean_copy(rule),'version':version['name']})
        binding.update(application_status='rolled_back', revision=binding['revision']+1, applied_hash=None, applied_cost_hash=None,
                       version=version['name'], issues=['已按应用记录回退并暂停同步；请核对调整草稿后重新采用最终来源'])
        save_binding(store,binding)
        store.put('state',{'id':'control','updated_at':utcnow(),'data':dumps({'enabled':False,'rollback_application':application_id})})
        ledger.put('version',version['name'],{'calculated_at':None,'summary_snapshot_json':'{}','rule_snapshot_json':'[]'})
        ledger.put('batch',batch['name'],{'status':'Dirty','confirm_status':'Pending','is_locked':0,'item_count':len(ledger.rows('item',batch=batch['name'],version=version['name']))})
        store.audit(binding['id'],'restore_application',actor,application_id=application_id,version=version['name'],reason=reason,before_items=current_items)
        return binding
