"""Reconcile missing fields on existing OA intake drafts using the archive sync."""
from collections import Counter

from overseas_costing.services.edit_session_service import lock_is_active
from overseas_costing.services.effective_logistics_source import load_source_bundle
from overseas_costing.services.purchase_value_evidence import number
from overseas_costing.services.supplier_resolution_service import load_active_suppliers
from overseas_costing.services.shipment_cost_service import object_json

from .initial_intake import plan_existing_initial_items
from .model import components, digest, dumps


def _verified_legacy_positions(batch, source, items):
    """Recover old repeated line keys only against the exact original table and row sequence."""
    from overseas_costing.scripts.import_oa_logistics import extract_form_fields

    metadata = object_json(batch.get('extra_json'))
    saved_fields = metadata.get('form_fields') or {}
    if metadata.get('source') != 'dingtalk_oa_logistics' or not isinstance(saved_fields, dict):
        return {}
    source_goods = source.get('goods') or []
    key_counts = Counter(row.get('line_key') for row in source_goods)
    if not any(count > 1 for key, count in key_counts.items() if key):
        return {}

    original_rows = [(index, row) for index, row in enumerate(source_goods, 1) if row.get('quantity') is not None]
    if len(items) != len(original_rows):
        return {}
    tables = {row.get('source_table') for _, row in original_rows}
    raw = source.get('raw') or {}
    controls = Counter(control.get('name') or control.get('label')
                       for control in components(raw) if isinstance(control, dict))
    fields = extract_form_fields(raw)
    if any(not table or controls[table] != 1 or table not in saved_fields
           or fields.get(table) != saved_fields[table] for table in tables):
        return {}

    by_position = {row.get('row_no'): row for row in items}
    if len(by_position) != len(items):
        return {}
    positions = {}
    for index, origin in original_rows:
        item = by_position.get(index) or {}
        line_key = object_json(item.get('extra_json')).get('initial_logistics_line')
        if (not line_key or line_key != origin.get('line_key')
                or number(item.get('quantity')) != number(origin.get('quantity'))
                or any(str(item.get(field) or '') != str(origin.get(field) or '')
                       for field in ('material_code', 'product_name', 'spec_model', 'unit'))):
            return {}
        if key_counts[line_key] > 1:
            positions[item['name']] = (origin['source_table'], origin['source_position'])
    return positions


def sync_initial_draft_items(db, ledger, batch_name, source_id, *,
                             dry_run=False, expected_plan_hash=None):
    """The caller owns the archive transaction and its rollback."""
    bundle = load_source_bundle(batch_name, store=db, ledger=ledger, lock=not dry_run)
    batch = bundle['batch'] or {}
    version = bundle['version'] or {}
    source = bundle['source'] or {}
    context = bundle['context']
    if (batch.get('source_type') != 'oa_logistics'
            or not source.get('snapshot')
            or not source.get('corp') or not source.get('instance')
            or batch.get('source_corp_id') != source['corp']
            or batch.get('source_instance_id') != source['instance']
            or source.get('id') != source_id or context.get('root_source_id') != source_id
            or context.get('root_kind') != 'logistics' or not context.get('available')
            or context.get('invalid') or (context.get('packing') or {}).get('selected_source')):
        return {'status': 'protected_source'}
    if (batch.get('status') not in {'Draft', 'Dirty'} or batch.get('docstatus')
            or batch.get('is_locked') or batch.get('confirm_status') != 'Pending'
            or batch.get('writeback_status') != 'Not Started'
            or version.get('status') != 'Active' or not version.get('is_current')
            or version.get('version_code') != '初始录入' or version.get('calculated_at')):
        return {'status': 'protected_draft'}
    if lock_is_active(batch):
        return {'status': 'editing'}

    items = sorted(ledger.rows('item', batch=batch_name, version=version['name']), key=lambda row: row['name'])
    if not dry_run:
        items = [ledger.get('item', row['name'], lock=True) for row in items]
    items = [row for row in items if row and row['version'] == version['name'] and not row.get('is_excluded')]
    rates = {'RMB': 1}
    usd = number(version.get('fx_usd_to_rmb'))
    mxn = number(version.get('fx_rmb_to_mxn'))
    if usd and usd > 0:
        rates['USD'] = usd
    if mxn and mxn > 0:
        rates['MXN'] = 1 / mxn
    plan = plan_existing_initial_items(
        source, items, fx_rates=rates, supplier_rows=load_active_suppliers,
        verified_legacy_positions=_verified_legacy_positions(batch, source, items),
    )
    updates = plan['patches']
    fingerprint = digest('oa-initial-draft-refresh-1', batch_name, batch.get('modified'),
                         version['name'], version.get('modified'), source['snapshot'], updates)
    if expected_plan_hash is not None and expected_plan_hash != fingerprint:
        raise ValueError('原单或批次已变化，请刷新补齐预览。')
    result = {'status': 'ready' if updates else 'unchanged', 'update_count': len(updates),
              'issues': plan.get('issues') or [], 'item_results': plan.get('item_results') or [],
              'plan_hash': fingerprint, 'item_count': len(items)}
    if dry_run or not updates:
        return result

    for update in updates:
        values = dict(update['values'])
        if isinstance(values.get('extra_json'), dict):
            values['extra_json'] = dumps(values['extra_json'])
        ledger.put('item', update['name'], values)
    ledger.put('batch', batch_name, {'item_count': len(items)})
    db.audit(source_id, 'initial_logistics_draft_synced', 'archive-sync', batch=batch_name,
             version=version['name'], snapshot=source['snapshot'], plan_hash=fingerprint,
             item_names=[update['name'] for update in updates])
    return {**result, 'status': 'updated'}
