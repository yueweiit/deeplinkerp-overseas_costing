"""Regression for the 11 archived rows of approval 202611031848000325271."""
from copy import deepcopy
from decimal import Decimal
import json
import sys
from types import SimpleNamespace

import pytest

from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation, extra
from overseas_costing.services.logistics_cargo_groups import group_goods, metadata, META_KEY

LINES = [
    ('CW000014', 'Yellow Duck', 80, '2.6', '3.3'),
    ('CW000228', 'Chick', 99, '1.98', '2.3'),
    ('CW000229', 'Bee', 99, '1.98', '2.3'),
    ('CW000007', 'Cat Chew Stick', 500, '9', '1.286'),
    ('CW000227', 'Green', 56, '4.48', '9.5'),
    ('CW000231', 'Pink', 70, '5.6', '9.5'),
    ('CW000232', 'Orange', 16, '1.28', '9.5'),
    ('CW000230', 'Pig', 99, '1.98', '2.3'),
    ('CW000007', 'Cat Chew Stick', 1300, '4.11', '1.286'),
    ('CW000007', 'Cat Chew Stick', 700, '8.925', '1.286'),
    ('CW000014', 'Yellow Duck', 70, '2.275', '3.3'),
]


@pytest.fixture
def cargo():
    source = {'source_id': 'OA:FORM', 'source_kind': 'approval_form', 'approval_role': 'international_logistics',
              'approval_no': '202611031848000325271', 'form_fields': {'货物信息Bienes': []}}
    items = []
    for index, (code, spec, quantity, weight, amount) in enumerate(LINES, 1):
        source['form_fields']['货物信息Bienes'].append({
            '物料编码': code, '物料名称': spec, '规格型号': spec, '数量': quantity,
            '重量 Peso': weight, '单位': '个', '货值': amount,
        })
        items.append({'name': f'I{index}', 'row_no': index, 'material_code': code,
            'product_name': spec, 'spec_model': spec, 'quantity': quantity,
            'actual_shipped_qty': quantity, 'unit': '个', 'gross_weight_kg': weight,
            'goods_value': amount, 'source_type': 'oa_logistics',
            'stable_line_key': f'original-{index}', 'extra_json': json.dumps({
                'initial_logistics_source': {'source_ref': {
                    'approval_no': source['approval_no'], 'field': '货物信息Bienes', 'row': index,
                }}, 'initial_logistics_line': f'line-{index}',
            })})
    return source, items


def test_group_quantity_weight_and_original_rows(cargo):
    source, items = cargo
    before = deepcopy((source, items))
    proposal = build_logistics_reconciliation(items, source)
    rows = proposal['payload']['rows']
    assert len(rows) == 8
    assert sum(Decimal(row['actual_shipped_qty']) for row in rows) == 3089
    assert sum(Decimal(row['gross_weight_kg']) for row in rows) == Decimal('44.21')
    duck = next(row for row in rows if row['material_code'] == 'CW000014')
    cat = next(row for row in rows if row['material_code'] == 'CW000007')
    assert duck['actual_shipped_qty'] == '150' and duck['gross_weight_kg'] == '4.875'
    assert cat['actual_shipped_qty'] == '2500' and cat['gross_weight_kg'] == '22.035'
    assert [line['row'] for line in extra(cat)[META_KEY]['source_rows']] == [4, 9, 10]
    assert extra(cat)[META_KEY]['item_names'] == ['I10', 'I4', 'I9']
    assert proposal['payload']['excluded_item_names'] == ['I10', 'I11', 'I9']
    assert (source, items) == before


def test_repeat_uses_source_totals_not_prior_aggregate(cargo):
    source, items = cargo
    first = build_logistics_reconciliation(items, source)['payload']['rows']
    second = build_logistics_reconciliation(first, source)['payload']['rows']
    for before, after in zip(first, second):
        for field in ('quantity', 'goods_value', 'actual_shipped_qty', 'gross_weight_kg', 'stable_line_key'):
            assert before.get(field) == after.get(field)
        assert extra(before)[META_KEY] == extra(after)[META_KEY]


