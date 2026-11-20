"""Exact linked-approval hydration uses native parsing and real archive storage."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from overseas_costing.services import material_ai_linked_sources as service
from overseas_costing.services import packing_source_service
from overseas_costing.services.logistics_settlement.documents import enrich_raw
from overseas_costing.services.logistics_settlement.model import dumps, parse_source
from overseas_costing.tests.test_logistics_settlement import store, source, ingest
from overseas_costing.tests.test_settlement_documents import workbook
from overseas_costing.tests.test_settlement_writer import Ledger


@pytest.fixture
def linked(store):
    ledger = Ledger(store)
    batch = ledger.create('batch', {'source_type': 'oa_logistics', 'source_instance_id': 'L'})
    version = ledger.create('version', {'batch': batch['name'], 'status': 'Active'})
    ledger.put('batch', batch['name'], {'current_version': version['name']})
    root = source('L', 'logistics')
    root['raw_payload']['formComponentValues'].append({
        'name': '关联审批单', 'componentType': 'RelateField', 'value': ['采购支出'],
        'extValue': [{'procInstId': 'BUY', 'businessId': 'PO-1'}],
    })
    parsed = ingest(store, root)
    store.insert('batch_map', {'id': parsed['id'], 'source_id': parsed['id'], 'batch': batch['name'], 'data': '{}'})
    return store, ledger, batch['name'], version['name'], parsed


def hydrate(context, rows, *, prepare=None, seen=None, metadata=(), metadata_seen=None):
    store, ledger, batch, version, _ = context

    def get_sources(pairs):
        if seen is not None:
            seen.append(pairs)
        return deepcopy(rows)

    def get_reference_metadata(pairs):
        if metadata_seen is not None:
            metadata_seen.append(pairs)
        return deepcopy(metadata)

    return service.hydrate_linked_sources(batch, version, store=store, ledger=ledger,
        archive=SimpleNamespace(get_sources=get_sources, get_reference_metadata=get_reference_metadata),
        prepare=prepare or (lambda raw, **kwargs: raw),
        logistics_codes={'logistics'})


def test_exact_linked_purchase_is_cached_without_financial_or_unrelated_writes(linked):
    store, ledger, batch, version, root = linked
    raw = source('BUY', kind='purchase')
    raw['archive_revision'] = 'REV-1'
    raw['raw_payload']['formComponentValues'].append({
        'name': '关联审批单', 'componentType': 'RelateField', 'value': ['采购支出'],
        'extValue': [{'procInstId': 'NESTED', 'businessId': 'PO-2'}]})
    data = workbook('完整货物明细', [['物料编码', '数量', '单位', '单价'], ['A', 2, '件', 10]])
    raw['attachments'] = [{'file_id': 'F1', 'file_name': 'purchase.xlsx', 'archive_status': 'archived',
                           'sha256': 'HASH', 'bucket': 'B', 'object_key': 'K', 'content_quality': 'original'}]
    reads, requests = [], []

    def prepare(row, **kwargs):
        assert kwargs == {'include_unclassified': True}
        return enrich_raw(store, row, reader=lambda manifest: (reads.append(manifest['file_id']) or data),
                          cache_file=lambda *args: '/private/files/purchase.xlsx')

    before = ledger.rows('batch') + ledger.rows('version')
    result = hydrate(linked, [raw, source('UNRELATED', kind='purchase'), source('BUY', kind='purchase', corp='FOREIGN')],
                     prepare=prepare, seen=requests)
    assert result == {'status': 'COMPLETE', 'linked_count': 1, 'hydrated_count': 1, 'blockers': []}
    assert requests == [[('C', 'BUY')]] and reads == ['F1']
    purchase = store.find('source', corp='C', instance='BUY')[0]
    assert purchase['kind'] == 'unclassified' and purchase['documents'][0]['tables']
    assert len(store.find('source')) == 2 and not store.find('source', instance='NESTED')
    assert store.count('snapshot') == 2 and store.count('document') == 1
    for table in ('candidate', 'binding', 'application', 'freight_claim', 'payment_claim', 'packing_review'):
        assert store.count(table) == 0
    assert len(store.find('batch_map')) == 1 and before == ledger.rows('batch') + ledger.rows('version')
    assert not ledger.rows('item') and not ledger.rows('rule') and not ledger.rows('attachment')
    again = hydrate(linked, [raw], prepare=lambda *args, **kwargs: pytest.fail('cached'), seen=requests)
    assert again['status'] == 'COMPLETE' and again['hydrated_count'] == 0 and len(requests) == 2
    detail = packing_source_service.related_approval_detail(batch, version, store=store, ledger=ledger)
    assert detail['linked_purchase_approvals'][0]['instance_id'] == 'BUY'
    assert detail['linked_purchase_approvals'][0]['attachments'][0]['archive_status'] == 'archived'


def test_missing_upstream_never_resolves_same_instance_from_another_corp(linked):
    result = hydrate(linked, [source('BUY', kind='purchase', corp='FOREIGN')])
    assert result['status'] == 'PARTIAL' and result['hydrated_count'] == 0
    assert result['blockers'][0]['code'] == 'LINKED_APPROVAL_ARCHIVE_MISSING'
    assert linked[0].count('source') == 1


def test_pending_attachment_stays_explicit_and_retries_after_upstream_archive_arrives(linked):
    store = linked[0]
    raw = source('BUY', kind='purchase')
    raw['attachments'] = [{'file_id': 'F1', 'file_name': 'purchase.xlsx', 'archive_status': 'pending'}]
    data = workbook('采购明细', [['物料编码', '数量', '单位'], ['A', 2, '件']])

    def prepare(row, **kwargs):
        return enrich_raw(store, row, reader=lambda manifest: data,
                          cache_file=lambda *args: '/private/files/purchase.xlsx')

    pending = hydrate(linked, [raw], prepare=prepare)
    assert pending['status'] == 'PARTIAL'
    assert pending['blockers'][0]['code'] == 'LINKED_ATTACHMENT_ARCHIVE_PENDING'
    assert pending['blockers'][0]['file_name'] == 'purchase.xlsx' and store.count('document') == 0
    raw['attachments'][0].update(archive_status='archived', sha256='H', bucket='B', object_key='K')
    assert hydrate(linked, [raw], prepare=prepare)['status'] == 'COMPLETE'
    assert store.count('document') == 1 and store.count('snapshot') == 3


@pytest.mark.parametrize('changed', ['snapshot', 'version', 'mapping', 'corp', 'batch_corp'])
def test_download_runs_without_write_lock_then_current_source_is_rechecked(linked, changed):
    store, ledger, batch, version, root = linked
    locked_reads = []
    native_get = store.get

    def get(table, name, lock=False):
        if lock:
            locked_reads.append(table)
        return native_get(table, name, lock=lock)

    store.get = get
    native_ledger_get = ledger.get

    def ledger_get(kind, name, lock=False):
        if lock:
            locked_reads.append(kind)
        return native_ledger_get(kind, name, lock=lock)

    ledger.get = ledger_get

    def prepare(row, **kwargs):
        assert not locked_reads
        if changed == 'version':
            ledger.put('batch', batch, {'current_version': 'NEW'})
        elif changed == 'batch_corp':
            ledger.put('batch', batch, {'source_corp_id': 'FOREIGN'})
        elif changed == 'mapping':
            store.put('batch_map', {'id': root['id'], 'source_id': 'CHANGED'})
        else:
            store.put('source', {'id': root['id'], changed: 'CHANGED'})
        return row

    with pytest.raises(ValueError, match='source changed'):
        hydrate(linked, [source('BUY', kind='purchase')], prepare=prepare)
    assert locked_reads[:4] == ['state', 'source', 'batch_map', 'batch']
    assert store.count('source') == 1 and not store.find('source', instance='BUY')


def test_attachment_preparation_failure_keeps_body_readable_and_reports_archive_problem(linked):
    def prepare(*args, **kwargs):
        raise OSError('/server/private/location?secret=do-not-expose')

    result = hydrate(linked, [source('BUY', kind='purchase')], prepare=prepare)
    assert result['status'] == 'PARTIAL'
    assert result['blockers'][0]['code'] == 'LINKED_ATTACHMENT_PREPARATION_FAILED'
    assert 'secret' not in repr(result)
    assert linked[0].find('source', corp='C', instance='BUY')[0]['kind'] == 'unclassified'


def test_archive_read_failure_is_coverage_not_a_request_to_upload_documents(linked):
    store, ledger, batch, version, _ = linked
    archive = SimpleNamespace(get_sources=lambda pairs: (_ for _ in ()).throw(OSError('private connection')))
    result = service.hydrate_linked_sources(batch, version, store=store, ledger=ledger,
        archive=archive, logistics_codes={'logistics'})
    assert result['status'] == 'PARTIAL' and result['hydrated_count'] == 0
    assert result['blockers'][0]['code'] == 'LINKED_APPROVAL_ARCHIVE_UNAVAILABLE'


def test_frontend_related_detail_keeps_local_only_behavior_when_link_is_missing(linked):
    store, ledger, batch, version, _ = linked
    before = store.db.total_changes
    detail = packing_source_service.related_approval_detail(batch, version, store=store, ledger=ledger)
    assert detail['local_only'] and detail['linked_purchase_approvals'] == []
    assert store.db.total_changes == before


def test_native_source_preparation_keeps_unclassified_default_and_explicit_opt_in(linked, monkeypatch):
    from overseas_costing.integrations.dingtalk_approval_source import MinioArchiveClient
    from overseas_costing.services import import_service
    from overseas_costing.services.logistics_settlement import runtime

    store = linked[0]
    raw = source('BUY', kind='purchase')
    raw['attachments'] = [{'file_id': 'F', 'file_name': 'purchase.xlsx', 'archive_status': 'archived',
                           'bucket': 'B', 'object_key': 'K', 'sha256': 'H'}]
    data = workbook('采购明细', [['物料编码', '数量'], ['A', 2]])
    downloads = []
    created = []

    def get_doc(values):
        created.append(values)
        assert 'file_url' not in values and values['content'] == data
        doc = SimpleNamespace(file_url='/private/files/purchase.xlsx')
        doc.insert = lambda **kwargs: (created.append(kwargs) or doc)
        return doc

    monkeypatch.setattr(runtime, 'frappe', SimpleNamespace(get_doc=get_doc))
    monkeypatch.setattr(import_service, '_get_minio_archive_client', lambda: SimpleNamespace(client=object()))
    monkeypatch.setattr(MinioArchiveClient, 'download', lambda client, manifest: (
        downloads.append((client.bucket, manifest['object_key'])) or (data, {})))
    monkeypatch.setattr(runtime.Store, 'frappe', lambda: store)
    monkeypatch.setattr(runtime, 'logistics_codes', lambda: {'logistics'})

    assert runtime.prepare_source(raw) == raw and not downloads
    prepared = runtime.prepare_source(raw, include_unclassified=True)
    assert downloads == [('B', 'K')] and prepared['settlement_documents'][0]['file_url']
    assert created == [{'doctype': 'File', 'file_name': 'purchase.xlsx', 'content': data, 'is_private': 1},
                       {'ignore_permissions': True}]
    assert parse_source(prepared, logistics_codes={'logistics'})['kind'] == 'unclassified'


def test_existing_parsed_document_without_local_file_can_recover_without_reparsing(linked, monkeypatch):
    from overseas_costing.services.logistics_settlement import documents

    store = linked[0]
    raw = source('BUY', kind='purchase')
    raw['attachments'] = [{'file_id': 'F', 'file_name': 'purchase.xlsx', 'archive_status': 'archived',
                           'bucket': 'B', 'object_key': 'K', 'sha256': 'H'}]
    data = workbook('采购明细', [['物料编码', '数量'], ['A', 2]])

    class MaxFileSizeReachedError(Exception):
        pass

    def too_large(*args):
        raise MaxFileSizeReachedError()

    first = hydrate(linked, [raw], prepare=lambda row, **kwargs: enrich_raw(
        store, row, reader=lambda manifest: data, cache_file=too_large))
    assert first['status'] == 'PARTIAL' and first['blockers'][0]['code'] == 'LINKED_ATTACHMENT_CACHE_PENDING'
    cached = store.find('document')[0]
    assert cached['tables'] and not cached.get('file_url') and cached['cache_issues']
    monkeypatch.setattr(documents, 'parse_document', lambda *args: pytest.fail('Immutable parse should be reused'))
    requests = []
    recovered = hydrate(linked, [raw], seen=requests, prepare=lambda row, **kwargs: enrich_raw(
        store, row, reader=lambda manifest: data, cache_file=lambda *args: '/private/files/purchase.xlsx'))
    assert recovered['status'] == 'COMPLETE' and requests == [[('C', 'BUY')]]
    assert store.count('document') == 1 and store.count('snapshot') == 3
    saved = store.find('document')[0]
    assert saved['file_url'] and saved['tables'] == cached['tables']
    assert not saved.get('cache_issues') and not saved['issues']


def test_complete_local_purchase_refreshes_changed_body_and_new_attachment_revision(linked):
    store = linked[0]
    raw = source('BUY', kind='purchase')
    raw['archive_revision'] = 'R1'
    assert hydrate(linked, [raw])['status'] == 'COMPLETE'
    old = store.find('source', corp='C', instance='BUY')[0]
    raw['archive_revision'] = 'R2'
    raw['raw_payload']['formComponentValues'].append({'name': '采购金额', 'value': '120'})
    raw['attachments'] = [{'file_id': 'F2', 'file_name': 'invoice.csv', 'archive_status': 'archived',
                           'bucket': 'B', 'object_key': 'K2', 'sha256': 'H2'}]
    result = hydrate(linked, [raw], prepare=lambda row, **kwargs: enrich_raw(
        store, row, reader=lambda manifest: b'quantity,amount\n2,120',
        cache_file=lambda *args: '/private/files/invoice.csv'))
    current = store.find('source', corp='C', instance='BUY')[0]
    assert result['status'] == 'COMPLETE' and result['hydrated_count'] == 1
    assert current['snapshot'] != old['snapshot'] and current['archive_revision'] == 'R2'
    assert current['fields']['采购金额'] == 120 and current['documents'][0]['file_id'] == 'F2'
