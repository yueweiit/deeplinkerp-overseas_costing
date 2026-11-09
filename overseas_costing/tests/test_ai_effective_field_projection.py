"""A selected source field must agree with the effective saved material."""
from copy import deepcopy
from decimal import Decimal

import pytest

from overseas_costing.services import material_ai_row_selection as rows
from overseas_costing.services.material_input_service import present_material_row
from overseas_costing.services.shipment_cost_service import object_json, shipment_value
from overseas_costing.tests.test_adopted_value_consistency import save, saved_context
from overseas_costing.tests.test_source_goods_value_adoption import evidence, review


def selected(items, catalog, *, fields=None, mode='update_selected'):
    chosen = [c for c in catalog['field_candidates'] if (
        c['fieldname'] in fields if fields is not None else c['default_selected'])]
    return rows.project(items, catalog, [], [], mode, field_choices={
        f"{c['item_name']}:{c['fieldname']}": c['candidate_id'] for c in chosen})


@pytest.mark.parametrize('item_spec,source_spec', [('', 'GRADE-A'), ('GRADE-A', '')])
def test_one_sided_spec_uses_bound_source_and_rejects_later_item_change(item_spec, source_spec):
    context = saved_context()
    _, ledger, _, _, items, source = context
    item = items[0]
    item['spec_model'] = item_spec
    source['form_fields']['货物信息Bienes'][0].update({'规格型号': source_spec, '货值Valor de mercancía': 7319})
    ledger.put('item', item['name'], item)
    preview = selected(items, review(items, source), fields={'goods_value'})
    row = preview['rows'][0]
    assert preview['can_apply'] and Decimal(row['shipment_value_rmb']) == 7319
    assert row['purchase_value_evidence']['original_amount'] == '7319'
    assert next(r for r in preview['field_results'] if r['fieldname'] == 'goods_value')['status'] == 'apply'
    saved = save(preview, context)
    assert Decimal(present_material_row(saved)['shipment_value_rmb']) == 7319
    assert shipment_value({**saved, 'spec_model': 'DIFFERENT'})['error'] == 'PURCHASE_VALUE_IDENTITY_MISMATCH'
    legacy = object_json(saved['extra_json'])
    fact = legacy['adopted_purchase_value']
    fact.pop('target_identity')
    assert shipment_value({**saved, 'extra_json': legacy})['error'] == 'PURCHASE_VALUE_IDENTITY_MISMATCH'


@pytest.mark.parametrize('with_cargo', [False, True])
def test_final_shipping_quantity_agrees_between_preview_and_reread(with_cargo):
    context = saved_context()
    _, ledger, _, _, items, source = context
    item = items[0]
    item.update(quantity='8', actual_shipped_qty=0, actual_shipped_qty_mode='DEFAULT_PURCHASE')
    if with_cargo:
        item['extra_json'] = {'settlement_cargo': {'quantity': '8', 'unit': 'kg',
                              'line_key': 'LINE', 'source_snapshot': 'old', 'binding_id': 'old'}}
    ledger.put('item', item['name'], item)
    preview = selected(items, review(items, source))
    row = preview['rows'][0]
    assert row['actual_shipped_qty'] == '4'
    assert Decimal(row['shipment_value_rmb']) == 800
    saved = save(preview, context)
    assert Decimal(present_material_row(saved)['shipment_value_rmb']) == 800
    assert saved['actual_shipped_qty_mode'] == 'EXPLICIT_SOURCE'
    if with_cargo:
        assert object_json(saved['extra_json'])['settlement_cargo']['quantity'] == '4'


def test_value_with_incompatible_retained_uom_reports_and_blocks_without_changing_row():
    items, source = evidence((800,), (4,))
    items[0].update(shipped_uom='箱', actual_shipped_qty_mode='MANUAL_CONFIRMED')
    preview = selected(items, review(items, source), fields={'goods_value'})
    assert not preview['can_apply']
    assert preview['rows'][0]['goods_value'] == 0
    assert preview['field_results'][0]['status'] == 'review'
    assert preview['field_results'][0]['code'] == 'PURCHASE_VALUE_UOM_MISMATCH'


def test_same_amount_does_not_make_unbound_candidate_writable():
    items, source = evidence((800,), (1,))
    items[0].update(quantity=10, actual_shipped_qty=1)
    fact = next(row['_purchase_value_fact'] for row in review(items, source)['rows'] if row.get('_purchase_value_fact'))
    proposals = [{
        'proposal_id': name, 'proposal_type': 'item_update', 'target_item_name': 'I1', 'confidence': 1,
        'source_refs': [{'source_id': 'LOG', 'field': '货物信息Bienes', 'row': source_row}],
        **({'_purchase_value_fact': fact} if source_row else {}),
        'payload': {'fields': {'goods_value': 800}}}
        for name, source_row in [('unbound', None), ('bound', 1)]]
    catalog = rows.catalog(items, proposals, [], {}, run_id='R', sources=[source])
    values = [c for c in catalog['field_candidates'] if c['fieldname'] == 'goods_value']
    assert len(values) == 2
    assert sum(c['can_apply'] for c in values) == 1
    preview = selected(items, catalog)
    assert Decimal(preview['rows'][0]['shipment_value_rmb']) == 800


