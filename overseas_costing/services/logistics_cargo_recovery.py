"""Scoped, transactional repair of an unconfirmed OA cargo draft."""
from decimal import Decimal

from .logistics_autofill_service import apply_reconciliation, build_logistics_reconciliation, extra
from .logistics_settlement.model import dumps

POLICY = 'oa-cargo-groups-v1'


def repair_batch(batch_name, *, dry_run=True):
    import frappe
    from .edit_session_service import lock_is_active
    from .logistics_settlement.ledger import FrappeLedger
    from .logistics_settlement.runtime import has_final_binding
    from .material_scope_reset_service import _list_original_logistics_sources, _authoritative_logistics_source

    ledger = FrappeLedger()
    batch = ledger.get('batch', batch_name, lock=not dry_run)
    if not batch:
        return {'status': 'absent'}
    version = ledger.get('version', batch['current_version'], lock=not dry_run)
    meta = extra(version)
    if (meta.get('cargo_group_repair') or {}).get('policy') == POLICY:
        return {'status': 'unchanged', 'result': meta['cargo_group_repair']}
    if (batch.get('source_type') != 'oa_logistics' or batch.get('status') not in {'Draft', 'Dirty'}
            or batch.get('docstatus') or batch.get('is_locked') or lock_is_active(batch)
            or batch.get('confirm_status') != 'Pending' or batch.get('writeback_status') != 'Not Started'
            or version.get('status') != 'Active' or not version.get('is_current')
            or has_final_binding(batch_name)):
        return {'status': 'protected'}
    sources = [source for source in _list_original_logistics_sources(batch_name, version['name'])
               if source.get('approval_no') == batch.get('batch_no')
               and source.get('approval_role') == 'international_logistics']
    source, source_status = _authoritative_logistics_source(sources)
    if source_status != 'AUTHORITATIVE':
        return {'status': 'source_unverified'}
    items = ledger.rows('item', batch=batch_name, version=version['name'])
    items = sorted(items, key=lambda row: (int(row.get('row_no') or 0), row['name']))
    if not dry_run:
        items = [ledger.get('item', row['name'], lock=True) for row in items]
    proposal = build_logistics_reconciliation(items, source)
    if not proposal or proposal.get('blocked'):
        return {'status': 'manual_review', 'reason': (proposal or {}).get('reason')}
    rows = proposal['payload']['rows']
    result = {
        'policy': POLICY, 'source_fingerprint': proposal['payload']['source_fingerprint'],
        'row_count': len(rows), 'original_item_names': [row['name'] for row in items],
        'quantity': str(sum(Decimal(str(row.get('actual_shipped_qty') or 0)) for row in rows)),
        'gross_weight_kg': str(sum(Decimal(str(row.get('gross_weight_kg') or 0)) for row in rows)),
        'source_row_count': sum(len(extra(row).get('logistics_cargo_group', {}).get('source_rows', [])) for row in rows),
    }
    if dry_run:
        return {'status': 'ready', 'result': result}
    apply_reconciliation(frappe, proposal, batch=batch_name, version=version['name'],
                         current=items, run_id=POLICY)
    meta['cargo_group_repair'] = result
    ledger.put('version', version['name'], {'extra_json': dumps(meta), 'calculated_at': None,
               'summary_snapshot_json': '{}', 'rule_snapshot_json': '[]'})
    ledger.put('batch', batch_name, {'status': 'Dirty'})
    return {'status': 'updated', 'result': result}
