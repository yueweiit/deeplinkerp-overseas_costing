"""Apply one confirmed source atomically; freeze confirmed versions and retain beforeimages."""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from datetime import datetime
from decimal import Decimal

from .application import plan_application, row_meta
from .item_metadata import persist_item_meta
from .matching import save_binding
from .model import digest, dumps, timestamp
from .jobs import utcnow
from .valuation import value_final_cargo

GOODS_FIELDS = ('material_code', 'product_name', 'spec_model', 'quantity', 'unit')
SYSTEM_FIELDS = {'name', 'doctype', 'owner', 'creation', 'modified', 'modified_by', 'docstatus', 'idx', '_user_tags', '_comments', '_assign', '_liked_by'}
DERIVED_FIELDS = ('freight_alloc_rmb', 'freight_alloc_mxn', 'total_logistics_mxn', 'total_cost_rmb', 'total_unit_rmb', 'total_cost_mxn', 'total_unit_mxn')


def clean_copy(doc):
    return {k: v for k, v in doc.items() if k not in SYSTEM_FIELDS and not k.startswith('_')}


def locked(batch):
    from overseas_costing.services.edit_session_service import lock_is_active
    return lock_is_active(batch)


def clone_version_children(rows_by_kind, version_name, create, update):
    """Clone independent fee evidence and row scopes for automatic and manual drafts."""
    cloned = {kind: {} for kind in ('item', 'rule', 'evidence', 'component')}
    copies = {kind: [] for kind in cloned}
    for kind in cloned:
        for doc in rows_by_kind.get(kind, []):
            values = clean_copy(doc)
            values['version'] = version_name
            if kind == 'item':
                values['stable_line_key'] = str(doc.get('stable_line_key') or '').strip() or 'legacy:' + doc['name']
                meta = row_meta(doc)
                meta['settlement_origin_item'] = meta.get('settlement_origin_item') or doc['name']
                values['extra_json'] = persist_item_meta(meta)
            for field, target in [('item', 'item'), ('fee_rule', 'rule'), ('evidence', 'evidence')]:
                if values.get(field) in cloned[target]:
                    values[field] = cloned[target][values[field]]
            copied = create(kind, values)
            cloned[kind][doc['name']] = copied['name']
            copies[kind].append(copied)
    for kind, field in [('evidence', 'related_evidence'), ('component', 'reverses_component')]:
        for copied in copies[kind]:
            if copied.get(field) in cloned[kind]:
                update(kind, copied['name'], {field: cloned[kind][copied[field]]})
    return cloned


def mutable_version(ledger, batch):
    current = ledger.get('version', batch['current_version'])
    if current['status'] not in {'Confirmed', 'Archived'} and batch.get('confirm_status') != 'Confirmed' and batch.get('writeback_status') != 'Success':
        return current
    values = clean_copy(current)
    values.update(version_code='ADJ-' + datetime.now().strftime('%Y%m%d%H%M%S%f'), version_type='Adjustment',
                  status='Active', is_current=1, source_type='Clone', calculated_at=None,
                  summary_snapshot_json='{}', rule_snapshot_json='[]', remark='采购支出更新；原确认版本保留')
    draft = ledger.create('version', values)
    clone_version_children(
        {kind: ledger.rows(kind, batch=batch['name'], version=current['name'])
         for kind in ('item', 'rule', 'evidence', 'component')}, draft['name'], ledger.create, ledger.put)
    ledger.put('version', current['name'], {'is_current': 0})
    ledger.put('batch', batch['name'], {'current_version': draft['name'], 'confirm_status': 'Pending',
                                      'status': 'Dirty', 'is_locked': 0, 'writeback_status': 'Not Started',
                                      'version_count': len(ledger.rows('version', batch=batch['name']))})
    return draft


def application_context(store, ledger, binding):
    mappings = store.find('batch_map', source_id=binding['logistics_id'], limit=1)
    if not mappings:
        return None
    batch = ledger.get('batch', mappings[0]['batch'], lock=True)
    if not batch:
        raise ValueError(_('来源对应的成本批次不存在'))
    if row_meta(ledger.get('version',batch['current_version']) or {}).get('freight_settlement'):
        raise ValueError(_('本批次已采用按票费用／装箱策略，旧整单写入已停用'))
    return batch


