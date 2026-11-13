"""Repair only the user-confirmed draft; absent or protected sites are no-ops."""


def execute():
    import frappe
    from overseas_costing.services.logistics_cargo_recovery import repair_batch
    from overseas_costing.services.oa_fee_backfill_service import backfill_missing_oa_fees

    if not frappe.db.table_exists('Overseas Cost Batch'):
        return
    names = frappe.get_all('Overseas Cost Batch', filters={
        'batch_no': '202611031848000325271', 'source_type': 'oa_logistics',
    }, pluck='name')
    for name in names:
        result = repair_batch(name, dry_run=False)
        if result['status'] in {'updated', 'unchanged'}:
            version = frappe.db.get_value('Overseas Cost Batch', name, 'current_version')
            backfill_missing_oa_fees(name, version)
        if result['status'] not in {'updated', 'unchanged', 'absent'}:
            frappe.logger('overseas_costing').warning('Cargo repair retained draft %s: %s', name, result)
