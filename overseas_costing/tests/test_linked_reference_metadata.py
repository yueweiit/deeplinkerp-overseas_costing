"""Public metadata distinguishes non-cost references without importing OA bodies."""

from types import SimpleNamespace

import pytest

from overseas_costing.integrations.logistics_settlement_source import SettlementArchive
from overseas_costing.services import material_ai_linked_sources as service
from overseas_costing.tests.test_material_ai_linked_sources import hydrate, linked
from overseas_costing.tests.test_logistics_settlement import ingest, source, store
from overseas_costing.tests.test_settlement_archive import ArchiveSource, ArchiveCursor


MOLD_CODE = 'PROC-00000000-0000-4000-8000-000000000001'
MOLD_TEMPLATE = 'LEMOS-开模申请 Fabricación de moldes'
MOLD_REFERENCES = [
    'TEST_MOLD_REFERENCE_0100000000000001',
    'TEST_MOLD_REFERENCE_0200000000000001',
    'TEST_MOLD_REFERENCE_0300000000000001',
    'TEST_MOLD_REFERENCE_0400000000000001',
    'TEST_MOLD_REFERENCE_0500000000000001',
]


def metadata(instance='BUY', **changes):
    return {'corp_id': 'C', 'process_instance_id': instance, 'process_code': MOLD_CODE,
            'template_name': MOLD_TEMPLATE, 'title': MOLD_TEMPLATE, 'status': 'RUNNING',
            'result': 'agree', 'deleted_at': None, 'registered_financial_flow': False, **changes}


def test_five_real_mold_reference_types_are_information_not_missing_cost_sources(linked):
    root = source('L', 'logistics')
    root['raw_payload']['formComponentValues'].append({
        'name': '关联审批单', 'componentType': 'RelateField', 'value': [MOLD_TEMPLATE],
        'extValue': [{'procInstId': instance} for instance in MOLD_REFERENCES]})
    ingest(linked[0], root)
    before = linked[0].db.total_changes
    requests = []
    result = hydrate(linked, [], metadata=[metadata(instance) for instance in MOLD_REFERENCES],
                     metadata_seen=requests)
    assert requests == [[('C', instance) for instance in MOLD_REFERENCES]]
    assert result['status'] == 'COMPLETE' and not result['blockers']
    assert result['linked_count'] == result['non_cost_reference_count'] == 5
    assert result['hydrated_count'] == 0
    assert {row['process_instance_id'] for row in result['non_cost_references']} == set(MOLD_REFERENCES)
    assert all(row['process_code'] == MOLD_CODE and row['status'] == 'RUNNING'
               for row in result['non_cost_references'])
    assert linked[0].count('source') == 1 and linked[0].db.total_changes == before


@pytest.mark.parametrize('changes', [
    {'template_name': '采购支出'}, {'template_name': '月结付款'},
    {'template_name': '费用支出'}, {'title': 'BU'},
    {'registered_financial_flow': True}, {'template_name': '未知申请'},
    {'process_code': 'logistics'},
])
def test_financial_or_unverified_metadata_remains_an_explicit_scope_blocker(linked, changes):
    result = hydrate(linked, [], metadata=[metadata(**changes)])
    assert result['status'] == 'PARTIAL' and result['hydrated_count'] == 0
    assert result['blockers'][0]['code'] == 'LINKED_APPROVAL_OUTSIDE_COST_SCOPE'
    assert not result.get('non_cost_references') and linked[0].count('source') == 1


def test_metadata_from_another_corp_or_unrelated_instance_cannot_clear_a_missing_reference(linked):
    result = hydrate(linked, [], metadata=[metadata(corp_id='FOREIGN'), metadata('UNRELATED')])
    assert result['status'] == 'PARTIAL'
    assert result['blockers'][0]['code'] == 'LINKED_APPROVAL_ARCHIVE_MISSING'
    assert not result.get('non_cost_reference_count')


def test_failed_metadata_lookup_remains_unavailable_instead_of_claiming_missing(linked):
    def unavailable(pairs):
        raise OSError('private connection details')

    store, ledger, batch, version, _ = linked
    archive = SimpleNamespace(get_sources=lambda pairs: [], get_reference_metadata=unavailable)
    result = service.hydrate_linked_sources(batch, version, store=store, ledger=ledger,
                                          archive=archive, logistics_codes={'logistics'})
    assert result['status'] == 'PARTIAL'
    assert result['blockers'][0]['code'] == 'LINKED_APPROVAL_ARCHIVE_UNAVAILABLE'
    assert 'private' not in repr(result) and store.count('source') == 1


def test_mold_metadata_does_not_reclassify_or_reimport_an_existing_local_reference(linked):
    local = source('BUY', kind='purchase')
    local['status'] = 'REJECTED'
    prior = ingest(linked[0], local)
    before = linked[0].get('source', prior['id'])
    result = hydrate(linked, [], metadata=[metadata(raw_payload={'Price': 'not a purchase amount'})])
    assert result['status'] == 'COMPLETE' and not result['blockers']
    assert 'raw_payload' not in result['non_cost_references'][0]
    assert linked[0].get('source', prior['id']) == before


def test_complete_cost_archive_sources_do_not_query_public_metadata(linked):
    requests = []
    result = hydrate(linked, [source('BUY', kind='purchase')], metadata_seen=requests)
    assert result['status'] == 'COMPLETE' and not requests


def test_metadata_adapter_uses_one_parameterized_exact_pair_query_without_body_or_attachments():
    class Cursor(ArchiveCursor):
        def execute(self, sql, args):
            self.source.queries.append((sql, args))
            self.rows = [metadata()]

    class Source(ArchiveSource):
        def __init__(self):
            super().__init__(0)
            self.queries = []

        def cursor(self):
            return Cursor(self)

    source = Source()
    archive = SettlementArchive(source, logistics_codes={'logistics'}, financial=True)
    assert archive.get_reference_metadata([]) == [] and not source.queries
    pairs = [('C', 'BUY'), ('C', "unsafe' OR TRUE")]
    assert archive.get_reference_metadata(pairs) == [metadata()]
    assert len(source.queries) == 1
    sql, args = source.queries[0]
    assert args == ('C', 'BUY', 'C', "unsafe' OR TRUE")
    assert sql.count('(i.corp_id=%s AND i.process_instance_id=%s)') == 2
    assert 'public.ding_approval_instance' in sql and 'public.ding_process_template' in sql
    assert 'USING(corp_id,process_code)' in sql and 'registered_financial_flow' in sql
    assert 'raw_payload' not in sql and 'attachment' not in sql and 'unsafe' not in sql