def pending(store, binding, status, issues):
    binding.update(application_status=status, issues=issues)
    save_binding(store, binding)
    return binding


def prepare_source_switch(store, ledger, binding, expense, batch, actor):
    """Install the source gate before any pending return; raw evidence stays intact."""
    from overseas_costing.services.effective_logistics_source import context_for_source
    version = ledger.get('version', batch['current_version'], lock=True)
    context = context_for_source(expense, binding, version['name'], batch['name'])
    current = row_meta(version).get('effective_logistics_source') or {}
    if current.get('fingerprint') == context['fingerprint']:
        return version, context
    version = mutable_version(ledger, batch)
    context = context_for_source(expense, binding, version['name'], batch['name'])
    meta = row_meta(version)
    if current:
        meta.setdefault('effective_source_history', []).append(current)
    meta['effective_logistics_source'] = context
    ledger.put('version', version['name'], {'extra_json':dumps(meta), 'calculated_at':None,
                                           'summary_snapshot_json':'{}', 'rule_snapshot_json':'[]'})
    for item in ledger.rows('item', batch=batch['name'], version=version['name']):
        item_meta = row_meta(item)
        if item_meta.get('effective_logistics_source'):
            item_meta.setdefault('settlement_source_history', []).append({k:v for k,v in item_meta.items()
                if k in {'effective_logistics_source','settlement_cargo','settlement_physical','settlement_valuation',
                         'settlement_packing_provenance','settlement_packing_candidates','packing_quantity'}})
        item_meta.update(effective_logistics_source=context, settlement_cargo={}, settlement_physical={},
                         settlement_packing_review=True)
        item_meta.pop('settlement_valuation',None)
        for key in ('settlement_packing_provenance','settlement_packing_candidates','packing_quantity'):
            item_meta.pop(key,None)
        ledger.put('item',item['name'],{'extra_json':persist_item_meta(item_meta), **{key:0 for key in DERIVED_FIELDS}})
    for rule in ledger.rows('rule',batch=batch['name'],version=version['name']):
        ledger.put('rule',rule['name'],{'is_enabled':0,'is_active':0,'is_final':0})
    for component in ledger.rows('component',batch=batch['name'],version=version['name']):
        ledger.put('component',component['name'],{'is_active':0})
    ledger.put('batch',batch['name'],{'status':'Dirty','confirm_status':'Pending','is_locked':0})
    binding.update(version=version['name'], application_status='pending')
    save_binding(store,binding)
    store.audit(binding['id'],'source_switch',actor,source_context=context,version=version['name'])
    return ledger.get('version',version['name']), context


