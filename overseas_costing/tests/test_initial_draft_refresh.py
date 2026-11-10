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

    def plan(source, items, *, fx_rates, supplier_rows):
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