@pytest.mark.parametrize('field_choices', [True, False])
def test_verified_metadata_fills_only_blank_fields_and_survives_writer(field_choices):
    context = saved_context()
    _, ledger, _, _, items, source = context
    item = items[0]
    item.update(product_name='', spec_model='KEPT')
    ledger.put('item', item['name'], item)
    values = {'product_name': '已核实物料', 'spec_model': 'GRADE-A', 'recipient': '收货方'}
    ref = {'source_id': 'LOG', 'row': 1}
    proposal = {'proposal_id': 'METADATA', 'proposal_type': 'item_update', 'target_item_name': item['name'],
                'source_refs': [ref], 'confidence': 1, 'result_origin': 'SYSTEM',
                '_review_verified_fields': {field: {'source_refs': [ref], 'raw_value': value, 'scope': 'row'}
                                            for field, value in values.items()},
                'payload': {'fields': values}}
    catalog = rows.catalog(items, [proposal], [], {}, run_id='R', sources=[source])
    if field_choices:
        preview = selected(items, catalog, fields=set(values))
    else:
        candidate = next(row for row in catalog['rows'] if row['origin'] == 'source')
        preview = rows.project(items, catalog, [candidate['row_id']], [], 'update_selected')
    assert {result['fieldname']: result['status'] for result in preview['field_results']} == {
        'product_name': 'apply', 'spec_model': 'retain', 'recipient': 'apply'}
    saved = save(preview, context)
    assert (saved['product_name'], saved['spec_model'], saved['recipient']) == ('已核实物料', 'KEPT', '收货方')
    unverified = deepcopy(proposal)
    unverified.pop('_review_verified_fields')
    assert not rows.catalog(items, [unverified], [], {}, run_id='R', sources=[source])['field_candidates']


@pytest.mark.parametrize('chosen_fields,expected_apply', [
    ({'shipped_uom'}, False), ({'shipped_uom', 'actual_shipped_qty'}, True),
])
def test_changed_unit_cannot_reinterpret_prior_quantity(chosen_fields, expected_apply):
    items, source = evidence((800,), (4,))
    items[0].update(actual_shipped_qty='2', shipped_uom='箱')
    source['form_fields']['货物信息Bienes'][0]['单位Unidad'] = '套'
    projection = selected(items, review(items, source), fields=chosen_fields)
    row = projection['rows'][0]
    assert projection['can_apply'] is expected_apply
    if expected_apply:
        assert (row['actual_shipped_qty'], row['shipped_uom']) == ('4', '套')
    else:
        assert (row['actual_shipped_qty'], row['shipped_uom']) == ('2', '箱')
        assert projection['field_results'][0]['code'] == 'SHIPPING_UNIT_QUANTITY_REQUIRED'


def test_existing_supplier_no_supplier_marker_is_not_an_empty_value():
    assert not rows.missing({'supplier': '/'}, 'supplier')


@pytest.mark.parametrize('value', [{'purchase_currency': 'RMB'}, {'unit_price': '11'}])
def test_isolated_price_fields_cannot_change_existing_purchase_basis(value):
    items, source = evidence((800,), (4,))
    items[0].update(unit_price='11', purchase_currency='USD', unit_price_uom='kg')
    proposals = [{'proposal_id': 'ORPHAN', 'proposal_type': 'item_update',
                  'target_item_name': items[0]['name'], 'confidence': 1,
                  'source_refs': [{'source_id': 'LOG', 'row': 1}], 'payload': {'fields': value}}]
    catalog = rows.catalog(items, proposals, [], {}, run_id='R', sources=[source])
    preview = selected(items, catalog, fields=set(value))
    assert not preview['can_apply']
    assert preview['rows'][0]['purchase_currency'] == 'USD'
    assert preview['rows'][0]['unit_price'] == '11'
    assert preview['field_results'][0]['status'] == 'review'


