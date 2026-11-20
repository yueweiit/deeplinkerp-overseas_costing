"""Exact same-code purchase specs may repeat the known product name at the start."""

from copy import deepcopy

import pytest

from overseas_costing.services import import_service, logistics_purchase_facts_service
from overseas_costing.services.logistics_settlement.model import merchandise_price
from overseas_costing.services.purchase_value_evidence import bind_purchase_value, identity_matches, mapped_row, spec_identity
from overseas_costing.services.source_review_extract_service import _match_item
from overseas_costing.tests.test_source_goods_value_adoption import project_defaults, review


PRODUCT = '硅胶耳机壳'
SPEC = 'AirPods 4-2.1MM厚-Rosa'


def target(**changes):
    return {'name': 'I1', 'material_code': 'FL004157', 'product_name': PRODUCT, 'spec_model': SPEC,
            'quantity': 50, 'actual_shipped_qty': 50, 'actual_shipped_qty_mode': 'EXPLICIT_SOURCE',
            'unit': '个', 'shipped_uom': '个', 'purchase_uom': '个', 'goods_value': 0, **changes}


def purchase(specification=PRODUCT + SPEC, code='FL004157'):
    return {'source_id': 'PURCHASE-209901010000000000001', 'source_kind': 'approval_form',
            'available': True, 'approval_role': 'purchase', 'approval_no': '209901010000000000001',
            'form_fields': {'币种Moneda': '人民币RMB', '采购明细': [{
                '物品编码Código': code, '物品名称': specification,
                '物品规格Especificacion': specification, '数量Cantidad': 50,
                '单位Unidad': '个', '单价Precio': 2, '总金额Monto Total': 100}]}}


def test_shared_spec_normalization_preserves_internal_text_and_never_erases_a_prefix_only_spec():
    row = target()
    assert spec_identity('  硅胶耳机壳ＡｉｒＰｏｄｓ  ４-２.１ＭＭ厚-Ｒｏｓａ ', row,
                         material_code='FL004157') == SPEC.casefold()
    assert spec_identity(PRODUCT, row, material_code='FL004157') == PRODUCT
    assert spec_identity('AirPods 4-' + PRODUCT + '-Rosa', row,
                         material_code='FL004157') == ('AirPods 4-' + PRODUCT + '-Rosa').casefold()
    assert spec_identity('AirPods4-2.1MM厚-Rosa', row, material_code='FL004157') != SPEC.casefold()


@pytest.mark.parametrize('target_code,source_code', [
    ('', 'FL004157'), ('/', 'FL004157'), ('--', 'FL004157'), ('FL004158', 'FL004157'),
    ('/', '/'), ('--', '--'), ('NEW', 'NEW'), ('0', '0'), ('|', '|'),
])
def test_product_prefix_requires_explicit_matching_nonplaceholder_material_codes(target_code, source_code):
    row = target(material_code=target_code)
    assert spec_identity(PRODUCT + SPEC, row, material_code=source_code) == (PRODUCT + SPEC).casefold()


@pytest.mark.parametrize('source_spec,source_code', [
    (PRODUCT + 'AirPods 4-2.1MM厚-Negro', 'FL004157'),
    (PRODUCT + 'AirPods 4-2.2MM厚-Rosa', 'FL004157'),
    (PRODUCT + 'AirPods 3-2.1MM厚-Rosa', 'FL004157'),
    (PRODUCT + SPEC, 'FL004158'),
    ('AirPods 4-' + PRODUCT + '-2.1MM厚-Rosa', 'FL004157'),
    ('', 'FL004157'),
])
def test_strict_fact_identity_still_rejects_changed_colors_thickness_models_codes_and_empty_specs(source_spec, source_code):
    row = target()
    fact = {'material_code': source_code, 'spec_model': source_spec, 'uom': '个'}
    assert not identity_matches(row, fact)
    matched = _match_item([row], source_code, mapped={'spec_model': source_spec})
    assert matched == (row if not source_spec else None)
    if source_spec:
        mapped = {**fact, 'product_name': PRODUCT}
        assert import_service._match_item(mapped, import_service._index_items([row]))[1] == []
        catalog = review([row], purchase(source_spec, source_code))
        assert not [candidate for candidate in catalog['field_candidates'] if candidate['fieldname'] == 'goods_value']


def test_strict_unknown_spec_cannot_match_a_spec_that_would_become_empty_after_prefix_removal():
    row = target(spec_model='')
    assert not identity_matches(row, {'material_code': row['material_code'], 'spec_model': PRODUCT, 'uom': '个'})


def test_native_price_matchers_share_the_prefix_rule_without_changing_original_specs():
    row = target()
    mapped = {'material_code': row['material_code'], 'spec_model': PRODUCT + SPEC}
    before = deepcopy((row, mapped))
    assert _match_item([row], mapped['material_code'], mapped=mapped) == row
    assert import_service._match_item(mapped, import_service._index_items([row])) == ('material_code', [row])
    candidates = [
        {**mapped, 'purchase_key': 'ROSA', 'purchase_fact': {}},
        {**mapped, 'spec_model': PRODUCT + SPEC.replace('Rosa', 'Negro'), 'purchase_key': 'NEGRO', 'purchase_fact': {}},
    ]
    assert logistics_purchase_facts_service._matching_candidates(row, {}, candidates) == candidates[:1]
    assert (row, mapped) == before


