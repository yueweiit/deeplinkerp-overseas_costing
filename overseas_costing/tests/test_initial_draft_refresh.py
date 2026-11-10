"""Existing OA draft reconciliation uses the same verified intake projection."""
from datetime import datetime, timedelta

import pytest

from overseas_costing.tests.test_settlement_writer import Ledger
from overseas_costing.tests.test_logistics_settlement import store


@pytest.fixture
def draft(store):
    db = store
    ledger = Ledger(db)
    source = {'id': 'S1', 'snapshot': 'SNAP-1', 'corp': 'C1', 'instance': 'I1',
              'kind': 'logistics', 'status': 'RUNNING'}
    batch = ledger.create('batch', {
        'source_type': 'oa_logistics', 'source_corp_id': 'C1', 'source_instance_id': 'I1',
        'confirm_status': 'Pending', 'writeback_status': 'Not Started',
        'status': 'Draft', 'modified': '2026-01-01 00:00:00', 'item_count': 0,
    })
    version = ledger.create('version', {
        'batch': batch['name'], 'version_type': 'Estimated', 'source_type': 'Import',
        'status': 'Active', 'is_current': 1, 'version_code': '初始录入',
    })
    batch = ledger.put('batch', batch['name'], {'current_version': version['name']})
    item = ledger.create('item', {'batch': batch['name'], 'version': version['name'],
                                  'material_code': 'SKU1', 'goods_value': 0,
                                  'extra_json': '{"initial_logistics_line":"LINE-1"}'})
    return db, ledger, source, batch, version, item


def wire(monkeypatch, draft):
    from overseas_costing.services.logistics_settlement import initial_draft_refresh as refresh

    db, ledger, source, batch, version, item = draft

    def bundle(name, *, store, ledger, lock):
        return {'batch': ledger.get('batch', name, lock=lock),
                'version': ledger.get('version', version['name'], lock=lock),
                'source': source, 'context': {
                    'root_kind': 'logistics', 'root_source_id': 'S1',
                    'source_snapshot': source['snapshot'], 'available': True,
                }}

    def plan(source, items, *, fx_rates, supplier_rows, verified_legacy_positions):
        assert source['id'] == 'S1'
        assert items and items[0]['name'] == item['name']
        return {'patches': [{'name': item['name'], 'values': {'goods_value': '12.30'}}]
                if not items[0]['goods_value'] else [], 'issues': []}

    monkeypatch.setattr(refresh, 'load_source_bundle', bundle)
    monkeypatch.setattr(refresh, 'plan_existing_initial_items', plan)
    return refresh.sync_initial_draft_items


def test_scoped_refresh_uses_frozen_preview_and_repeats_without_writes(monkeypatch, draft):
    sync = wire(monkeypatch, draft)
    db, ledger, source, batch, version, item = draft
    args = (db, ledger, batch['name'], source['id'])
    preview = sync(*args, dry_run=True)
    assert preview['status'] == 'ready' and preview['update_count'] == 1
    assert ledger.get('item', item['name'])['goods_value'] == 0
    with db.atomic():
        applied = sync(*args, expected_plan_hash=preview['plan_hash'])
    assert applied['status'] == 'updated'
    assert ledger.get('item', item['name'])['goods_value'] == '12.30'
    assert ledger.get('batch', batch['name'])['item_count'] == 1
    assert ledger.get('batch', batch['name'])['confirm_status'] == 'Pending'
    assert ledger.get('version', version['name']) == version
    assert db.count('audit', action='initial_logistics_draft_synced') == 1
    with db.atomic():
        assert sync(*args)['status'] == 'unchanged'
    assert db.count('audit', action='initial_logistics_draft_synced') == 1


@pytest.mark.parametrize('field,value,reason', [
    ('confirm_status', 'Confirmed', 'protected_draft'),
    ('writeback_status', 'Success', 'protected_draft'),
    ('edit_lock_expires_at', (datetime.now() + timedelta(hours=1)).isoformat(), 'editing'),
])
def test_existing_refresh_protects_current_business_and_edit_state(monkeypatch, draft, field, value, reason):
    sync = wire(monkeypatch, draft)
    db, ledger, source, batch, _, item = draft
    ledger.put('batch', batch['name'], {field: value})
    with db.atomic():
        assert sync(db, ledger, batch['name'], source['id'])['status'] == reason
    assert not ledger.get('item', item['name'])['goods_value']


def test_existing_refresh_rejects_source_change_after_preview(monkeypatch, draft):
    sync = wire(monkeypatch, draft)
    db, ledger, source, batch, _, item = draft
    args = (db, ledger, batch['name'], source['id'])
    preview = sync(*args, dry_run=True)
    source['snapshot'] = 'SNAP-2'
    with pytest.raises(ValueError, match='刷新'):
        with db.atomic():
            sync(*args, expected_plan_hash=preview['plan_hash'])
    assert not ledger.get('item', item['name'])['goods_value']


@pytest.mark.parametrize("change", [None, "source_order", "erp_order", "amount", "count"])
def test_legacy_positions_require_complete_original_goods_table_and_erp_order(change):
    from copy import deepcopy

    from overseas_costing.services.logistics_settlement.initial_draft_refresh import _verified_legacy_positions
    from overseas_costing.services.logistics_settlement.model import dumps
    from overseas_costing.scripts.import_oa_logistics import extract_form_fields

    label = "货物信息Bienes"
    raw_rows = [[{"name": "数量Cantidad", "value": str(qty)},
                 {"name": "货值Valor de mercancía", "value": str(amount)}]
                for qty, amount in [(2, 12), (2, 17)]]
    raw = {"formComponentValues": [{"name": label, "componentType": "TableField", "value": raw_rows}]}
    source = {"raw": deepcopy(raw), "goods": [
        {"line_key": "same-legacy-key", "material_code": "SKU1", "quantity": "2", "unit": "套",
         "source_table": label, "source_position": idx}
        for idx in [1, 2]
    ]}
    batch = {"extra_json": dumps({"source": "dingtalk_oa_logistics", "form_fields": extract_form_fields(raw)})}
    items = [{"name": f"I{idx}", "material_code": "SKU1", "quantity": 2, "unit": "套", "row_no": idx,
              "extra_json": '{"initial_logistics_line":"same-legacy-key"}'}
             for idx in [1, 2]]
    if change == "source_order":
        source["raw"]["formComponentValues"][0]["value"].reverse()
    if change == "erp_order":
        items[0]["row_no"] = 3
    if change == "amount":
        source["raw"]["formComponentValues"][0]["value"][0][1]["value"] = "18"
    if change == "count":
        items.pop()
    expected = {} if change else {"I1": (label, 1), "I2": (label, 2)}
    assert _verified_legacy_positions(batch, source, items) == expected