def apply_binding(store, ledger, binding_id, actor, *, trusted_review_actor=None):
    with store.atomic():
        binding = store.get('binding', binding_id, lock=True)
        expense = store.get('source', binding['expense_id'], lock=True)
        logistics = store.get('source', binding['logistics_id'], lock=True)
        from .reviewed_cargo import resolve_reviewed_source
        expense = resolve_reviewed_source(store, expense, binding)
        batch = application_context(store, ledger, binding)
        if not batch:
            return pending(store, binding, 'pending', ['国际物流尚未对应唯一成本批次'])
        if locked(batch) and (not trusted_review_actor or batch.get('edit_lock_owner') != trusted_review_actor):
            return pending(store, binding, 'queued', ['批次正在编辑，已排队等待应用'])
        before = {'items':ledger.rows('item',batch=batch['name'],version=batch['current_version']),
                  'rules':ledger.rows('rule',batch=batch['name'],version=batch['current_version']), 'batch':dict(batch)}
        version, context = prepare_source_switch(store,ledger,binding,expense,batch,actor)
        batch = ledger.get('batch',batch['name'],lock=True)
        from .bound_wiki_service import pending_wiki_issues
        wiki_issues = pending_wiki_issues(store, binding)
        if wiki_issues:
            return pending(store, binding, 'pending', wiki_issues)
        if not expense or expense.get('invalid') or expense.get('kind') != 'expense':
            return pending(store,binding,'invalid',['采购支出缺失或已失效，当前资料待处理'])
        if not expense.get('approved'):
            return pending(store,binding,'pending',['采购支出尚未审批通过'])
        from .document_writer import sync_source_documents
        if (expense.get('coverage') == 'unknown' or binding.get('coverage')) and binding.get('coverage_cost_hash') != expense['cost_hash']:
            return pending(store, binding, 'pending', ['采购支出已变化，请重新核对费用覆盖范围'])
        negative_confirmed = bool(binding.get('negative_confirmed') and binding.get('negative_cost_hash') == expense['cost_hash'])
        effective_hash = digest(expense['cost_hash'], binding['revision'], binding.get('coverage'), binding.get('negative_confirmed'), context['fingerprint'])
        if binding.get('applied_hash') == effective_hash and binding.get('version') == batch['current_version']:
            return refresh_application_state(store, ledger, binding, expense)
        items = ledger.rows('item', batch=batch['name'], version=batch['current_version'])
        rules = ledger.rows('rule', batch=batch['name'], version=batch['current_version'])
        plan = plan_application(expense, items, rules, binding_id=binding_id, coverage=binding.get('coverage'), negative_confirmed=negative_confirmed)
        if not plan['ready']:
            return pending(store, binding, 'pending', plan['blocking'])
        items = ledger.rows('item', batch=batch['name'], version=version['name'])
        rules = ledger.rows('rule', batch=batch['name'], version=version['name'])
        plan = plan_application(expense, items, rules, binding_id=binding_id, coverage=binding.get('coverage'), negative_confirmed=negative_confirmed)
        if not binding.get('baseline'):
            binding['baseline'] = before
        added_items = []
        for update in plan['goods_updates']:
            item = next(i for i in items if i['name'] == update['name'])
            meta = row_meta(item)
            if meta.get('settlement_binding_id') != binding_id:
                meta['settlement_original_values'] = {field: item.get(field) for field in GOODS_FIELDS}
            meta.update(settlement_binding_id=binding_id, settlement_line_key=update['line_key'], settlement_source_snapshot=expense['snapshot'])
            previous_cargo = meta.get('settlement_cargo') or {'quantity':item.get('actual_shipped_qty') or item.get('quantity'), 'unit':item.get('shipped_uom') or item.get('unit')}
            cargo = {**update['values'], 'binding_id':binding_id, 'source_snapshot':expense['snapshot'], 'line_key':update['line_key']}
            changed_identity = any(str(item.get(k) or '') != str(update['values'].get(k) or '') for k in ('material_code', 'spec_model')) or str(previous_cargo.get('unit') or '') != str(cargo.get('unit') or '')
            quantity_changed = Decimal(str(previous_cargo.get('quantity') or 0)) != Decimal(str(cargo['quantity']))
            if changed_identity or quantity_changed:
                meta['settlement_packing_review'] = True
                meta.setdefault('settlement_packing_quantity_at_change', item.get('actual_shipped_qty'))
            # Purchase quantity, pricing units/value and raw packing remain independent.
            values = {k:v for k,v in update['values'].items() if k in {'material_code','product_name','spec_model'}}
            meta['effective_logistics_source'] = context
            meta['settlement_cargo'] = cargo
            meta['settlement_valuation'] = value_final_cargo(item, cargo, {k:v for k,v in version.items() if k.startswith('fx_')})
            if meta['settlement_valuation']['error']:
                meta['settlement_purchase_value_review'] = True
            else:
                meta.pop('settlement_purchase_value_review', None)
            meta['settlement_applied_values'] = dict(values)
            values.update({key: 0 for key in DERIVED_FIELDS})
            values['extra_json'] = persist_item_meta(meta)
            ledger.put('item', item['name'], values)
        for index, addition in enumerate(plan['goods_additions']):
            cargo = {**addition['values'], 'binding_id':binding_id, 'source_snapshot':expense['snapshot'], 'line_key':addition['line_key']}
            values = {k:v for k,v in addition['values'].items() if k in {'material_code','product_name','spec_model','unit'}}
            meta = {'settlement_binding_id': binding_id, 'settlement_line_key': addition['line_key'],
                    'settlement_source_snapshot': expense['snapshot'], 'settlement_created': True, 'settlement_packing_review': True,
                    'settlement_cargo':cargo, 'settlement_purchase_value_review':True, 'settlement_applied_values':dict(values)}
            meta['effective_logistics_source'] = context
            meta['settlement_valuation'] = value_final_cargo(values, cargo, {k:v for k,v in version.items() if k.startswith('fx_')})
            values.update(batch=batch['name'], version=version['name'], row_no=len(items)+index+1,
                          stable_line_key='settlement:'+digest(binding_id,addition['line_key'])[:32],
                          source_type='oa_logistics', extra_json=persist_item_meta(meta))
            added_items.append(ledger.create('item', values)['name'])
        # Only a positively identified complete table may retire rows. Their full evidence is retained below.
        retired = binding.setdefault('retired_items', {})
        for name in plan['remove_items']:
            item = next(i for i in items if i['name'] == name)
            meta = row_meta(item)
            retired[meta.get('settlement_origin_item') or name] = item
            ledger.delete('item', name)
        for rule in rules:
            if rule['name'] in plan['disable_rules'] or rule.get('source_binding_id') == binding_id:
                ledger.put('rule', rule['name'], {'is_enabled': 0, 'is_active': 0, **({'is_final': 0} if rule.get('source_binding_id') == binding_id else {})})
        # Selected expense owns all current logistics fees and allocation facts.
        inherited = {}
        basis = 'goods_value'
        for component in ledger.rows('component',batch=batch['name'],version=version['name']):
            ledger.put('component',component['name'],{'is_active':0})
        for index, rule in enumerate(plan['rules']):
            # Include snapshot in the physical rule identity; prior rows remain auditable and disabled.
            ledger.create('rule', {**rule, 'batch': batch['name'], 'version': version['name'],
                                   'allocation_basis': basis, 'basis_field': inherited.get('basis_field') or basis, 'scope_type':'ALL_ITEMS',
                                   'scope_value_json':inherited.get('scope_value_json') or '[]', 'priority_no': index,
                                   'remark': '最终物流采购支出；费用范围：' + ','.join(plan['coverage'])})
        sync_source_documents(store, ledger, expense, batch['name'], actor, source_context=context, trusted_review_actor=trusted_review_actor)
        remaining = ledger.rows('item', batch=batch['name'], version=version['name'])
        issues = application_issues(ledger, version, expense, remaining, plan)
        application_id = digest(binding_id, expense['snapshot'], version['name'], effective_hash)
        store.insert('application', {'id': application_id, 'binding_id': binding_id,
                     'snapshot': digest(expense['snapshot'], effective_hash), 'version': version['name'], 'status': 'applied',
                     'data': dumps({'source_snapshot': expense['snapshot'], 'expense_id': expense['id'], 'cost_hash': expense['cost_hash'],
                                   'effective_hash': effective_hash, 'actor': actor, 'before': before, 'plan': plan, 'source_context':context,
                                   'review_id': expense.get('review_id'), 'adopted_goods': expense.get('goods') or [],
                                   'fx_evidence': {k: v for k, v in version.items() if k.startswith('fx_')},
                                   'added_items': added_items, 'applied_at': utcnow()})})
        binding.update(application_status='applied_pending' if issues else 'applied', issues=issues,
                       applied_hash=effective_hash, applied_cost_hash=expense['cost_hash'], source_snapshot=expense['snapshot'],
                       version=version['name'], last_application=application_id)
        save_binding(store, binding)
        ledger.put('version', version['name'], {'calculated_at': None, 'summary_snapshot_json': '{}'})
        ledger.put('batch', batch['name'], {'status': 'Dirty', 'confirm_status': 'Pending', 'is_locked': 0, 'item_count': len(remaining)})
        store.audit(binding_id, 'apply', actor, application_id=application_id, snapshot=expense['snapshot'], version=version['name'])
        return binding


