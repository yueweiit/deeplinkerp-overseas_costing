"""物料表格 API 的权限、大小和薄层契约测试。"""

import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest


def _load_api(monkeypatch):
    fake_frappe = ModuleType("frappe")
    fake_frappe.whitelist = lambda **kwargs: (lambda function: function)
    monkeypatch.setitem(sys.modules, "frappe", fake_frappe)
    sys.modules.pop("overseas_costing.api.materials", None)
    return importlib.import_module("overseas_costing.api.materials")


def test_grid_checks_read_permission_before_query(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    calls = []
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: calls.append((batch, ptype)) or "BATCH-DOC",
    )
    monkeypatch.setattr(
        api.material_input_service,
        "get_material_grid",
        lambda **kwargs: {"batch": kwargs["batch_name"]},
    )

    assert api.get_material_grid("BATCH-NO") == {"batch": "BATCH-DOC"}
    assert calls == [("BATCH-NO", "read")]


def test_supplier_resolution_checks_read_permission_and_delegates_to_central_service(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    calls = []
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: calls.append((batch, ptype)) or "BATCH-DOC",
    )
    monkeypatch.setattr(
        api.supplier_resolution_service,
        "resolve_supplier_reference",
        lambda raw: {
            "raw_value": raw,
            "status": "SUGGESTED",
            "canonical_supplier": "",
            "candidates": [{"name": "SUP-1", "score": 0.94, "high_confidence": True}],
        },
    )

    result = api.resolve_supplier_options("BATCH-NO", "Alpha Tradng")

    assert calls == [("BATCH-NO", "read")]
    assert result["raw_value"] == "Alpha Tradng"
    assert result["canonical_supplier"] == ""
    assert result["candidates"][0]["name"] == "SUP-1"


def test_supplier_creation_requires_batch_write_and_returns_expected_business_error(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    calls = []
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: calls.append((batch, ptype)) or "BATCH-DOC",
    )

    class _ExpectedError(api.supplier_resolution_service.SupplierCreationError):
        pass

    monkeypatch.setattr(
        api.supplier_resolution_service,
        "create_supplier",
        lambda name, confirm_similar=False: (_ for _ in ()).throw(
            _ExpectedError(
                "SIMILAR_SUPPLIER_CONFIRMATION_REQUIRED",
                "存在近似供应商。",
                candidates=[{"name": "SUP-1", "score": 0.94}],
            )
        ),
    )

    result = api.create_supplier_from_workbench("BATCH-NO", "Alpha Tradng", 0)

    assert calls == [("BATCH-NO", "write")]
    assert result == {
        "ok": False,
        "code": "SIMILAR_SUPPLIER_CONFIRMATION_REQUIRED",
        "message": "存在近似供应商。",
        "candidates": [{"name": "SUP-1", "score": 0.94}],
    }


def test_project_route_options_pass_resolved_batch_to_existing_service(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, permission: "BATCH-DOC" if (batch, permission) == ("BATCH-NO", "read") else "",
    )
    from overseas_costing.services import erp_sync_plan_service

    calls = []
    monkeypatch.setattr(
        erp_sync_plan_service,
        "list_project_route_options",
        lambda batch_name: calls.append(batch_name) or {"ok": True, "options": []},
    )

    assert api.list_project_route_options("BATCH-NO") == {"ok": True, "options": []}
    assert calls == ["BATCH-DOC"]