def test_different_spec_table_and_unit_never_merge(cargo):
    source, _ = cargo
    rows = source['form_fields']['货物信息Bienes']
    rows[-1]['规格型号'] = 'Another Duck'
    rows[9]['单位'] = '箱'
    source['form_fields']['货物信息 2'] = [deepcopy(rows[0])]
    groups = group_goods(source)
    assert len(groups) == 11


@pytest.mark.parametrize('field,value', [
    ('quantity', 999), ('gross_weight_kg', 99), ('goods_value', 88),
    ('unit_price', 99), ('manual_override_flag', 1),
    ('actual_shipped_qty_mode', 'MANUAL_CONFIRMED'),
    ('purchase_currency', 'USD'), ('recipient', 'Manual recipient'),
    ('supplier', 'Manual supplier'), ('unit', '箱'),
])
def test_manual_changes_are_not_overwritten(cargo, field, value):
    source, items = cargo
    items[8][field] = value
    before = deepcopy(items)
    proposal = build_logistics_reconciliation(items, source)
    assert proposal['blocked']
    assert items == before


def test_scope_keeps_duplicate_rows_until_group_quantity_is_confirmed(cargo):
    from overseas_costing.services.material_ai_row_selection import catalog, _finalize_cargo_groups
    source, items = cargo
    proposal = build_logistics_reconciliation(items, source)
    result = catalog(items, [proposal], [], {}, run_id='R', sources=[source])
    assert not result['material_scope_excluded_item_names']
    grouped = next(row for row in result['rows'] if row.get('_cargo_group')
                   and row['values']['material_code'] == 'CW000007')
    assert grouped['target_item_name'] == 'I4'
    assert [ref['row'] for ref in grouped['source_refs']] == [4, 9, 10]
    current = deepcopy(items)
    excluded = set()
    _finalize_cargo_groups(current, [grouped], [], excluded)
    assert len(current) == 11 and not excluded
    _finalize_cargo_groups(current, [grouped], [{
        'fieldname': 'actual_shipped_qty', 'item_name': 'I4', 'row_id': grouped['row_id'],
    }], excluded)
    assert excluded == {'I9', 'I10'}
    assert len(current) == 9
    assert metadata(next(row for row in current if row['name'] == 'I4'))[META_KEY]['source_rows']


def test_targeted_repair_and_repeated_patch_preserve_original_records(cargo, monkeypatch):
    from overseas_costing.tests.test_settlement_writer import Ledger
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services import logistics_cargo_recovery as recovery
    from overseas_costing.services import material_scope_reset_service as scope
    from overseas_costing.services import edit_session_service
    from overseas_costing.services.logistics_settlement import ledger as ledger_module, runtime
    import sqlite3

    source, items = cargo
    db = Store.sqlite(sqlite3.connect(':memory:'))
    db.install()
    ledger = Ledger(db)
    batch = ledger.create('batch', {'batch_no': source['approval_no'], 'source_type': 'oa_logistics',
        'status': 'Dirty', 'confirm_status': 'Pending', 'writeback_status': 'Not Started'})
    version = ledger.create('version', {'batch': batch['name'], 'status': 'Active', 'is_current': 1})
    ledger.put('batch', batch['name'], {'current_version': version['name']})
    for item in items:
        item.update(batch=batch['name'], version=version['name'])
        if item['material_code'] in {'CW000007', 'CW000014'}:
            item.update(is_excluded=1, actual_shipped_qty=0, excluded_by='system',
                        exclusion_reason='系统按国际物流物料范围自动排除')
        ledger.create('item', item)
    monkeypatch.setattr(ledger_module, 'FrappeLedger', lambda: ledger)
    monkeypatch.setattr(runtime, 'has_final_binding', lambda name: False)
    monkeypatch.setattr(edit_session_service, 'lock_is_active', lambda batch: False)
    monkeypatch.setattr(scope, '_list_original_logistics_sources', lambda *args: [
        source, {**source, 'source_kind': 'approval_attachment', 'source_id': 'OA:XLSX'},
    ])
    def set_value(doctype, name, values, field_value=None, **kwargs):
        if isinstance(values, str):
            values = {values: field_value}
        if doctype.endswith('Item'):
            assert all(values[field] is not None for field in
                       ('net_weight_kg', 'gross_weight_kg', 'volume_m3', 'volume_weight_kg', 'chargeable_weight_kg')
                       if field in values)
        return ledger.put('item' if doctype.endswith('Item') else 'batch', name, values)
    monkeypatch.setitem(sys.modules, 'frappe', SimpleNamespace(db=SimpleNamespace(set_value=set_value)))
    preview = recovery.repair_batch(batch['name'])
    assert preview['status'] == 'ready'
    assert preview['result']['source_row_count'] == 11
    applied = recovery.repair_batch(batch['name'], dry_run=False)
    assert applied['status'] == 'updated'
    saved = ledger.rows('item', batch=batch['name'])
    assert len(saved) == 11
    active = [row for row in saved if not row.get('is_excluded')]
    assert len(active) == 8
    assert sum(Decimal(str(row['actual_shipped_qty'])) for row in active) == 3089
    assert recovery.repair_batch(batch['name'], dry_run=False)['status'] == 'unchanged'
    assert saved == ledger.rows('item', batch=batch['name'])