def reverse_binding(store, ledger, old, new, actor):
    """Called inside the replacement transaction. Restore only fields still owned by old source."""
    batch = application_context(store, ledger, old)
    if not batch:
        return
    if locked(batch):
        raise ValueError(_('批次正在编辑，请结束编辑后更正关联'))
    version = mutable_version(ledger, batch)
    items = ledger.rows('item', batch=batch['name'], version=version['name'])
    baseline = old.get('baseline') or {}
    for item in items:
        meta = row_meta(item)
        if meta.get('settlement_binding_id') != old['id']:
            continue
        if meta.get('settlement_created'):
            # Retain manually supplemented rows pending explicit mapping rather than discard them.
            if has_material_supplements(item):
                meta = {k: v for k, v in meta.items() if not k.startswith('settlement_') or k.startswith('settlement_packing_') or k == 'settlement_origin_item'}
                meta['settlement_packing_review'] = True
                ledger.put('item', item['name'], {'extra_json': persist_item_meta(meta), 'manual_override_flag': 1, **{key: 0 for key in DERIVED_FIELDS}})
            else:
                ledger.delete('item', item['name'])
            continue
        original = meta.get('settlement_original_values') or {}
        applied = meta.get('settlement_applied_values') or {}
        values = {k: v for k, v in original.items() if k in GOODS_FIELDS and str(item.get(k) or '') == str(applied.get(k) or '')}
        for key in list(meta):
            if key.startswith('settlement_') and key != 'settlement_origin_item' and not key.startswith('settlement_packing_'):
                meta.pop(key)
        values['extra_json'] = persist_item_meta(meta)
        values.update({key: 0 for key in DERIVED_FIELDS})
        if 'quantity' in values and meta.get('goods_value_source') == 'derived_quantity_unit_price':
            values['goods_value'] = str(Decimal(str(values['quantity'] or 0)) * Decimal(str(item.get('unit_price') or 0)))
        ledger.put('item', item['name'], values)
    current_origins = {row_meta(i).get('settlement_origin_item') or i['name'] for i in ledger.rows('item', batch=batch['name'], version=version['name'])}
    for origin, retired in (old.get('retired_items') or {}).items():
        if origin in current_origins:
            continue
        meta = row_meta(retired)
        if meta.get('settlement_created'):
            continue
        values = clean_copy(retired)
        values.update({k: v for k, v in (meta.get('settlement_original_values') or {}).items() if k in GOODS_FIELDS})
        meta = {k: v for k, v in meta.items() if not k.startswith('settlement_') or k.startswith('settlement_packing_')}
        meta['settlement_origin_item'] = origin
        values.update(version=version['name'], extra_json=persist_item_meta(meta))
        values.update({key: 0 for key in DERIVED_FIELDS})
        ledger.create('item', values)
    for rule in ledger.rows('rule', batch=batch['name'], version=version['name']):
        if rule.get('source_binding_id') == old['id']:
            ledger.put('rule', rule['name'], {'is_final': 0, 'is_enabled': 0, 'is_active': 0})
    # Preserve retired rows and rule beforeimages in audit; do not silently reactivate estimates.
    new.update(baseline=None, retired_items={}, version=version['name'], last_application=None, applied_hash=None, applied_cost_hash=None,
               issues=['关联已更正，等待新采购支出资料应用'], application_status='pending')
    ledger.put('batch', batch['name'], {'status': 'Dirty', 'confirm_status': 'Pending', 'is_locked': 0})
    store.audit(old['id'], 'reverse', actor, old=old, retired_evidence=baseline, version=version['name'])


