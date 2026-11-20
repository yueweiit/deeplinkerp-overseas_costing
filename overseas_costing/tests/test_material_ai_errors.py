"""AI endpoint failures are safe inline responses, with no partial writes."""
import importlib
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('failure,code', [(ValueError('请刷新预览。'), 'REVIEW_REQUIRED'),
                                       (PermissionError('secret permission details'), 'PERMISSION_DENIED'),
                                       (RuntimeError('secret SQL and document body'), 'AI_INTERNAL_ERROR')])
def test_endpoint_rolls_back_logs_only_identity_and_returns_inline_error(monkeypatch, failure, code):
    events, calls = [], []
    fake = SimpleNamespace(db=SimpleNamespace(rollback=lambda:events.append('rollback')),
        logger=lambda *args: SimpleNamespace(warning=lambda message:events.append(message)))
    monkeypatch.setitem(sys.modules, 'frappe', fake)
    errors = importlib.import_module('overseas_costing.services.material_ai_errors')
    @errors.source_review_endpoint('启动分析')
    def endpoint(batch_name):
        calls.append(batch_name)
        raise failure
    response = endpoint('B1')
    assert response['ok'] is False and response['code'] == code
    assert response['retryable'] is False and response['error']['retryable'] is False
    assert events[0] == 'rollback'
    assert 'secret' not in str(response) + str(events)
    assert response['error']['stage'] == '启动分析'
    assert response['trace_id'] in str(events)
    assert calls == ['B1'] and events.count('rollback') == 1


def test_source_error_keeps_approval_and_corrective_action(monkeypatch):
    from overseas_costing.services.material_ai_source_dependencies import SourceEligibilityError
    monkeypatch.setitem(sys.modules, 'frappe', SimpleNamespace(db=SimpleNamespace(rollback=lambda:None),
        logger=lambda *a:SimpleNamespace(warning=lambda msg:None)))
    from overseas_costing.services.material_ai_errors import source_review_endpoint
    @source_review_endpoint('读取资料')
    def endpoint(batch_name):
        raise SourceEligibilityError('附件本地归档缺失。', source={'approval_no':'APP1', 'source_label':'packing.xlsx'},code='SOURCE_ARCHIVE_MISSING')
    result=endpoint('B1')
    assert result['error']['approval_no']=='APP1' and 'packing.xlsx' in result['message']
    assert '刷新资料' in result['error']['next_action']
    assert '查看资料来源' not in result['error']['next_action']


def test_returned_conflict_is_structured_without_losing_current_note():
    from overseas_costing.services.material_ai_errors import source_review_endpoint
    @source_review_endpoint('启动分析')
    def endpoint(batch_name):
        return {'ok':False, 'conflict':True, 'message':'说明已变化，请刷新。', 'clarification':{'revision':3}}
    result=endpoint('B1')
    assert result['error']['reason']=='说明已变化，请刷新。'
    assert result['error']['code']=='REVIEW_REQUIRED' and not result['retryable']
    assert result['clarification']['revision']==3


def test_database_deadlock_restarts_transaction_without_changing_request(monkeypatch):
    from overseas_costing.services.material_ai_errors import source_review_endpoint
    class QueryDeadlockError(Exception):
        pass
    calls=[];rollbacks=[]
    monkeypatch.setitem(sys.modules,'frappe',SimpleNamespace(db=SimpleNamespace(rollback=lambda:rollbacks.append(True))))
    @source_review_endpoint('启动分析')
    def endpoint(batch_name,request_id):
        calls.append(request_id)
        if len(calls)==1:raise QueryDeadlockError('1020')
        return {'ok':True,'run_id':'ALREADY-COMMITTED'}
    assert endpoint('B','SAME-REQUEST')['run_id']=='ALREADY-COMMITTED'
    assert calls==['SAME-REQUEST','SAME-REQUEST'] and len(rollbacks)==1


def test_endpoint_deadlock_limit_keeps_three_rollbacks_and_private_inline_failure(monkeypatch):
    from overseas_costing.services.material_ai_errors import source_review_endpoint

    class QueryDeadlockError(Exception):
        pass

    calls, events = [], []
    monkeypatch.setitem(sys.modules, 'frappe', SimpleNamespace(
        db=SimpleNamespace(rollback=lambda: events.append('rollback')),
        logger=lambda *args: SimpleNamespace(warning=events.append)))

    @source_review_endpoint('启动分析')
    def endpoint(batch_name, request_id):
        calls.append((batch_name, request_id))
        raise QueryDeadlockError('private SQL and document body')

    result = endpoint('B1', 'SAME-REQUEST')
    assert calls == [('B1', 'SAME-REQUEST')] * 3 and events.count('rollback') == 3
    assert result['code'] == 'AI_INTERNAL_ERROR' and not result['retryable']
    assert 'private' not in str(result) + str(events)
    assert sum('material_ai_request_failed' in row for row in events) == 1
