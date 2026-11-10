import json

import pytest

from overseas_costing.services import batch_service as service


@pytest.mark.parametrize('history', [0, 1])
def test_explicit_dates_win_over_history_and_keyword(history):
    filters, alternatives, _, recent = service._build_default_batch_time_filters({
        'include_history': history, 'keyword': 'SKU',
        'start_date': '2026-08-10', 'end_date': '2026-09-08',
    })
    assert filters == [['source_created_at', '>=', '2026-08-10 00:00:00'],
                       ['source_created_at', '<=', '2026-09-08 23:59:59']]
    assert alternatives == []
    assert not recent


def test_permission_filtered_ids_are_enriched_without_exposing_private_json(monkeypatch):
    queries = []
    source = json.dumps({'linked_purchase_approvals': [
        {'approval_no': 'P-1', 'approval_status': 'COMPLETED'}]})

    class Frappe:
        @staticmethod
        def get_list(doctype, **kwargs):
            assert doctype == 'Overseas Cost Batch'
            # Frappe filters permlevel-protected fields even for a readable batch.
            assert kwargs['fields'] == ['name']
            assert not kwargs.get('or_filters')
            return [{'name': 'ALLOWED'}]

        @staticmethod
        def get_all(doctype, **kwargs):
            queries.append((doctype, kwargs))
            if doctype == 'Overseas Cost Batch':
                assert ['name', 'in', ['ALLOWED']] in kwargs['filters']
                return [{'name': 'ALLOWED', 'batch_no': 'B-1', 'transport_mode': 'AIR',
                         'source_created_at': '2026-08-20', 'extra_json': source}]
            return []

    monkeypatch.setattr(service, 'frappe', Frappe)
    monkeypatch.setattr(service, '_db_has_column', lambda *args: True)
    result = service.get_batch_list({'include_history': 1})
    assert [r['name'] for r in result['items']] == ['ALLOWED']
    assert result['items'][0]['source_status']['purchase_approval_sync_state'] == 'valid'
    assert 'extra_json' not in result['items'][0]
    assert len([q for q in queries if q[0] == 'Overseas Cost Batch']) == 1


def test_keyword_material_branch_keeps_dates_and_authorized_scope(monkeypatch):
    queries = []

    class Frappe:
        @staticmethod
        def get_list(doctype, **kwargs):
            assert kwargs['fields'] == ['name']
            return [{'name': 'ALLOWED'}]

        @staticmethod
        def get_all(doctype, **kwargs):
            queries.append((doctype, kwargs))
            if doctype == 'Overseas Cost Item':
                assert ['batch', 'in', ['ALLOWED']] in kwargs['filters']
                return [{'batch': 'ALLOWED'}]
            return []

    monkeypatch.setattr(service, 'frappe', Frappe)
    monkeypatch.setattr(service, '_db_has_column', lambda *args: True)
    result = service.get_batch_list({'keyword': 'SKU', 'include_history': 1,
                                     'start_date': '2026-08-10', 'end_date': '2026-09-08'})
    assert result['items'] == []
    batch_queries = [q for q in queries if q[0] == 'Overseas Cost Batch']
    assert len(batch_queries) == 2
    for _, query in batch_queries:
        assert ['name', 'in', ['ALLOWED']] in query['filters']
        assert ['source_created_at', '>=', '2026-08-10 00:00:00'] in query['filters']
        assert ['source_created_at', '<=', '2026-09-08 23:59:59'] in query['filters']
    keyword_query = batch_queries[0][1]
    for field in ("waybill_no", "sea_bill_no", "container_no"):
        assert field in keyword_query["fields"]
        assert [field, "like", "%SKU%"] in keyword_query["or_filters"]


def test_no_authorized_ids_means_no_internal_reads(monkeypatch):
    class Frappe:
        @staticmethod
        def get_list(*args, **kwargs):
            return []

        @staticmethod
        def get_all(*args, **kwargs):
            raise AssertionError('must not read other users data')

    monkeypatch.setattr(service, 'frappe', Frappe)
    monkeypatch.setattr(service, '_db_has_column', lambda *args: True)
    assert service.get_batch_list({'keyword': 'SKU', 'include_history': 1})['items'] == []


@pytest.mark.parametrize('source', [{}, {'extra_json': '{broken'}, {'extra_json': '[]'},
                                    {'extra_json': '{"oa_logistics_trace": []}'}])
def test_unreadable_source_is_not_reported_as_no_linked_approval(source):
    status = service._build_batch_source_status({'source_type': 'oa_logistics', **source})
    assert status['purchase_approval_sync_state'] == 'unreadable'
    assert '无法读取' in status['purchase_approval_sync_message']


def test_truly_empty_source_keeps_unlinked_status():
    status = service._build_batch_source_status({'source_type': 'oa_logistics', 'extra_json': '{}'})
    assert status['purchase_approval_sync_state'] == 'missing'


def test_snapshot_enrichment_checks_batch_ownership_even_for_wrong_pointer(monkeypatch):
    queries = []

    class Frappe:
        @staticmethod
        def get_all(doctype, **kwargs):
            queries.append(kwargs)
            return [{'name': 'WRONG-VERSION', 'batch': 'OTHER',
                     'summary_snapshot_json': '{"total_cost_rmb": 999}'}]

    monkeypatch.setattr(service, 'frappe', Frappe)
    result = service._attach_batch_calculation_snapshot([
        {'name': 'ALLOWED', 'current_version': 'WRONG-VERSION'}])
    assert queries[0]['filters']['batch'] == ['in', ['ALLOWED']]
    assert result[0]['summary_snapshot'] == {}


def test_readonly_source_enrichment_never_triggers_ai_http(monkeypatch):
    from overseas_costing.scripts import import_oa_logistics

    calls = []
    monkeypatch.setattr(import_oa_logistics, '_should_ai_parse_logistics_text', lambda *args: True)
    monkeypatch.setattr(import_oa_logistics, '_call_ai_logistics_text_summary',
                        lambda *args: calls.append(args) or {})
    result = service._logistics_text_summary({'form_fields': {'运输方式': '快递'}})
    assert isinstance(result, dict)
    assert calls == []