PACKING_FIELDS = ('actual_shipped_qty', 'gross_weight_kg', 'volume_m3', 'volume_weight_kg', 'chargeable_weight_kg', 'weight_ratio')


def has_material_supplements(item):
    meta = row_meta(item)
    return bool(item.get('manual_override_flag') or any(item.get(k) for k in PACKING_FIELDS)
                or str(item.get('actual_shipped_qty_mode') or '').upper() in {'EXPLICIT_SOURCE', 'MANUAL_CONFIRMED'}
                or meta.get('settlement_packing_provenance'))


def application_issues(ledger, version, expense, items, plan):
    issues = list(plan.get('goods_pending') or [])
    from overseas_costing.services.effective_source_values import project_source_values, item_source_context
    if any(item_source_context(i).get('root_kind') == 'expense'
           and not any(project_source_values(i).get(key) is not None for key in ('gross_weight_kg','volume_m3','chargeable_weight_kg'))
           for i in items):
        issues.append('采购支出装箱资料待补，旧物流重量和体积不参与当前核算')
    if any(row_meta(i).get('settlement_packing_review') for i in items):
        issues.append('采购数量或物料变化，需核对原重量、体积及装箱数量')
    if any(row_meta(i).get('settlement_purchase_value_review') or (row_meta(i).get('settlement_valuation') or {}).get('error') for i in items):
        issues.append('数量变化，需核对独立来源商品货值')
    currencies = {r['currency'] for r in plan.get('rules', [])}
    for field, label, required in [('fx_usd_to_rmb', 'USD 兑 RMB', 'USD' in currencies), ('fx_rmb_to_mxn', 'RMB 兑 MXN', True)]:
        if required:
            try:
                value = Decimal(str(version.get(field) or 0))
                valid = value.is_finite() and value > 0
            except Exception:
                valid = False
            if not valid:
                issues.append('缺少有效 ' + label + ' 汇率')
    return sorted(set(issues))


