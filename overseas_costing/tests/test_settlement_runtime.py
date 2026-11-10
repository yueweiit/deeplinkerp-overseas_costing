import pytest
import json
import sqlite3
from types import SimpleNamespace

from overseas_costing.tests.test_settlement_writer import setup
from overseas_costing.tests.test_settlement_writer import Ledger
from overseas_costing.tests.test_logistics_settlement import source, ingest, store
from overseas_costing.services.logistics_settlement.writer import apply_binding
from overseas_costing.services.logistics_settlement import runtime
from overseas_costing.services.logistics_settlement.model import parse_source
from overseas_costing.services.logistics_settlement.store import Store
from overseas_costing.services.logistics_settlement.ledger import FrappeLedger


def attach_runtime(monkeypatch, s, ledger):
    monkeypatch.setattr(runtime, 'installed', lambda: True)
    monkeypatch.setattr(runtime.Store, 'frappe', lambda: s)
    monkeypatch.setattr(runtime, 'FrappeLedger', lambda: ledger)
    monkeypatch.setattr(runtime, 'archive', lambda: pytest.fail('normal page query contacted upstream'))


def waybill_runtime_context(comment='DHL 3080665836\nETA 2026-9-18已签收'):
    s = Store.sqlite(sqlite3.connect(':memory:'))
    s.install()
    ledger = Ledger(s)
    batch = ledger.create('batch', {
        'batch_no': 'B-WAYBILL',
        'source_corp_id': 'C',
        'source_instance_id': 'L-WAYBILL',
        'waybill_no': '',
        'status': 'Calculated',
        'confirm_status': 'Pending',
        'extra_json': '{}',
    })
    raw = source('L-WAYBILL', 'logistics', text='国际快递')
    raw['raw_payload']['operationRecords'] = [{'remark': comment}]
    parsed = s.ingest(parse_source(raw, logistics_codes={'logistics'}))
    s.insert('batch_map', {
        'id': parsed['id'], 'source_id': parsed['id'], 'batch': batch['name'], 'data': '{}',
    })
    return s, ledger, batch, parsed


