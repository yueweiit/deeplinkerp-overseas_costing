from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

from overseas_costing.services.logistics_settlement.model import parse_source
from overseas_costing.services.purchase_value_evidence import shipment_valuation


def source(*, value='2437.50', currency='人民币RMB', status='RUNNING', result='', rows=None):
    row = {'rowId': 'line-1', 'rowValue': [
        {'label': '物料编码 Código de material', 'value': 'FIXTURE1'},
        {'label': '物料名称（中文）Nombre del material (chino)', 'value': '测试夹具'},
        {'label': '规格型号Especificación / Modelo', 'value': 'LARGE'},
        {'label': '数量Cantidad', 'value': '2'},
        {'label': '单位Unidad', 'value': '套'},
        {'label': '货值Valor de mercancía', 'value': value},
        {'label': '币种Moneda', 'value': currency},
        {'label': '收件人Destinatario', 'value': '演示收件方'},
        {'label': '净重Peso Neto', 'value': '3.50'},
    ]}
    return parse_source({
        'corp_id': 'TEST-CORP', 'process_instance_id': 'TEST-APPROVAL', 'process_code': 'test-logistics',
        'status': status, 'result': result,
        'raw_payload': {'formComponentValues': [
            {'name': '项目归属Pertenencia del proyecto', 'value': '演示项目'},
            {'name': '货物信息Bienes', 'componentType': 'TableField', 'value': rows if rows is not None else [row]},
        ]},
    }, logistics_codes={'test-logistics'})


def test_running_logistics_archived_row_supplies_provisional_draft_fields_and_goods_value():
    from overseas_costing.services.logistics_settlement.initial_intake import project_initial_logistics_items

    approval = source()
    result = project_initial_logistics_items(approval)
    item, = result['rows']
    assert result['issues'] == []
    assert item['product_name'] == '测试夹具'
    assert item['spec_model'] == 'LARGE'
    assert item['recipient'] == '演示收件方'
    assert item['project_collection'] == '演示项目'
    assert item['net_weight_kg'] == '3.50'
    assert item['goods_value'] == '2437.50'
    assert item['extra_json']['initial_logistics_line'] == approval['goods'][0]['line_key']
    fact = item['extra_json']['adopted_purchase_value']
    assert fact['approval_status'] == 'RUNNING'
    assert fact['provisional'] is True
    assert fact['source_refs'][0]['source_row_id'] == 'line-1'
    assert fact['target_identity']['line_key'] == item['stable_line_key']
    assert shipment_valuation(item, fact)['amount_rmb'] == '2437.50'


def test_initial_intake_distinguishes_explicit_zero_missing_fx_and_invalid_approval():
    from overseas_costing.services.logistics_settlement.initial_intake import project_initial_logistics_items

    zero = project_initial_logistics_items(source(value='0', status='COMPLETED', result='agree'))['rows'][0]
    assert zero['goods_value'] == '0'
    assert zero['extra_json']['adopted_purchase_value']['explicit_zero'] is True
    assert zero['extra_json']['adopted_purchase_value']['provisional'] is False

    foreign = project_initial_logistics_items(source(currency='USD'))
    item, = foreign['rows']
    assert 'goods_value' not in item
    assert 'adopted_purchase_value' not in item['extra_json']
    assert item['extra_json']['initial_logistics_value_review']['original_amount'] == '2437.50'
    assert item['extra_json']['initial_logistics_value_review']['currency_fields'] == {'币种Moneda': 'USD'}
    assert '汇率' in foreign['issues'][0]['message']
    converted = project_initial_logistics_items(source(currency='USD'), fx_rates={'USD': '2'})
    assert converted['rows'][0]['goods_value'] == '4875.00'

    dummy_currency = project_initial_logistics_items(source(currency='待补币种'))
    assert 'goods_value' not in dummy_currency['rows'][0]
    assert '币种无法识别' in dummy_currency['issues'][0]['message']

    rejected = project_initial_logistics_items(source(status='COMPLETED', result='refuse'))
    assert 'goods_value' not in rejected['rows'][0]
    assert rejected['issues'][0]['code'] == 'OA_INITIAL_APPROVAL_NOT_ELIGIBLE'


