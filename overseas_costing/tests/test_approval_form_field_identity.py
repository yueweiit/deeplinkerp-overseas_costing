"""Hidden duplicate OA controls must not erase the visible purchase currency."""

import json
from copy import deepcopy

import pytest

from overseas_costing.services import dingtalk_approval_service, packing_snapshot_service
from overseas_costing.services.material_ai_selection_writer import write_rows
from overseas_costing.services.shipment_cost_service import object_json, shipment_value
from overseas_costing.tests.test_freight_lines import setup_cost
from overseas_costing.tests.test_source_goods_value_adoption import project_defaults, review


def purchase_source(currency_values, *, color='Rosa', code='FL004157'):
    specification = '硅胶耳机壳AirPods 4-2.1MM厚-' + color
    cells = {'物品编码Código': code, '物品名称Nombre del artículo': specification,
             '物品规格Especificacion': specification, '数量Cantidad': 50,
             '单位Unidad': '个', '单价Precio': 2, '总金额Monto Total': 100}
    payload = {'formComponentValues': [
        *[{'name': '币种Moneda', 'value': value} for value in currency_values],
        {'name': '需求明细Desglose de los gastos', 'componentType': 'TableField',
         'value': json.dumps([{'rowValue': [{'label': label, 'value': str(value)}
                                         for label, value in cells.items()]}], ensure_ascii=False)},
    ]}
    approval = {'instance_id': 'PURCHASE', 'business_id': '209901010000000000001',
                'title': '采购支出 Gastos de compra',
                'form_fields': dingtalk_approval_service._form_fields(payload)}
    before = deepcopy(approval)
    source = packing_snapshot_service._list_approval_body_ai_sources(
        'BATCH', detail={'ok': True, 'linked_purchase_approvals': [approval]})[0]
    assert approval == before
    return source


def target(**changes):
    return {'name': 'I1', 'material_code': 'FL004157', 'product_name': '硅胶耳机壳',
            'spec_model': 'AirPods 4-2.1MM厚-Rosa', 'quantity': 0,
            'actual_shipped_qty': 50, 'actual_shipped_qty_mode': 'EXPLICIT_SOURCE',
            'unit': '个', 'shipped_uom': '个', 'goods_value': 0, 'unit_price': 0,
            **changes}


@pytest.mark.parametrize('currencies', [
    ['人民币RMB', None], [None, '人民币RMB'],
    ['人民币RMB', '', '人民币RMB', None],
])
def test_empty_duplicate_controls_preserve_the_explicit_currency_in_native_review(currencies):
    source = purchase_source(currencies)
    assert source['form_fields']['币种Moneda'] == '人民币RMB'
    projected = project_defaults([target()], review([target()], source))
    assert shipment_value(projected['rows'][0])['amount_rmb'] == '100'


@pytest.mark.parametrize('currencies', [
    ['人民币RMB', '美元USD', None], ['美元USD', None, '人民币RMB'],
])
def test_nonempty_duplicate_currency_conflicts_remain_unusable(currencies):
    source = purchase_source(currencies)
    assert len(source['form_fields']['币种Moneda']) == 2
    catalog = review([target()], source)
    values = [candidate for candidate in catalog['field_candidates']
              if candidate['fieldname'] in {'unit_price', 'goods_value', 'shipment_value_rmb'}]
    assert values and all(not candidate['can_apply'] for candidate in values)
    projected = project_defaults([target()], catalog)
    assert shipment_value(projected['rows'][0])['amount_rmb'] is None


@pytest.mark.parametrize('color,code', [
    ('Azul marino', 'FL004156'), ('Rosa', 'FL004157'),
    ('Rojo', 'FL004158'), ('Negro', 'FL004159'),
])
def test_actual_duplicate_header_and_split_specs_survive_native_write_and_reread(color, code):
    store, ledger, batch, version, current, *_ = setup_cost()
    row = target(name=current['name'], material_code=code,
                 spec_model='AirPods 4-2.1MM厚-' + color)
    source = purchase_source(['人民币RMB', None], color=color, code=code)
    projected = project_defaults([row], review([row], source))
    ledger.put('item', current['name'], row)
    write_rows(store, ledger, {**projected, 'batch': batch['name'], 'version': version['name'],
                              'id': 'P', 'revision': 'R', 'run_id': 'RUN',
                              'source_context': {}, 'sources': []}, {})
    saved = ledger.get('item', current['name'])
    fact = object_json(saved['extra_json'])['adopted_purchase_value']
    assert shipment_value(saved)['amount_rmb'] == '100'
    assert fact['currency'] == 'RMB' and fact['currency_scope'] == 'header'
    assert fact['spec_model'] == '硅胶耳机壳' + row['spec_model']
    assert saved['spec_model'] == row['spec_model'] and saved['quantity'] == 0
    assert ledger.get('batch', batch['name'])['writeback_status'] == 'Not Started'