def refresh_application_state(store, ledger, binding, expense):
    from .reviewed_cargo import resolve_reviewed_source
    expense = resolve_reviewed_source(store, expense, binding)
    application = store.get('application', binding.get('last_application', '')) or {}
    version = ledger.get('version', binding['version'])
    batch = ledger.get('batch', version['batch']) or {}
    can_refresh = (version.get('status') not in {'Confirmed', 'Archived'}
                   and batch.get('confirm_status') != 'Confirmed'
                   and batch.get('writeback_status') != 'Success')
    items = ledger.rows('item', batch=version['batch'], version=version['name'])
    for item in items:
        meta = row_meta(item)
        if can_refresh and meta.get('settlement_binding_id') == binding['id'] and meta.get('settlement_cargo'):
            valuation = value_final_cargo(item,meta['settlement_cargo'],{k:v for k,v in version.items() if k.startswith('fx_')})
            if valuation != meta.get('settlement_valuation'):
                meta['settlement_valuation'] = valuation
                if valuation['error']:
                    meta['settlement_purchase_value_review'] = True
                else:
                    meta.pop('settlement_purchase_value_review',None)
                ledger.put('item',item['name'],{'extra_json':persist_item_meta(meta)})
    items = ledger.rows('item', batch=version['batch'], version=version['name'])
    issues = application_issues(ledger, version, expense, items, application.get('plan') or {})
    return pending(store, binding, 'applied_pending' if issues else 'applied', issues)


def item_review(item):
    meta = row_meta(item)
    cargo = meta.get('settlement_cargo') or {}
    from overseas_costing.services.effective_source_values import project_source_values
    effective = project_source_values(item)
    return {'item_name': item['name'], 'revision': digest(item),
            **{k: effective.get(k) for k in GOODS_FIELDS + PACKING_FIELDS + ('unit_price', 'goods_value')},
            'historical_packing':{k:item.get(k) for k in PACKING_FIELDS},
            'source_context':meta.get('effective_logistics_source') or {},
            'quantity':cargo.get('quantity',item.get('quantity')), 'unit':cargo.get('unit',item.get('unit')),
            'purchase_quantity':item.get('quantity'),
            'packing_quantity': effective.get('actual_shipped_qty'),
            'packing_pending': bool(meta.get('settlement_packing_review')),
            'goods_value_pending': bool(meta.get('settlement_purchase_value_review')),
            'packing_candidates': list((meta.get('settlement_packing_candidates') or {}).values())}