def test_preview_checks_permission_and_rejects_paths_or_urls(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    monkeypatch.setattr(api, "require_batch_permission", lambda _batch, _ptype: "BATCH-DOC")
    monkeypatch.setattr(
        api.material_import_service,
        "preview_material_import",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not inspect path")),
    )

    with pytest.raises(ValueError, match="受控 ID"):
        api.preview_material_import("BATCH", "manual_xlsx", "/tmp/packing.xlsx", "Sheet1")
    with pytest.raises(ValueError, match="受控 ID"):
        api.preview_material_import("BATCH", "manual_xlsx", "https://example.test/a.xlsx", "Sheet1")


def test_apply_limits_choices_payload_and_requires_write_permission(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    calls = []
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: calls.append((batch, ptype)) or "BATCH-DOC",
    )

    with pytest.raises(ValueError, match="过大"):
        api.apply_material_import("BATCH", "token", "x" * 200_000, "EDIT", "MOD")

    assert calls == [("BATCH", "write")]


def test_set_shipping_quantity_uses_write_permission_and_whitelisted_mode(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    monkeypatch.setattr(api, "require_batch_permission", lambda _batch, _ptype: "BATCH-DOC")
    captured = {}
    monkeypatch.setattr(
        api.material_input_service,
        "set_shipping_quantity",
        lambda **kwargs: captured.update(kwargs) or {"ok": True},
    )

    result = api.set_shipping_quantity(
        "BATCH",
        "ITEM-1",
        "MANUAL_CONFIRMED",
        "32",
        "桶",
        "EDIT",
        "MOD",
    )

    assert result["ok"] is True
    assert captured["batch_name"] == "BATCH-DOC"
    assert captured["mode"] == "MANUAL_CONFIRMED"
    with pytest.raises(ValueError, match="来源状态"):
        api.set_shipping_quantity("BATCH", "ITEM-1", "CLIENT_FAKE", "32", "桶", "EDIT", "MOD")


def test_merge_review_preview_preserves_read_permission_and_payload_limit(monkeypatch):
    api = _load_api(monkeypatch)
    checks = []
    captured = {}
    monkeypatch.setattr(api, "require_batch_permission", lambda batch, ptype: checks.append(ptype) or batch)
    monkeypatch.setattr(api.material_import_service, "preview_material_import",
                        lambda *_args, **kwargs: captured.update(kwargs) or {"ok": True})
    payload = {"source_hash": "a" * 64, "ranges": []}
    assert api.preview_material_import("B1", "wiki_sheet", "WB:ST", merge_reviews_json=payload)["ok"]
    assert captured["merge_reviews_json"] == payload
    assert checks == ["read"]
    with pytest.raises(ValueError, match="过大"):
        api.preview_material_import("B1", "wiki_sheet", "WB:ST", merge_reviews_json="x" * 100_001)


def test_ai_fill_api_uses_read_for_status_and_write_for_mutations(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    checks = []
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: checks.append((batch, ptype)) or "BATCH-DOC",
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "start_material_ai_fill",
        lambda *args: {"ok": True, "args": args},
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "get_material_ai_fill_status",
        lambda *args, **kwargs: {"ok": True, "args": args, "kwargs": kwargs},
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "apply_material_ai_fill",
        lambda *args: {"ok": True, "args": args},
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "discard_material_ai_fill",
        lambda *args: {"ok": True, "args": args},
    )

    with pytest.raises(ValueError, match="重新分析"):
        api.start_material_ai_fill("B", "V", "T", "M")
    api.get_material_ai_fill_status("B", "R")
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_material_ai_fill("B", "R", '[{"item_name":"I"}]', "T", "M")
    api.discard_material_ai_fill("B", "R")
    assert checks == [
        ("B", "write"),
        ("B", "read"),
        ("B", "write"),
        ("B", "write"),
    ]


def test_source_ai_process_open_target_is_read_only_and_uses_safe_reference(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    checks = []
    captured = {}
    monkeypatch.setattr(
        api,
        "require_batch_permission",
        lambda batch, ptype: checks.append((batch, ptype)) or "BATCH-DOC",
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "get_source_ai_process_open_target",
        lambda *args: captured.update(args=args) or {"ok": True, "open_url": "dingtalk://safe"},
    )

    result = api.get_source_ai_process_open_target("B", "RUN", "proc_" + ("a" * 64))

    assert result == {"ok": True, "open_url": "dingtalk://safe"}
    assert checks == [("B", "read")]
    assert captured["args"] == ("BATCH-DOC", "RUN", "proc_" + ("a" * 64))


def test_ai_apply_rejects_oversized_or_non_array_updates_before_service(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    monkeypatch.setattr(api, "require_batch_permission", lambda batch, _ptype: batch)
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_material_ai_fill("B", "R", "x" * 1_100_000, "T", "M")
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_material_ai_fill("B", "R", '{"item_name":"I"}', "T", "M")


def test_legacy_ai_rpc_rejects_before_parsing_or_delegating_client_values(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    checks = []
    touched = []
    monkeypatch.setattr(
        api, "require_batch_permission", lambda batch, ptype: checks.append(ptype) or batch
    )
    monkeypatch.setattr(api, "_ai_updates_payload", lambda *_args: touched.append("updates"))
    monkeypatch.setattr(api, "_ai_review_payload", lambda *_args: touched.append("review"))
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "start_material_ai_fill",
        lambda *_args, **_kwargs: touched.append("start"),
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "apply_material_ai_fill",
        lambda *_args, **_kwargs: touched.append("material_apply"),
    )
    monkeypatch.setattr(
        api.material_ai_fill_service,
        "apply_source_ai_review",
        lambda *_args, **_kwargs: touched.append("source_apply"),
    )

    with pytest.raises(ValueError, match="重新分析"):
        api.start_material_ai_fill("B", "V", "TOKEN", "M1")
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_material_ai_fill("B", "R", '<html>{"value":999}</html>', "TOKEN", "M1")
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_source_ai_review(
            "B", "R", '["P"]', '{"P":{"amount":999}}', "TOKEN", "M1", '[{"value":999}]'
        )

    assert checks == ["write", "write", "write"]
    assert touched == []


def test_unified_source_review_api_uses_write_role_and_requires_edit_token_only_when_applying(monkeypatch) -> None:
    api = _load_api(monkeypatch)
    checks = []
    calls = []
    monkeypatch.setattr(api, "require_batch_permission", lambda batch, ptype: checks.append(ptype) or batch)
    monkeypatch.setattr(api.material_ai_fill_service, "start_source_ai_review", lambda *args, **kwargs: calls.append(("start", args, kwargs)) or {"ok": True})
    monkeypatch.setattr(api.material_ai_fill_service, "get_source_ai_review_status", lambda *args, **kwargs: calls.append(("get", args, kwargs)) or {"ok": True})
    monkeypatch.setattr(api.material_ai_fill_service, "apply_source_ai_review", lambda *args: calls.append(("apply", args)) or {"ok": True})
    monkeypatch.setattr(api.material_ai_fill_service, "discard_source_ai_review", lambda *args: calls.append(("discard", args)) or {"ok": True})

    api.start_source_ai_review(
        "B", "V", "两款是一套", 1, '["SOURCE-1"]',
        payment_candidate_refs_json='[{"candidate_id":"C1","revision":"R1","version":"V"}]',
    )
    api.get_source_ai_review_status("B", "R", None, "9")
    with pytest.raises(ValueError, match="重新分析"):
        api.apply_source_ai_review(
            "B",
            "R",
            '["P1"]',
            '{"P1":{}}',
            "TOKEN",
            "M1",
            '[{"item_name":"I1","fieldname":"goods_value","value":"120"}]',
        )
    api.discard_source_ai_review("B", "R")

    start_call = next(row for row in calls if row[0] == "start")
    assert start_call[2]["selected_source_ids"] == ["SOURCE-1"]
    assert start_call[2]["payment_candidate_refs"] == [
        {"candidate_id": "C1", "revision": "R1", "version": "V"}
    ]

    assert checks == ["write", "read", "write", "write"]
    assert calls[0][1] == ("B", "V", "两款是一套")
    assert calls[0][2] == {
        "force": True,
        "selected_source_ids": ["SOURCE-1"],
        "payment_candidate_refs": [
            {"candidate_id": "C1", "revision": "R1", "version": "V"}
        ],
        "request_id": None,
    }
    assert calls[1][2]["after_revision"] == 9
    assert not any(row[0] == "apply" for row in calls)

    api.start_source_ai_review("B", "V", request_id="retry-request-1")
    assert calls[-1][2]["request_id"] == "retry-request-1"
    assert checks[-1] == "write"


def test_clarification_api_permissions_and_optimistic_revision(monkeypatch):
    api = _load_api(monkeypatch)
    checks = []
    monkeypatch.setattr(api, 'require_batch_permission', lambda b, p: checks.append(p) or 'B1')
    monkeypatch.setattr(api.material_ai_fill_service, 'get_source_ai_clarification', lambda *a: {'args': a})
    monkeypatch.setattr(api.material_ai_fill_service, 'save_source_ai_clarification', lambda *a: {'args': a})
    monkeypatch.setattr(api.material_ai_fill_service, 'start_source_ai_review', lambda *a, **k: {'args': a, 'kwargs': k})
    assert api.get_source_ai_clarification('batch')['args'] == ('B1',)
    assert api.save_source_ai_clarification('batch', '说明', '2')['args'] == ('B1', '说明', 2)
    started = api.start_source_ai_review('batch', 'V1', expected_clarification_revision='3')
    assert started['args'][2] is None
    assert started['kwargs']['expected_clarification_revision'] == 3
    assert checks == ['read', 'write', 'write']


def test_selected_row_preview_and_confirm_use_ids_write_permission_and_inline_conflicts(monkeypatch):
    from types import SimpleNamespace
    from overseas_costing.services import material_ai_selection_service as selection
    api=_load_api(monkeypatch);calls=[];rollbacks=[]
    api.frappe.db=SimpleNamespace(rollback=lambda:rollbacks.append(True))
    monkeypatch.setattr(api,'require_batch_permission',lambda batch,permission:calls.append((batch,permission)) or 'B')
    monkeypatch.setattr(selection,'prepare',lambda *args:calls.append(args) or {'ok':True})
    assert api.preview_source_ai_selection('BATCH','RUN','["ROW"]','[]','update_selected','V')['ok']
    assert calls==[('BATCH','write'),('B','RUN',['ROW'],[],'update_selected','V')]
    def expired(*args):raise ValueError('来源已更新，请重新预览')
    monkeypatch.setattr(selection,'confirm',expired)
    result=api.confirm_source_ai_selection('BATCH','RUN','PREVIEW','REV','TOKEN','MOD')
    assert not result['ok'] and result['code']=='REVIEW_REQUIRED' and rollbacks


def test_selected_row_preview_passes_optional_field_and_packing_assignment_choices(monkeypatch):
    from overseas_costing.services import material_ai_selection_service as selection
    api=_load_api(monkeypatch);captured={}
    monkeypatch.setattr(api,'require_batch_permission',lambda batch,_permission:batch)
    monkeypatch.setattr(selection,'prepare',lambda *args,**kwargs:captured.update(args=args,kwargs=kwargs) or {'ok':True})

    result=api.preview_source_ai_selection(
        'B','RUN','[]','[]','update_selected','V',
        field_choices_json='{"I1:gross_weight_kg":"FIELD-1"}',
        packing_assignments_json='{"GROUP-1":"GROUP-1:ALL"}',
    )

    assert result['ok']
    assert captured['kwargs']=={
        'field_choices':{'I1:gross_weight_kg':'FIELD-1'},
        'packing_assignments':{'GROUP-1':'GROUP-1:ALL'},
    }


def test_source_reanalysis_flag_is_server_boolean(monkeypatch):
    api = _load_api(monkeypatch)
    captured = {}
    monkeypatch.setattr(api, 'require_batch_permission', lambda batch, _permission: batch)
    monkeypatch.setattr(api.material_ai_fill_service, 'start_source_ai_review',
                        lambda *args, **kwargs: captured.update(kwargs) or {'ok': True})

    api.start_source_ai_review('B', 'V', reanalyze_original_sources='true')

    assert captured['reanalyze_original_sources'] is True


@pytest.mark.parametrize('always_conflicts', [False, True])
def test_confirm_selection_retries_once_from_fresh_request_transaction(monkeypatch, always_conflicts):
    from overseas_costing.services import material_ai_selection_service as selection

    api = _load_api(monkeypatch)
    calls = []
    api.frappe.db = SimpleNamespace(rollback=lambda: calls.append(('rollback',)))

    class QueryDeadlockError(Exception):
        pass

    def permission(batch, ptype):
        calls.append(('permission', batch, ptype))
        return 'BATCH-DOC'

    def confirm(*args):
        calls.append(('confirm', *args))
        if always_conflicts or sum(call[0] == 'confirm' for call in calls) == 1:
            raise QueryDeadlockError(1020, 'Record has changed since last read')
        return {'ok': True, 'idempotent': True}

    monkeypatch.setattr(api, 'require_batch_permission', permission)
    monkeypatch.setattr(selection, 'confirm', confirm)
    args = ('BATCH', 'RUN', 'PREVIEW', 'REV', 'TOKEN', 'MOD')
    if always_conflicts:
        result = api.confirm_source_ai_selection(*args)
        assert result['ok'] is False and result['code'] == 'REVIEW_CONFLICT'
    else:
        assert api.confirm_source_ai_selection(*args) == {'ok': True, 'idempotent': True}
    attempt = [('permission', 'BATCH', 'write'), ('confirm', 'BATCH-DOC', *args[1:])]
    assert calls == [*attempt, ('rollback',), *attempt, *([('rollback',)] if always_conflicts else [])]


@pytest.mark.parametrize('problem,code', [
    ('stale_revision', 'BATCH_REVISION_CHANGED'),
    ('missing_revision', 'BATCH_REVISION_CHANGED'),
    ('expired_lease', 'EDIT_SESSION_REQUIRED'),
    ('replaced_lease', 'EDIT_SESSION_REQUIRED'),
])
def test_confirm_selection_returns_real_lease_guard_errors_without_retry(monkeypatch, problem, code):
    from datetime import datetime, timedelta
    from overseas_costing.services import edit_session_service, material_ai_selection_service as selection

    api = _load_api(monkeypatch)
    calls = []
    now = datetime(2030, 1, 1, 10)
    row = {'modified': now, 'edit_lock_owner': 'fixture-user', 'edit_lock_token': 'VALID',
           'edit_lock_expires_at': now + timedelta(minutes=5)}
    if problem == 'expired_lease':
        row['edit_lock_expires_at'] = now
    token = 'REPLACED' if problem == 'replaced_lease' else row['edit_lock_token']
    modified = (None if problem == 'missing_revision'
                else str(now - timedelta(seconds=1)) if problem == 'stale_revision'
                else str(now))
    api.frappe.db = SimpleNamespace(rollback=lambda: calls.append('rollback'))
    monkeypatch.setattr(edit_session_service, 'frappe', SimpleNamespace(session=SimpleNamespace(user='fixture-user')))
    monkeypatch.setattr(edit_session_service, '_lock_row', lambda batch: row)
    monkeypatch.setattr(edit_session_service, 'now_datetime', lambda: now)
    monkeypatch.setattr(api, 'require_batch_permission', lambda batch, permission: calls.append(permission) or 'B')

    def confirm(batch, _run, _preview, _revision, edit_token, expected_modified):
        calls.append('confirm')
        edit_session_service.assert_batch_write(batch, edit_token=edit_token, expected_modified=expected_modified)
        pytest.fail('the conflicting lease must fail before any write')

    monkeypatch.setattr(selection, 'confirm', confirm)
    result = api.confirm_source_ai_selection('BATCH', 'RUN', 'PREVIEW', 'REV', token, modified)
    assert result['ok'] is False and result['code'] == code
    assert result['message']
    assert calls == ['write', 'confirm', 'rollback']


def test_confirm_selection_does_not_swallow_unexpected_runtime_error(monkeypatch):
    from overseas_costing.services import material_ai_selection_service as selection

    api = _load_api(monkeypatch)
    monkeypatch.setattr(api, 'require_batch_permission', lambda batch, permission: batch)

    def confirm(*_args):
        raise RuntimeError('unexpected writer failure')

    monkeypatch.setattr(selection, 'confirm', confirm)
    with pytest.raises(RuntimeError, match='unexpected writer failure'):
        api.confirm_source_ai_selection('BATCH', 'RUN', 'PREVIEW', 'REV', 'TOKEN', 'MOD')


def test_payment_selection_is_returned_by_the_unified_start_endpoint(monkeypatch):
    api = _load_api(monkeypatch)
    calls = []
    monkeypatch.setattr(api, 'require_batch_permission',
                        lambda batch, permission: calls.append((batch, permission)) or 'B1')
    monkeypatch.setattr(api.material_ai_fill_service, 'start_source_ai_review',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {
                            'ok': True, 'status': 'PAYMENT_SELECTION',
                            'payment_preflight': {'status': 'NEEDS_SELECTION', 'candidates': []},
                        })

    result = api.start_source_ai_review('B', 'V')

    assert result['status'] == 'PAYMENT_SELECTION'
    assert calls[0] == ('B', 'write')
    assert calls[1][0][:2] == ('B1', 'V')


def test_material_row_recovery_api_keeps_preview_and_confirm_separate(monkeypatch):
    api = _load_api(monkeypatch)
    from overseas_costing.services import material_ai_row_recovery as recovery
    from overseas_costing.services import edit_session_service
    from overseas_costing.services.logistics_settlement.store import Store
    from overseas_costing.services.logistics_settlement import ledger as ledger_module
    calls = []
    api.frappe.db = SimpleNamespace(rollback=lambda: None, get_value=lambda *args: 'MOD-2')
    api.frappe.session = SimpleNamespace(user='finance-user')
    monkeypatch.setattr(api, 'require_batch_permission', lambda batch, permission: calls.append(('permission', permission)) or 'B1')
    monkeypatch.setattr(Store, 'frappe', lambda: 'STORE')
    monkeypatch.setattr(ledger_module, 'FrappeLedger', lambda: 'LEDGER')
    monkeypatch.setattr(recovery, 'preview_recovery', lambda *args: calls.append(('preview', args)) or {'ok': True, 'id': 'P'})
    monkeypatch.setattr(recovery, 'confirm_recovery', lambda *args: calls.append(('confirm', args)) or {'ok': True, 'version_name': 'V2'})
    monkeypatch.setattr(edit_session_service, 'assert_batch_write',
                        lambda *args, **kwargs: calls.append(('edit', args, kwargs)))

    assert api.preview_material_row_recovery('B', 'V1')['id'] == 'P'
    confirmed = api.confirm_material_row_recovery('B', 'V1', 'P', 'R', 'TOKEN', 'MOD-1')

    assert confirmed['version_name'] == 'V2' and confirmed['batch_modified'] == 'MOD-2'
    assert [call[0] for call in calls] == ['permission', 'preview', 'permission', 'edit', 'confirm']


def test_packing_group_api_keeps_server_preview_and_confirm_separate(monkeypatch):
    api = _load_api(monkeypatch)
    from overseas_costing.services import material_packing_group_service as groups
    calls = []
    api.frappe.db = SimpleNamespace(rollback=lambda: calls.append(('rollback',)))
    api.frappe.session = SimpleNamespace(user='finance-user')
    monkeypatch.setattr(api, 'require_batch_permission',
                        lambda batch, permission: calls.append(('permission', permission)) or 'B1')
    monkeypatch.setattr(groups, 'prepare_group_preview',
                        lambda *args, **kwargs: calls.append(('preview', args, kwargs)) or {'ok':True,'preview_id':'P'})
    monkeypatch.setattr(groups, 'confirm_group_preview',
                        lambda *args, **kwargs: calls.append(('confirm', args, kwargs)) or {'ok':True})

    preview = api.preview_material_packing_group(
        'B', 'V1', '["L1","L2"]', 'create', values_json='{"gross_weight_kg":"12"}', reason='共箱')
    confirmed = api.confirm_material_packing_group('B', 'P', 'R', 'TOKEN', 'MOD')

    assert preview['preview_id'] == 'P' and confirmed['ok']
    assert calls[0] == ('permission', 'write')
    assert calls[1][1][2] == ['L1','L2']
    assert calls[1][2]['values'] == {'gross_weight_kg':'12'}
    assert calls[2] == ('permission', 'write')
    assert calls[3][1] == ('B1','P','R','TOKEN','MOD')
    assert calls[3][2]['actor'] == 'finance-user'


def test_batch_group_remove_api_uses_server_preview_and_confirm(monkeypatch):
    api = _load_api(monkeypatch)
    from overseas_costing.services import material_packing_group_service as groups
    calls = []
    api.frappe.db = SimpleNamespace(rollback=lambda: calls.append(('rollback',)))
    api.frappe.session = SimpleNamespace(user='finance-user')
    monkeypatch.setattr(api, 'require_batch_permission',
                        lambda batch, permission: calls.append(('permission', permission)) or 'B1')
    monkeypatch.setattr(groups, 'prepare_group_batch_preview',
                        lambda *args, **kwargs: calls.append(('preview', args, kwargs)) or {'ok':True,'preview_id':'P'})
    monkeypatch.setattr(groups, 'confirm_group_batch_preview',
                        lambda *args, **kwargs: calls.append(('confirm', args, kwargs)) or {'ok':True})

    preview = api.preview_material_packing_group_batch(
        'B', 'V1', '["G1","G2"]', reason='批量解除')
    confirmed = api.confirm_material_packing_group_batch('B', 'P', 'R', 'TOKEN', 'MOD')

    assert preview['preview_id'] == 'P' and confirmed['ok']
    assert calls[0] == ('permission', 'write')
    assert calls[1][1] == ('B1', 'V1', ['G1', 'G2'])
    assert calls[1][2]['reason'] == '批量解除'
    assert calls[2] == ('permission', 'write')
    assert calls[3][1] == ('B1', 'P', 'R', 'TOKEN', 'MOD')
    assert calls[3][2]['actor'] == 'finance-user'


def test_bulk_material_exclusion_api_parses_stable_keys_and_uses_edit_lease(monkeypatch):
    api = _load_api(monkeypatch)
    from overseas_costing.services import material_bulk_edit_service as edits
    calls = []
    api.frappe.db = SimpleNamespace(rollback=lambda: calls.append(('rollback',)))
    api.frappe.session = SimpleNamespace(user='finance-user')
    monkeypatch.setattr(api, 'require_batch_permission',
                        lambda batch, permission: calls.append(('permission', permission)) or 'B1')
    monkeypatch.setattr(edits, 'bulk_exclude_materials',
                        lambda *args, **kwargs: calls.append(('exclude', args, kwargs)) or {'ok':True,'excluded_count':2})

    result = api.exclude_material_items(
        'B', 'V1', '["L1","L2"]', '批量软删除', 'TOKEN', 'MOD')

    assert result['excluded_count'] == 2
    assert calls[0] == ('permission', 'write')
    assert calls[1][1] == ('B1', 'V1', ['L1', 'L2'], 'TOKEN', 'MOD')
    assert calls[1][2] == {'reason':'批量软删除', 'actor':'finance-user'}