def test_initial_intake_does_not_inherit_currency_from_other_goods_rows():
    from overseas_costing.services.logistics_settlement.initial_intake import project_initial_logistics_items

    other = deepcopy(source(currency='USD')['raw']['formComponentValues'][1]['value'][0])
    own = deepcopy(other)
    own['rowId'] = 'line-2'
    own['rowValue'][0]['value'] = 'FIXTURE2'
    own['rowValue'] = [cell for cell in own['rowValue'] if cell['label'] != '币种Moneda']
    result = project_initial_logistics_items(source(rows=[other, own]))
    assert 'goods_value' not in result['rows'][0]
    assert 'goods_value' not in result['rows'][1]
    assert result['rows'][1]['extra_json']['initial_logistics_value_review']['currency_fields'] == {}
    assert '未注明币种' in result['issues'][1]['message']


def test_existing_initial_intake_plan_only_supplements_unique_unchanged_unvalued_items():
    from overseas_costing.services.logistics_settlement.initial_intake import plan_existing_initial_items

    approval = source()
    item = {'name': 'EXISTING', 'source_type': 'oa_logistics', 'material_code': 'FIXTURE1', 'quantity': 2,
            'unit': '套', 'goods_value': 0,
            'extra_json': {'initial_logistics_line': approval['goods'][0]['line_key'], 'unrelated': 'keep'}}
    before = deepcopy(item)
    plan = plan_existing_initial_items(approval, [item])
    change, = plan['patches']
    assert change['name'] == 'EXISTING'
    assert change['values']['goods_value'] == '2437.50'
    assert change['values']['extra_json']['unrelated'] == 'keep'
    assert change['values']['extra_json']['adopted_purchase_value']['target_identity']['line_key'] == 'legacy:EXISTING'
    assert item == before
    assert plan_existing_initial_items(approval, [{**item, **change['values']}])['patches'] == []
    assert plan_existing_initial_items(approval, [{**item, 'unit_price': 10}])['patches'] == []
    assert plan_existing_initial_items(approval, [{**item, 'quantity': 1}])['patches'] == []
    assert plan_existing_initial_items(approval, [item, {**item, 'name': 'OTHER'}])['patches'] == []
    assert plan_existing_initial_items(approval, [{**item, 'actual_shipped_qty': 1}])['patches'] == []
    assert plan_existing_initial_items(approval, [{**item, 'manual_override_flag': 1}])['patches'] == []
    assert plan_existing_initial_items(approval, [{**item, 'extra_json': {'manual_shipment_valuation': {
        'manual': True, 'confirmed': True, 'amount_rmb': '0',
    }}}])['patches'] == []


def test_initial_intake_rejects_duplicate_unidentified_lines_without_merging_real_native_rows():
    from overseas_costing.services.logistics_settlement.initial_intake import project_initial_logistics_items

    first = deepcopy(source()['raw']['formComponentValues'][1]['value'][0])
    second = {**deepcopy(first), 'rowId': 'line-2'}
    separate = project_initial_logistics_items(source(rows=[first, second]))
    assert separate['goods_value_summary']['valued_count'] == 2
    assert len({row['stable_line_key'] for row in separate['rows']}) == 2

    first.pop('rowId')
    second.pop('rowId')
    duplicate = project_initial_logistics_items(source(rows=[first, second]))
    assert duplicate['goods_value_summary']['valued_count'] == 0
    assert {issue['code'] for issue in duplicate['issues']} == {'OA_INITIAL_SOURCE_LINE_UNVERIFIED'}


def test_archive_ensure_batch_inserts_priced_items_once_without_calculation(monkeypatch):
    from overseas_costing.services.logistics_settlement import runtime
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.tests.test_settlement_writer import Ledger

    store = Store.sqlite(sqlite3.connect(':memory:'))
    store.install()
    parsed = store.ingest(source())
    ledger = Ledger(store)
    monkeypatch.setattr(runtime, 'FrappeLedger', lambda: ledger)
    monkeypatch.setattr(runtime, 'frappe', SimpleNamespace(conf={}))
    with store.atomic():
        batch_name = runtime.ensure_batch(store, parsed)
    with store.atomic():
        assert runtime.ensure_batch(store, parsed) == batch_name

    batch = ledger.get('batch', batch_name)
    assert batch['status'] == 'Draft'
    assert batch['confirm_status'] == 'Pending'
    assert batch['item_count'] == 1
    assert not batch.get('total_goods_value')
    item, = ledger.rows('item', batch=batch_name)
    assert item['goods_value'] == '2437.50'
    fact = json.loads(item['extra_json'])['adopted_purchase_value']
    assert fact['source_refs'][0]['source_hash'] == parsed['snapshot']
    assert shipment_valuation(item, fact)['amount_rmb'] == '2437.50'