@pytest.mark.parametrize('color,code', [('Azul marino', 'FL004156'), ('Rosa', 'FL004157'),
                                       ('Rojo', 'FL004158'), ('Negro', 'FL004159')])
def test_native_purchase_review_and_writer_keep_four_colors_and_original_evidence_separate(color, code):
    from overseas_costing.services.material_ai_selection_writer import write_rows
    from overseas_costing.services.shipment_cost_service import object_json, shipment_value
    from overseas_costing.tests.test_freight_lines import setup_cost

    store, ledger, batch, version, current, *_ = setup_cost()
    specification = SPEC.replace('Rosa', color)
    row = target(name=current['name'], material_code=code, spec_model=specification)
    source = purchase(PRODUCT + specification, code)
    before = deepcopy((row, source))
    catalog = review([row], source)
    projected = project_defaults([row], catalog)
    assert shipment_value(projected['rows'][0])['amount_rmb'] == '100'
    ledger.put('item', current['name'], row)
    preview = {**projected, 'batch': batch['name'], 'version': version['name'], 'id': 'P',
               'revision': 'R', 'run_id': 'RUN', 'source_context': {}, 'sources': []}
    write_rows(store, ledger, preview, {})
    saved = ledger.get('item', current['name'])
    fact = object_json(saved['extra_json'])['adopted_purchase_value']
    assert fact['spec_model'] == PRODUCT + specification
    assert fact['target_identity']['spec_model'] == specification.casefold()
    assert saved['spec_model'] == specification and saved['product_name'] == PRODUCT
    assert shipment_value(saved)['amount_rmb'] == '100'
    assert shipment_value({**saved, 'spec_model': specification + '-other'})['error'] == 'PURCHASE_VALUE_IDENTITY_MISMATCH'
    assert shipment_value({**saved, 'material_code': 'OTHER'})['error'] == 'PURCHASE_VALUE_IDENTITY_MISMATCH'
    assert (row, source) == before
    assert ledger.get('batch', batch['name'])['writeback_status'] == 'Not Started'


def test_binding_keeps_its_existing_blank_spec_policy_and_unit_guard():
    row = target()
    fact = {'material_code': row['material_code'], 'spec_model': PRODUCT + SPEC, 'uom': '套',
            'source_refs': [{'document_id': 'DOC', 'row': 1}]}
    assert not identity_matches(row, fact)
    assert identity_matches(row, fact, check_uom=False)
    assert bind_purchase_value(row, {**fact, 'spec_model': ''}) is not None
    assert not identity_matches(row, {**fact, 'spec_model': '', 'uom': '个'})


@pytest.mark.parametrize('currency', [None, 'UNKNOWN'])
def test_cached_statement_columns_reach_native_identity_without_currency_or_price_defaults(currency):
    raw = {'品名': '测试磁片', '规格': 'TEST-SPEC 8x3', '数量': 3,
           '单位': '片', '单价': 1.25, '金额': 3.75, '币种Moneda': currency}
    before = deepcopy(raw)

    mapped = mapped_row(raw)
    price = merchandise_price(raw)

    assert mapped['product_name'] == '测试磁片' and mapped['spec_model'] == 'TEST-SPEC 8x3'
    assert mapped['material_code'] is None and mapped['purchase_currency'] == currency
    assert mapped['quantity'] == 3 and mapped['unit'] == '片'
    assert price['price'] == '1.25' and price['price_uom'] == '片'
    assert price['currency'] == (currency or '')
    assert not merchandise_price({'品名': '测试磁片', '规格': 'TEST-SPEC 8x3'})['present']
    assert raw == before


@pytest.mark.parametrize('oa_name,oa_spec', [('原有OA名称', 'OA-SPEC'), ('', None)])
def test_cached_statement_aliases_only_fill_empty_oa_identity(oa_name, oa_spec):
    raw = {'物料名称（中文）Nombre del material (chino)': oa_name,
           '规格型号Especificación / Modelo': oa_spec,
           '品名': '测试磁片', '规格': 'TEST-SPEC 8x3'}
    before = deepcopy(raw)

    mapped = mapped_row(raw)

    assert mapped['product_name'] == (oa_name or '测试磁片')
    assert mapped['spec_model'] == (oa_spec or 'TEST-SPEC 8x3')
    assert raw == before


@pytest.mark.parametrize('separate_columns', [{}, {'品名': '测试磁片', '规格': 'TEST-SPEC 8x3'}])
def test_combined_statement_description_is_preserved_without_splitting(separate_columns):
    raw = {'品名规格': '测试磁片 TEST-SPEC 8x3', **separate_columns}
    before = deepcopy(raw)

    mapped = mapped_row(raw)

    assert mapped['product_name'] == separate_columns.get('品名')
    assert mapped['spec_model'] == separate_columns.get('规格')
    assert mapped['purchase_currency'] is None
    assert raw == before