def test_existing_batch_mapping_syncs_one_comment_waybill_without_dirtying_cost(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        result = runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    provenance = json.loads(saved['extra_json'])['waybill_source']
    assert result == batch['name']
    assert saved['waybill_no'] == '3080665836'
    assert saved['status'] == 'Calculated'
    assert saved['confirm_status'] == 'Pending'
    assert provenance == {
        'actor': 'archive-sync',
        'kind': 'comment-derived',
        'source_id': parsed['id'],
        'source_snapshot': parsed['snapshot'],
        'synced_at': provenance['synced_at'],
        'value': '3080665836',
    }
    audits = s.find('audit', binding_id=parsed['id'], action='batch_waybill_synced')
    assert len(audits) == 1
    assert audits[0]['batch'] == batch['name']
    assert audits[0]['new_value'] == '3080665836'

    with s.atomic():
        runtime.ensure_batch(s, parsed)
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 1


def test_comment_waybill_preserves_manual_value_and_audits_conflict(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    ledger.put('batch', batch['name'], {'waybill_no': 'MANUAL-88888888'})
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == 'MANUAL-88888888'
    assert 'waybill_source' not in json.loads(saved['extra_json'])
    conflicts = s.find('audit', binding_id=parsed['id'], action='batch_waybill_conflict')
    assert len(conflicts) == 1
    assert conflicts[0]['current_value'] == 'MANUAL-88888888'
    assert conflicts[0]['candidate_values'] == ['3080665836']

    with s.atomic():
        runtime.ensure_batch(s, parsed)
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_conflict') == 1
    conflict = json.loads(ledger.get('batch', batch['name'])['extra_json'])['waybill_conflict']
    assert conflict['source_snapshot'] == parsed['snapshot']
    assert conflict['current_value'] == 'MANUAL-88888888'
    assert conflict['candidate_values'] == ['3080665836']


def test_comment_waybill_same_source_provenance_allows_later_update(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    with s.atomic():
        runtime.ensure_batch(s, parsed)

    refreshed_raw = source('L-WAYBILL', 'logistics', text='国际快递')
    refreshed_raw['updated_at'] = '2026-09-20T12:00:00+00:00'
    refreshed_raw['raw_payload']['operationRecords'] = [{'remark': 'DHL 9988776655'}]
    refreshed = s.ingest(parse_source(refreshed_raw, logistics_codes={'logistics'}))
    with s.atomic():
        runtime.ensure_batch(s, refreshed)

    saved = ledger.get('batch', batch['name'])
    provenance = json.loads(saved['extra_json'])['waybill_source']
    assert saved['waybill_no'] == '9988776655'
    assert provenance['source_id'] == parsed['id']
    assert provenance['source_snapshot'] == refreshed['snapshot']
    assert provenance['value'] == '9988776655'
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 2


def test_matching_unproven_waybill_never_claims_future_overwrite_rights(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    ledger.put('batch', batch['name'], {'waybill_no': '3080665836'})
    attach_runtime(monkeypatch, s, ledger)
    with s.atomic():
        runtime.ensure_batch(s, parsed)

    assert 'waybill_source' not in json.loads(ledger.get('batch', batch['name'])['extra_json'])

    refreshed_raw = source('L-WAYBILL', 'logistics', text='国际快递')
    refreshed_raw['updated_at'] = '2026-09-20T12:00:00+00:00'
    refreshed_raw['raw_payload']['operationRecords'] = [{'remark': 'DHL 9988776655'}]
    refreshed = s.ingest(parse_source(refreshed_raw, logistics_codes={'logistics'}))
    with s.atomic():
        runtime.ensure_batch(s, refreshed)

    assert ledger.get('batch', batch['name'])['waybill_no'] == '3080665836'
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 0
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_conflict') == 1


def test_ambiguous_comment_waybills_do_not_change_batch(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context(
        'DHL 3080665836\nDHL 9988776655\nETA 2026-9-18已签收'
    )
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == ''
    assert json.loads(saved['extra_json']) == {}
    assert s.count('audit') == 0


@pytest.mark.parametrize('comment', [
    'DHL 12345678',
    'DHL 2026091214550001731',
    'DHL 20260912145500017316',
    'DHL 202609121455000173161',
    'DHL 2,385.37 RMB',
    'DHL 23853700 RMB',
    'DHL 2026-09-18',
    '备注 DHL 3080665836',
    'DHL 3080665836XYZ',
    'DHL 3080665836，金额：RMB 2,385.37',
    'DHL 3080665836 (approval ID: 20260912145500017316)',
    'DHL 3080665836；pesos 100',
    'DHL 3080665836、比索 100',
])
def test_comment_waybill_sync_rejects_unanchored_or_non_tracking_values(monkeypatch, comment):
    s, ledger, batch, parsed = waybill_runtime_context(comment)
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == ''
    assert json.loads(saved['extra_json']) == {}
    assert s.count('audit') == 0


@pytest.mark.parametrize('comment', [
    'DHL 3080665836',
    'FedEx 123456789012',
    'FedEx 123456789012345',
    'UPS 1Z999AA10123456784',
])
def test_comment_waybill_sync_accepts_only_real_carrier_shapes(monkeypatch, comment):
    s, ledger, batch, parsed = waybill_runtime_context(comment)
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == comment.split(' ', 1)[1]


def test_comment_waybill_sync_ignores_operator_name_ids_and_timestamps(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context('ETA 2026-9-18已签收')
    parsed['raw']['operationRecords'] = [{
        'operatorName': 'DHL 3080665836',
        'operatorId': '0217304551217188371',
        'time': '2026-09-19 14:08',
        'remark': 'ETA 2026-9-18已签收',
    }]
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == ''
    assert json.loads(saved['extra_json']) == {}
    assert s.count('audit') == 0


def test_comment_waybill_sync_accepts_running_logistics_source(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    parsed.update(status='RUNNING', approved=False, invalid=False)
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == '3080665836'
    assert json.loads(saved['extra_json'])['waybill_source']['source_id'] == parsed['id']
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 1


def test_comment_waybill_backfill_defaults_to_read_only_preview(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context('DHL 4104020185\nETA 2026-9-17\n已签收')
    parsed.update(status='RUNNING', approved=False, invalid=False)
    attach_runtime(monkeypatch, s, ledger)

    result = runtime.backfill_comment_waybills()

    assert result['ok'] is True
    assert result['dry_run'] is True
    assert result['summary'] == {'ready': 1}
    assert result['rows'] == [{
        'batch_name': batch['name'],
        'source_id': parsed['id'],
        'source_snapshot': parsed['snapshot'],
        'status': 'ready',
        'current_value': '',
        'candidates': ['4104020185'],
    }]
    assert result['plan_hash']
    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0


def test_comment_waybill_backfill_applies_exact_preview_once(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context('DHL 410 402 0185')
    parsed.update(status='RUNNING', approved=False, invalid=False)
    attach_runtime(monkeypatch, s, ledger)
    preview = runtime.backfill_comment_waybills()

    result = runtime.backfill_comment_waybills(
        dry_run=False,
        expected_plan_hash=preview['plan_hash'],
        actor='release-test',
    )

    assert result['dry_run'] is False
    assert result['summary'] == {'updated': 1}
    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == '4104020185'
    assert saved['status'] == 'Calculated'
    assert saved['confirm_status'] == 'Pending'
    provenance = json.loads(saved['extra_json'])['waybill_source']
    assert provenance['actor'] == 'release-test'
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 1

    repeated = runtime.backfill_comment_waybills(dry_run=False)
    assert repeated['summary'] == {'already_set': 1}
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 1


def test_comment_waybill_backfill_rejects_changed_preview(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context('DHL 4104020185')
    parsed.update(status='RUNNING', approved=False, invalid=False)
    attach_runtime(monkeypatch, s, ledger)

    with pytest.raises(ValueError, match='回填清单已变化'):
        runtime.backfill_comment_waybills(dry_run=False, expected_plan_hash='stale')

    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0


@pytest.mark.parametrize(('confirm_status', 'writeback_status', 'expected_status'), [
    ('Confirmed', 'Not Started', 'confirmed'),
    ('Pending', 'Success', 'erp_success'),
])
def test_comment_waybill_backfill_skips_finalized_batches(
    monkeypatch, confirm_status, writeback_status, expected_status,
):
    s, ledger, batch, parsed = waybill_runtime_context('DHL 4104020185')
    parsed.update(status='RUNNING', approved=False, invalid=False)
    ledger.put('batch', batch['name'], {
        'confirm_status': confirm_status,
        'writeback_status': writeback_status,
    })
    attach_runtime(monkeypatch, s, ledger)

    result = runtime.backfill_comment_waybills()

    assert result['summary'] == {expected_status: 1}
    assert result['rows'][0]['status'] == expected_status
    assert ledger.get('batch', batch['name'])['waybill_no'] == ''


@pytest.mark.parametrize(('kind', 'status', 'approved', 'invalid'), [
    ('logistics', 'COMPLETED', False, False),
    ('logistics', 'RUNNING', False, True),
    ('expense', 'RUNNING', False, False),
    ('logistics', 'TERMINATED', False, False),
])
def test_comment_waybill_sync_rejects_ineligible_source_state(
    monkeypatch, kind, status, approved, invalid,
):
    s, ledger, batch, parsed = waybill_runtime_context()
    parsed.update(kind=kind, status=status, approved=approved, invalid=invalid)
    attach_runtime(monkeypatch, s, ledger)

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == ''
    assert json.loads(saved['extra_json']) == {}
    assert s.count('audit') == 0


def test_waybill_sync_and_audit_roll_back_together(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    monkeypatch.setattr(s, 'audit', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('audit failed')))

    with pytest.raises(RuntimeError, match='audit failed'):
        with s.atomic():
            runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == ''
    assert json.loads(saved['extra_json']) == {}


def test_frappe_ledger_batch_metadata_patch_preserves_modified_and_edit_lease():
    writes = []

    class Db:
        def set_value(self, doctype, name, values, **kwargs):
            writes.append((doctype, name, values, kwargs))

    ledger = object.__new__(FrappeLedger)
    ledger.frappe = SimpleNamespace(db=Db())
    ledger.fields = lambda kind, values: dict(values)
    ledger.get = lambda kind, name: {
        'name': name,
        'modified': '2026-09-20 17:59:00',
        'edit_lock_owner': 'active-user',
        'edit_lock_expires_at': '2099-01-01 00:00:00',
        **writes[-1][2],
    }

    result = ledger.patch_batch_metadata('BATCH-1', {
        'waybill_no': '3080665836', 'extra_json': '{"waybill_source":{}}',
    })

    assert writes == [(
        'Overseas Cost Batch', 'BATCH-1',
        {'waybill_no': '3080665836', 'extra_json': '{"waybill_source":{}}'},
        {'update_modified': False},
    )]
    assert result['modified'] == '2026-09-20 17:59:00'
    assert result['edit_lock_owner'] == 'active-user'


def test_single_batch_mapping_uses_exact_local_source_without_creating_batch(setup, monkeypatch):
    s,l,b,v,i,r,binding = setup
    attach_runtime(monkeypatch,s,l)
    s.sql('DELETE FROM oc_ls_batch_map')
    l.put('batch',b['name'],{'source_corp_id':'C','source_instance_id':'L'})
    result = runtime.ensure_batch_source(s,b['name'])
    assert result['id'] == binding['logistics_id']
    assert s.find('batch_map',batch=b['name'])[0]['source_id'] == binding['logistics_id']
    assert len(l.rows('batch')) == 1


def test_single_batch_missing_identity_never_starts_global_initialization(setup, monkeypatch):
    s,l,b,*_ = setup; attach_runtime(monkeypatch,s,l)
    s.sql('DELETE FROM oc_ls_batch_map')
    with pytest.raises(ValueError,match='审批实例'):
        runtime.ensure_batch_source(s,b['name'])


def test_single_batch_hydrates_only_exact_approval_and_preserves_existing_costs(setup, monkeypatch):
    from types import SimpleNamespace
    s,l,b,v,i,r,binding = setup; attach_runtime(monkeypatch,s,l)
    s.sql('DELETE FROM oc_ls_batch_map')
    l.put('batch',b['name'],{'source_corp_id':'C','source_instance_id':'new-L'})
    calls=[]
    monkeypatch.setattr(runtime,'archive',lambda:SimpleNamespace(get_sources=lambda pairs:calls.append(pairs) or [source('new-L','logistics')]))
    monkeypatch.setattr(runtime,'logistics_codes',lambda:{'logistics'})
    monkeypatch.setattr(runtime,'prepare_source',lambda raw:raw)
    result=runtime.ensure_batch_source(s,b['name'])
    assert calls == [[('C','new-L')]] and result['instance'] == 'new-L'
    assert l.get('item',i['name'])['quantity'] == 2 and l.get('rule',r['name'])['amount'] == 200


def test_normal_batch_query_uses_only_local_persisted_data(setup, monkeypatch):
    s,l,b,v,i,r,binding = setup
    attach_runtime(monkeypatch,s,l)
    result=runtime.batch_status(b['name'])
    assert result['expense']['amount']=='100'
    assert result['binding']['application_status']=='pending'
    assert result['expense']['open_url'].startswith('dingtalk://')


def test_invalid_or_changed_source_blocks_current_result_without_restoring_estimates(setup, monkeypatch):
    s,l,b,v,i,r,binding=setup
    attach_runtime(monkeypatch,s,l)
    apply_binding(s,l,binding['id'],'u')
    row=source('E',amount='0');row.update(status='COMPLETED',result='refuse')
    ingest(s,row)
    assert runtime.calculation_blockers(b['name'],v['name'])
    assert l.get('rule',r['name'])['is_enabled']==0


def test_confirmed_final_version_cannot_be_recalculated(setup, monkeypatch):
    s,l,b,v,i,r,binding=setup
    attach_runtime(monkeypatch,s,l)
    apply_binding(s,l,binding['id'],'u')
    l.put('version',v['name'],{'status':'Confirmed'})
    assert any('已确认' in reason for reason in runtime.calculation_blockers(b['name'],v['name'],for_calculation=True))


def test_final_rule_and_quantity_cannot_diverge_from_adopted_source(setup, monkeypatch):
    s,l,b,v,i,r,binding=setup
    attach_runtime(monkeypatch,s,l)
    apply_binding(s,l,binding['id'],'u')
    final=next(rule for rule in l.rows('rule') if rule.get('is_final'))
    l.put('rule',final['name'],{'amount':999})
    assert any('费用' in reason and '不一致' in reason for reason in runtime.calculation_blockers(b['name'],v['name']))
    l.put('rule',final['name'],{'amount':'100'})
    import json
    meta=json.loads(l.get('item',i['name'])['extra_json']);meta['settlement_cargo']['quantity']='999'
    l.put('item',i['name'],{'extra_json':json.dumps(meta)})
    assert any('数量' in reason and '不一致' in reason for reason in runtime.calculation_blockers(b['name'],v['name']))


def test_historical_version_shows_its_adopted_expense_snapshot(setup, monkeypatch):
    from copy import deepcopy
    s,l,b,v,i,r,binding=setup
    attach_runtime(monkeypatch,s,l)
    apply_binding(s,l,binding['id'],'u')
    l.put('version',v['name'],{'status':'Confirmed'})
    original=s.get('source',binding['expense_id'])
    row=source('E',amount='120');row['updated_at']='2026-09-09T04:00:00+00:00'
    ingest(s,row);apply_binding(s,l,binding['id'],'u')
    old=runtime.batch_status(b['name'],v['name'])
    current=runtime.batch_status(b['name'])
    assert old['historical'] and old['expense']['amount']=='100'
    assert old['binding']['application_status']=='historical' and not old['item_reviews']
    assert not current['historical'] and current['expense']['amount']=='120'


def test_scheduler_cannot_undo_stop_while_upstream_health_is_in_flight(setup,monkeypatch):
    from types import SimpleNamespace
    from overseas_costing.services.logistics_settlement.model import dumps
    s,l,*_=setup;attach_runtime(monkeypatch,s,l)
    s.put('state',{'id':'control','updated_at':'t','data':dumps({'enabled':True})})
    class Archive:
        def health(self):
            s.put('state',{'id':'control','updated_at':'later','data':dumps({'enabled':False})})
            return {}
    monkeypatch.setattr(runtime,'archive',lambda:Archive())
    monkeypatch.setattr(runtime,'frappe',SimpleNamespace(session=SimpleNamespace(user='u')))
    monkeypatch.setattr(runtime,'enqueue',lambda _:pytest.fail('stopped scheduler enqueued work'))
    assert runtime.scheduled_sync()['skipped']
    assert not s.get('state','control')['enabled'] and s.count('job')==0


def test_pending_worker_cannot_reapply_a_rollback_from_stale_inventory(setup,monkeypatch):
    from overseas_costing.services.logistics_settlement.rollback import restore_application
    from overseas_costing.services.logistics_settlement.model import dumps
    s,l,b,v,i,r,binding=setup;attach_runtime(monkeypatch,s,l)
    applied=apply_binding(s,l,binding['id'],'u')
    s.put('state',{'id':'control','updated_at':'t','data':dumps({'enabled':True})})
    original_find=s.find; rolled_back=[]
    def interleave(table,**filters):
        rows=original_find(table,**filters)
        if table=='binding' and filters.get('limit')==50 and not rolled_back:
            rolled_back.append(True)
            restore_application(s,l,applied['last_application'],applied['revision'],'worker inventory race','u')
        return rows
    monkeypatch.setattr(s,'find',interleave)
    runtime.resume_pending()
    assert rolled_back and not s.get('state','control')['enabled']
    assert s.get('binding',binding['id'])['application_status']=='rolled_back'
    assert str(l.get('item',i['name'])['quantity'])=='2'


def test_queued_document_resume_rechecks_stop_after_inventory(setup,monkeypatch):
    from overseas_costing.services.logistics_settlement.model import dumps
    s,l,b,v,i,r,binding=setup;attach_runtime(monkeypatch,s,l)
    s.put('state',{'id':'control','updated_at':'t','data':dumps({'enabled':True})})
    s.put('document_sync',{'id':'queue','source_id':binding['logistics_id'],'batch':b['name'],'status':'queued','data':'{}'})
    original_find=s.find
    def interleave(table,**filters):
        rows=original_find(table,**filters)
        if table=='document_sync' and filters.get('status')=='queued':
            s.put('state',{'id':'control','updated_at':'stop','data':dumps({'enabled':False})})
        return rows
    monkeypatch.setattr(s,'find',interleave)
    monkeypatch.setattr(runtime,'apply_source',lambda _:pytest.fail('stopped document queue applied work'))
    runtime.resume_pending()
    assert s.get('document_sync','queue')['status']=='queued'


def attach_load_job(monkeypatch, store, count):
    from types import SimpleNamespace
    from overseas_costing.services.logistics_settlement.jobs import start_job, run_step
    rows = [source(f'worker-{index}') for index in range(count)]
    class Archive:
        def page(self, *, cursor, limit, **kwargs):
            offset = int(cursor or 0)
            page = rows[offset:offset + limit]
            return {'items': page, 'next_cursor': offset + len(page),
                    'has_more': offset + len(page) < len(rows)}
    upstream = Archive()
    job = start_job(store, mode='incremental', actor='tester')
    while job['phase'] == 'inventory':
        job = run_step(store, upstream, job['id'], logistics_codes={'logistics'})
    store.commit()
    monkeypatch.setattr(runtime, 'store', lambda: store)
    monkeypatch.setattr(runtime, 'archive', lambda: upstream)
    monkeypatch.setattr(runtime, 'logistics_codes', lambda: {'logistics'})
    monkeypatch.setattr(runtime, 'freight_enabled', lambda: False)
    monkeypatch.setattr(runtime, 'apply_source', None)
    monkeypatch.setattr(runtime, 'frappe', SimpleNamespace(db=store.db, log_error=lambda **kwargs: None))
    return job


def test_worker_commits_slow_source_and_requeues_before_next_reader(store, monkeypatch):
    job = attach_load_job(monkeypatch, store, 205)
    elapsed, prepared, queued, checkpoints = [0], [], [], []
    monkeypatch.setattr(runtime, 'monotonic', lambda: elapsed[0], raising=False)
    monkeypatch.setattr(runtime, 'RUN_JOB_BUDGET_SECONDS', 60, raising=False)
    original_commit = store.commit
    def commit():
        original_commit()
        checkpoints.append(store.count('job_item', job_id=job['id'], status='loaded'))
    monkeypatch.setattr(store, 'commit', commit)
    def prepare(raw):
        prepared.append(raw['process_instance_id'])
        elapsed[0] += 61  # A slow download/parse, without sleeping in the test.
        return raw
    monkeypatch.setattr(runtime, 'prepare_source', prepare)
    def enqueue(job_id):
        assert checkpoints[-1] == len(prepared)
        queued.append(job_id)
    monkeypatch.setattr(runtime, 'enqueue', enqueue)
    runtime.run_job(job['id'])
    assert len(prepared) == 1 and checkpoints[0] == 1
    assert store.count('job_item', job_id=job['id'], status='pending') == 204
    assert queued == [job['id']]
    runtime.run_job(job['id'])
    assert len(prepared) == len(set(prepared)) == 2
    assert store.count('job_item', job_id=job['id'], status='pending') == 203
    assert queued == [job['id'], job['id']]


def test_worker_fast_load_is_step_bounded_and_continues_to_completion(store, monkeypatch):
    job = attach_load_job(monkeypatch, store, 5)
    prepared, queued = [], []
    monkeypatch.setattr(runtime, 'monotonic', lambda: 0, raising=False)
    monkeypatch.setattr(runtime, 'RUN_JOB_MAX_STEPS', 3, raising=False)
    monkeypatch.setattr(runtime, 'prepare_source', lambda raw: prepared.append(raw['process_instance_id']) or raw)
    monkeypatch.setattr(runtime, 'enqueue', queued.append)
    runtime.run_job(job['id'])
    assert len(prepared) == 3 and queued == [job['id']]
    assert store.count('job_item', job_id=job['id'], status='pending') == 2
    runtime.run_job(job['id'])
    completed = store.get('job', job['id'])
    assert completed['status'] == 'completed' and completed['processed_count'] == 5
    assert len(prepared) == len(set(prepared)) == 5
    assert queued == [job['id']]


def with_structured_waybill(store, fields, *, comment='DHL 3080665836'):
    raw = source('L-WAYBILL', 'logistics', text='国际快递')
    raw['updated_at'] = '2026-09-21T12:00:00+00:00'
    raw['raw_payload']['operationRecords'] = [{'remark': comment}]
    raw['raw_payload']['formComponentValues'].extend(
        {'name': name, 'value': value} for name, value in fields.items()
    )
    return store.ingest(parse_source(raw, logistics_codes={'logistics'}))


def test_existing_mapped_batch_uses_structured_waybill_before_comment(monkeypatch):
    s, ledger, batch, _ = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    parsed = with_structured_waybill(s, {'柜号/单号Número DE Logística': '4104020185'})
    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == '4104020185'
    proof = json.loads(saved['extra_json'])['waybill_source']
    assert proof['kind'] == 'form-derived'
    assert proof['source_fields'] == ['柜号/单号Número DE Logística']
    assert saved['status'] == 'Calculated'
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_synced') == 1


@pytest.mark.parametrize(('fields', 'reason'), [
    ({'运单号': '4104020185', '物流单号': '9988776655'}, 'ambiguous'),
    ({'运单号': 'DHL 2,385.37 RMB'}, 'invalid_form'),
])
def test_structured_waybill_conflicts_or_invalid_values_do_not_fall_back(monkeypatch, fields, reason):
    s, ledger, batch, _ = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    parsed = with_structured_waybill(s, fields)
    preview = runtime.backfill_comment_waybills(batch_names=[batch['name']])
    assert preview['summary'] == {reason: 1}

    with s.atomic():
        runtime.ensure_batch(s, parsed)
    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0


@pytest.mark.parametrize('finalized', [{'confirm_status': 'Confirmed'}, {'writeback_status': 'Success'}])
def test_mapped_waybill_sync_never_changes_finalized_batch(monkeypatch, finalized):
    s, ledger, batch, _ = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    ledger.put('batch', batch['name'], finalized)
    parsed = with_structured_waybill(s, {'Número de guía': '4104020185'})

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == '' and json.loads(saved['extra_json']) == {}
    assert s.count('audit') == 0


def test_targeted_waybill_preview_queries_requested_mapping_before_limit(monkeypatch):
    s, ledger, batch, parsed = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    s.insert('batch_map', {'id': '000-other', 'source_id': '000-other', 'batch': 'OTHER', 'data': '{}'})

    result = runtime.backfill_comment_waybills(batch_names=[batch['name'], 'UNMAPPED'], limit=1)

    assert {row['batch_name']: row['status'] for row in result['rows']} == {
        batch['name']: 'ready', 'UNMAPPED': 'mapping_missing',
    }
    assert next(row for row in result['rows'] if row['status'] == 'ready')['source_id'] == parsed['id']
    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0


@pytest.mark.parametrize('changed', ['source_snapshot', 'finalized_batch'])
def test_waybill_backfill_rechecks_locked_source_and_batch_before_apply(monkeypatch, changed):
    s, ledger, batch, parsed = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    first = runtime.backfill_comment_waybills(batch_names=[batch['name']])
    original_source_get = s.get
    original_batch_get = ledger.get

    def source_get(table, name, lock=False):
        stored = original_source_get(table, name, lock=lock)
        if changed == 'source_snapshot' and table == 'source' and name == parsed['id'] and lock:
            return {**stored, 'snapshot': 'new-snapshot', 'raw': {'comments': ['DHL 4104020185']}}
        return stored

    def batch_get(kind, name, lock=False):
        stored = original_batch_get(kind, name, lock=lock)
        if changed == 'finalized_batch' and kind == 'batch' and name == batch['name'] and lock:
            return {**stored, 'confirm_status': 'Confirmed'}
        return stored

    monkeypatch.setattr(s, 'get', source_get)
    monkeypatch.setattr(ledger, 'get', batch_get)

    result = runtime.backfill_comment_waybills(batch_names=[batch['name']], dry_run=False,
                                              expected_plan_hash=first['plan_hash'])

    assert result['summary'] == {'changed': 1}
    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0


def test_duplicate_structured_control_names_do_not_hide_waybill_conflicts(monkeypatch):
    s, ledger, batch, _ = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    raw = source('L-WAYBILL', 'logistics', text='国际快递')
    raw['updated_at'] = '2026-09-21T12:00:00+00:00'
    raw['raw_payload']['formComponentValues'].extend([
        {'name': '运单号', 'value': '4104020185'},
        {'name': '运单号', 'value': '9988776655'},
    ])
    parsed = s.ingest(parse_source(raw, logistics_codes={'logistics'}))

    with s.atomic():
        runtime.ensure_batch(s, parsed)

    assert ledger.get('batch', batch['name'])['waybill_no'] == ''
    assert s.count('audit') == 0
    preview = runtime.backfill_comment_waybills(batch_names=[batch['name']])
    assert preview['summary'] == {'ambiguous': 1}
    assert preview['rows'][0]['candidates'] == ['4104020185', '9988776655']


def test_existing_form_derived_waybill_is_not_replaced_by_a_later_comment(monkeypatch):
    s, ledger, batch, _ = waybill_runtime_context()
    attach_runtime(monkeypatch, s, ledger)
    parsed = with_structured_waybill(s, {'运单号': '4104020185'})
    with s.atomic():
        runtime.ensure_batch(s, parsed)

    raw = source('L-WAYBILL', 'logistics', text='国际快递')
    raw['updated_at'] = '2026-09-22T12:00:00+00:00'
    raw['raw_payload']['operationRecords'] = [{'remark': 'DHL 9988776655'}]
    refreshed = s.ingest(parse_source(raw, logistics_codes={'logistics'}))
    with s.atomic():
        runtime.ensure_batch(s, refreshed)

    saved = ledger.get('batch', batch['name'])
    assert saved['waybill_no'] == '4104020185'
    assert json.loads(saved['extra_json'])['waybill_source']['kind'] == 'form-derived'
    assert s.count('audit', binding_id=parsed['id'], action='batch_waybill_conflict') == 1