def test_manual_value_blocks_adoption_of_new_purchase_basis():
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    items, source = evidence((800,), (4,))
    item = items[0]
    item['extra_json'] = {'manual_shipment_valuation': build_manual_shipment_valuation(item, 950, actor='reviewer')}
    before = deepcopy(item)
    preview = selected(items, review(items, source), fields={'goods_value'})
    row = preview['rows'][0]
    assert not preview['can_apply']
    assert preview['field_results'][0]['code'] == 'MANUAL_VALUE_RETAINED'
    assert row['goods_value'] == before['goods_value'] and row['extra_json'] == before['extra_json']
    assert Decimal(row['shipment_value_rmb']) == 950


def test_trusted_packing_zero_is_distinct_from_a_placeholder():
    from overseas_costing.tests.test_adopted_value_consistency import SOURCES, shipment_proposal

    items, _ = evidence((800,), (4,))
    catalog = rows.catalog(items, [shipment_proposal(items[0], amount='0')], [], {}, run_id='R', sources=SOURCES)
    preview = selected(items, catalog, fields={'shipment_value_rmb'})
    assert preview['can_apply']
    assert Decimal(preview['rows'][0]['shipment_value_rmb']) == 0
    assert preview['field_results'][0]['status'] == 'apply'


def test_known_erp_unit_aliases_compare_without_rewriting_source_unit():
    items, source = evidence((800,), (4,))
    items[0].update(unit='套：conjunto', shipped_uom='套：conjunto')
    source['form_fields']['货物信息Bienes'][0]['单位Unidad'] = '套'
    preview = selected(items, review(items, source), fields={'goods_value'})
    assert preview['can_apply']
    row = preview['rows'][0]
    assert row['shipped_uom'] == '套：conjunto'
    assert row['purchase_value_evidence']['uom'] == '套'
    assert Decimal(row['shipment_value_rmb']) == 800


def test_stale_higher_priority_value_falls_back_after_final_shipping_changes():
    from overseas_costing.tests.test_adopted_value_consistency import shipment_proposal
    from overseas_costing.services import source_review_extract_service
    from overseas_costing.services import material_ai_fill_service

    items, source = evidence((800,), (4,))
    items[0].update(quantity='8', actual_shipped_qty=0, actual_shipped_qty_mode='DEFAULT_PURCHASE')
    raw = source_review_extract_service.build_system_approval_proposals(items, source)
    for proposal in raw:
        for ref in proposal['source_refs']:
            ref['document_id'] = 'DOC'
    normalized = material_ai_fill_service.normalize_source_review_proposals(raw, items, [
        {'document_id': 'DOC', 'form_fields': source['form_fields'], 'source_ref': {
            'source_id': 'LOG', 'approval_role': 'international_logistics'}}],
        trusted_system_proposal_ids={proposal['proposal_id'] for proposal in raw})
    older = shipment_proposal({**items[0], 'actual_shipped_qty': '8'}, amount='1600')
    older['payload']['rows'][0]['extra_json']['shipment_valuation']['quantity'] = '8'
    older['payload']['rows'][0]['_review_source_values'] = {
        'material_code': items[0]['material_code'], 'product_name': items[0]['product_name']}
    sources = [source, {'source_id': 'SHIP', 'source_kind': 'approval_attachment', 'approval_role': 'payment'}]
    catalog = rows.catalog(items, [older, *normalized], [], {}, run_id='R', sources=sources)
    preview = selected(items, catalog)
    assert preview['can_apply']
    assert not any(c['fieldname'] == 'shipment_value_rmb' and c['default_selected'] for c in catalog['field_candidates'])
    assert Decimal(preview['rows'][0]['shipment_value_rmb']) == 800


def test_spec_only_does_not_activate_unmatched_legacy_purchase_value():
    items, source = evidence((800,), (4,))
    item = items[0]
    item['spec_model'] = ''
    source['form_fields']['货物信息Bienes'][0]['规格型号'] = 'GRADE-A'
    fact = next(row['_purchase_value_fact'] for row in review(items, source)['rows'] if row.get('_purchase_value_fact'))
    item.update(goods_value='800', extra_json={'adopted_purchase_value': fact})
    assert shipment_value(item)['error'] == 'PURCHASE_VALUE_IDENTITY_MISMATCH'
    catalog = review(items, source)

    spec_only = selected(items, catalog, fields={'spec_model'})
    assert not spec_only['can_apply']
    assert spec_only['rows'][0]['spec_model'] == ''
    assert spec_only['rows'][0]['shipment_value_rmb'] is None
    assert spec_only['field_results'][0]['code'] == 'LEGACY_PURCHASE_VALUE_REVIEW_REQUIRED'

    explicitly_rebound = selected(items, catalog, fields={'spec_model', 'goods_value'})
    assert explicitly_rebound['can_apply']
    assert explicitly_rebound['rows'][0]['spec_model'] == 'GRADE-A'
    assert Decimal(explicitly_rebound['rows'][0]['shipment_value_rmb']) == 800