def resolve_item_checks(store, ledger, binding_id, expected_revision, selections, reason, actor):
    if not reason.strip() or not selections or len(selections) > 200:
        raise ValueError(_('请选择待核对行并填写核对说明'))
    with store.atomic():
        binding = store.get('binding', binding_id, lock=True)
        if not binding or binding['revision'] != int(expected_revision):
            raise ValueError(_('关联已变化，请刷新'))
        batch = application_context(store, ledger, binding)
        if not batch or locked(batch):
            raise ValueError(_('批次正在编辑或尚未对应，请结束编辑后核对'))
        version = ledger.get('version', batch['current_version'], lock=True)
        if version['status'] == 'Confirmed' or batch.get('writeback_status') == 'Success' or binding.get('version') != version['name']:
            raise ValueError(_('只能核对当前调整草稿，已确认版本保留历史'))
        for selection in selections:
            item = ledger.get('item', selection['item_name'], lock=True)
            if not item or item.get('batch') != batch['name'] or item.get('version') != version['name']:
                raise ValueError(_('物料不属于当前版本'))
            if item_review(item)['revision'] != selection.get('expected_item_hash'):
                raise ValueError(_('物料或装箱资料已变化，请刷新后核对'))
            meta = row_meta(item)
            if selection.get('packing_confirmed') is True and (meta.get('effective_logistics_source') or {}).get('root_kind') == 'expense':
                from overseas_costing.services.effective_source_values import project_source_values
                current_values = project_source_values(item)
                if not any(current_values.get(key) is not None for key in ('gross_weight_kg','volume_m3','chargeable_weight_kg')):
                    raise ValueError(_('采购支出尚无可采用的装箱资料，不能将旧重量确认为当前来源'))
            for flag, check in [('settlement_packing_review', 'packing_confirmed'), ('settlement_purchase_value_review', 'goods_value_confirmed')]:
                if selection.get(check) is True:
                    meta.pop(flag, None)
            ledger.put('item', item['name'], {'extra_json': persist_item_meta(meta)})
            store.audit(binding_id, 'verify_item', actor, item_name=item['name'], before=item_review(item), choices=selection, reason=reason)
        resolve_document_checks(store, batch['name'], version['name'], selections, actor, reason, ledger=ledger)
        ledger.put('version', version['name'], {'calculated_at': None, 'summary_snapshot_json': '{}'})
        ledger.put('batch', batch['name'], {'status': 'Dirty'})
        return refresh_application_state(store, ledger, binding, store.get('source', binding['expense_id']))


def validate_application_preview(store, ledger, binding, expected_snapshot, expected_version):
    expense = store.get('source', binding['expense_id'], lock=True)
    batch = application_context(store, ledger, binding)
    if not expected_snapshot or expense['snapshot'] != expected_snapshot:
        raise ValueError(_('采购支出已变化，请刷新明细和费用后重新确认'))
    if not expected_version or not batch or batch.get('current_version') != expected_version:
        raise ValueError(_('当前成本版本已变化，请刷新后重新确认'))


def resolve_document_checks(store, batch_name, version_name, selections, actor, reason, *, ledger):
    from .document_writer import source_packing_state_hash
    acknowledged = {s['item_name'] for s in selections if s.get('packing_confirmed') is True}
    from overseas_costing.services.effective_logistics_source import resolve_source_context
    context = resolve_source_context(batch_name,version_name,store=store,ledger=ledger)
    for candidate in store.find('document_sync', batch=batch_name, source_id=context['root_source_id'], limit=1):
        state = store.get('document_sync', candidate['id'], lock=True)
        if state.get('version') != version_name or state['status'] == 'queued':
            continue
        remaining = {key: [row for row in rows if row.get('item_name') not in acknowledged] for key,rows in (state.get('reviews') or {}).items()}
        remaining = {key: rows for key,rows in remaining.items() if rows}
        if remaining == state.get('reviews'):
            continue
        state.update(reviews=remaining, blocking=bool(remaining), status='review' if remaining else 'applied',
                     items_hash=source_packing_state_hash(ledger.rows('item', batch=batch_name, version=version_name), context),
                     issues=sorted({row.get('reason') or '装箱行待核对' for rows in remaining.values() for row in rows} | set(state.get('document_issues') or [])))
        store.put('document_sync', {key:state[key] for key in ('id','source_id','batch','status')} | {'data':dumps(state)})
        store.audit(state['source_id'],'verify_document_rows',actor,items=sorted(acknowledged),reason=reason,remaining=remaining)