@pytest.mark.parametrize('per_field', [False, True])
@pytest.mark.parametrize('with_facts', [False, True])
def test_review_writer_saves_group_quantity_trace_and_soft_exclusion(cargo, per_field, with_facts):
    import sqlite3
    from overseas_costing.tests.test_settlement_writer import Ledger
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.material_ai_row_selection import catalog, project
    from overseas_costing.services.material_ai_selection_writer import write_rows

    source, items = cargo
    db = Store.sqlite(sqlite3.connect(':memory:'))
    db.install()
    ledger = Ledger(db)
    batch = ledger.create('batch', {'status': 'Dirty', 'confirm_status': 'Pending'})
    version = ledger.create('version', {'status': 'Active', 'batch': batch['name']})
    ledger.put('batch', batch['name'], {'current_version': version['name']})
    for item in items:
        ledger.create('item', {**item, 'batch': batch['name'], 'version': version['name']})
    items = ledger.rows('item', batch=batch['name'], version=version['name'])
    if with_facts:
        from overseas_costing.services.purchase_value_evidence import bind_purchase_value
        for item in items:
            item['stable_line_key'] = None
            meta = metadata(item)
            ref = {**meta['initial_logistics_source']['source_ref'], 'source_id': source['source_id']}
            meta['adopted_purchase_value'] = bind_purchase_value(item, {
                'material_code': item['material_code'], 'spec_model': item['spec_model'],
                'quantity': str(item['quantity']), 'amount_rmb': item['goods_value'],
                'original_amount': item['goods_value'], 'currency': 'RMB', 'uom': '个', 'source_refs': [ref],
            })
            item.update(extra_json=json.dumps(meta))
            ledger.put('item', item['name'], item)
    proposal = build_logistics_reconciliation(items, source)
    choices = catalog(items, [proposal], [], {}, run_id='R', sources=[source])
    source_rows = [row for row in choices['rows'] if row['origin'] == 'source']
    fields = {}
    for candidate in choices['field_candidates']:
        if candidate['fieldname'] in {'actual_shipped_qty', 'shipped_uom', 'gross_weight_kg'} and candidate.get('can_apply'):
            fields[f"{candidate['item_name']}:{candidate['fieldname']}"] = candidate['candidate_id']
    preview = project(items, choices, [row['row_id'] for row in source_rows], [], 'update_selected',
                      field_choices=fields if per_field else None)
    preview.update(batch=batch['name'], version=version['name'], id='P', run_id='R', revision='REV', source_context={})
    write_rows(db, ledger, preview, {})
    saved = ledger.rows('item', batch=batch['name'])
    active = [row for row in saved if not row.get('is_excluded')]
    assert len(saved) == 11 and len(active) == 8
    assert sum(Decimal(str(row['actual_shipped_qty'])) for row in active) == 3089
    assert sum(Decimal(str(row['gross_weight_kg'])) for row in active) == Decimal('44.21')
    assert sum(len(metadata(row)[META_KEY]['source_rows']) for row in active) == 11
    assert any(change.get('derived_source_group') and change['fieldname'] == 'goods_value'
               for change in preview['changes'])
    if with_facts:
        from overseas_costing.services.shipment_cost_service import shipment_value
        values = [shipment_value(row) for row in active]
        assert all(not value.get('error') for value in values)
        assert sum(Decimal(value['amount_rmb']) for value in values) == sum(Decimal(line[-1]) for line in LINES)
