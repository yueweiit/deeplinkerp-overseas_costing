"""OA source refreshes may update estimates, never user fee decisions."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from overseas_costing.scripts import import_oa_logistics as importer
from overseas_costing.services import fee_service, import_service
from overseas_costing.tests.test_unified_transport_fees import MODES


class FeeDB:
    def __init__(self, rows=None, mode="AIR"):
        self.rows = deepcopy(rows or [])
        self.batch = dict(name="B", current_version="V", transport_mode=mode, status="Dirty", confirm_status="Draft", is_locked=0)
        self.version = dict(name="V", batch="B", status="Active")
        self.writes = []
        self.locks = []
        self.items = []
        self.snapshot_rows = None
        self.before_parent_lock = None

    def sql(self, query, values, as_dict=False):
        assert "for update" in query.lower()
        self.locks.append(query)
        if "tabOverseas Cost Batch" in query:
            if self.before_parent_lock:
                self.before_parent_lock()
                self.before_parent_lock = None
            fields = [field.strip().strip("`") for field in query.lower().split("select", 1)[1].split("from", 1)[0].split(",")]
            return [{field: self.batch.get(field) for field in fields}]
        if "tabOverseas Cost Version" in query:
            return [dict(self.version)]
        if "tabOverseas Cost Item" in query:
            return deepcopy(self.items)
        if "tabOverseas Cost Allocation Rule" in query:
            assert values == ("B", "V")
            return deepcopy(self.rows)
        raise AssertionError(query)

    def get_value(self, doctype, key, fields, as_dict=False):
        if doctype == "Overseas Cost Batch":
            return dict(self.batch) if as_dict else self.batch.get(fields)
        if doctype == "Overseas Cost Version":
            return dict(self.version) if as_dict else self.version.get(fields)
        for row in self.rows:
            if key == row.get("name") or isinstance(key, dict) and all(row.get(field) == value for field, value in key.items()):
                return dict(row) if as_dict else row.get(fields)
        return None

    def set_value(self, doctype, name, values, value=None, **kwargs):
        values = values if isinstance(values, dict) else {values: value}
        self.writes.append((doctype, name, deepcopy(values)))
        if doctype == "Overseas Cost Allocation Rule":
            next(row for row in self.rows if row["name"] == name).update(values)
        elif doctype == "Overseas Cost Batch":
            self.batch.update(values, modified="2026-09-08 12:00:01")


def install(monkeypatch, rows=None, mode="AIR"):
    db = FeeDB(rows, mode)
    audits = []

    def get_all(doctype, **kwargs):
        assert doctype == "Overseas Cost Allocation Rule"
        assert kwargs["filters"] == {"batch": "B", "version": "V"}
        assert db.locks, "read fee rows only after locking their parent"
        return deepcopy(db.rows if db.snapshot_rows is None else db.snapshot_rows)

    def get_doc(values):
        def insert(**kwargs):
            name = f"NEW-{len(db.rows) + 1}"
            row = {**values, "name": name}
            db.writes.append(("Overseas Cost Allocation Rule", name, deepcopy(values)))
            db.rows.append(row)
            return SimpleNamespace(name=name)
        return SimpleNamespace(insert=insert)

    monkeypatch.setattr(importer, "frappe", SimpleNamespace(db=db, get_all=get_all, get_doc=get_doc,
                                                          session=SimpleNamespace(user="Administrator")))
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *args: None)
    monkeypatch.setattr(importer, "_insert_batch_audit_log", lambda **kwargs: audits.append(kwargs))
    return db, audits


def sync(amount=500, **extra):
    approval = {"logistics_fee": {"amount": amount, "currency": "RMB"}, **extra}
    return importer._sync_oa_logistics_allocation_rule(batch_name="B", version_name="V", approval_item=approval)


def test_multi_fee_sync_uses_one_canonical_upsert_path_and_is_idempotent(monkeypatch):
    db, _ = install(monkeypatch, mode="EXPRESS")
    approval = {
        "transport_mode_raw": "Express快递",
        "form_fields": {
            "物流费用": "100 USD",
            "币种Moneda": "USD",
            "清关费": "200",
            "进口税费": "300",
            "快递附加费": "40",
            "当地配送费": "50",
        },
    }

    created = importer._sync_oa_logistics_allocation_rules(
        batch_name="B", version_name="V", approval_item=approval
    )
    repeated = importer._sync_oa_logistics_allocation_rules(
        batch_name="B", version_name="V", approval_item=approval
    )

    assert created["ok"] is True
    assert created["created_count"] == 5
    assert repeated["action"] == "unchanged"
    assert repeated["updated_count"] == 0
    assert len(db.rows) == 5
    assert {row["logical_fee_key"] for row in db.rows} == {
        "international_express_fee",
        "customs_clearance_fee",
        "import_tax",
        "express_surcharge",
        "destination_delivery",
    }
    assert next(row for row in db.rows if row["logical_fee_key"] == "international_express_fee")["currency"] == "USD"
    assert all(row["currency"] == "MXN" for row in db.rows if row["logical_fee_key"] != "international_express_fee")
    assert all(row["allocation_basis"] == "gross_weight" for row in db.rows
               if row["logical_fee_key"] in {"international_express_fee", "express_surcharge", "destination_delivery"})
    assert all(row["allocation_basis"] == "goods_value" for row in db.rows
               if row["logical_fee_key"] in {"customs_clearance_fee", "import_tax"})


@pytest.mark.parametrize("mode,key,label,basis,count", MODES)
def test_new_oa_fee_uses_saved_batch_transport_and_explicit_estimate(monkeypatch, mode, key, label, basis, count):
    db, audits = install(monkeypatch, mode=mode)
    result = sync(500, transport_mode="SEA")
    assert result["action"] == "created"
    row = db.rows[0]
    assert (row["logical_fee_key"], row["expense_category"], row["allocation_basis"]) == (key, label, basis)
    assert row["amount_status"] == "ESTIMATED" and row["amount"] == 500
    assert row["rule_code"] == "oa_logistics_freight" and row["amount_revision"].startswith("oa:")
    assert audits
    assert sync(500)["action"] == "unchanged"
    assert len(db.rows) == 1


@pytest.mark.parametrize("amount", [None, "", 0, "0", "garbage"])
def test_absent_or_invalid_oa_money_does_not_create_a_zero_fee(monkeypatch, amount):
    db, _ = install(monkeypatch)
    assert sync(amount)["action"] == "skipped"
    assert not db.writes


@pytest.mark.parametrize("state,revision", [("ACTUAL", ""), ("NOT_INCURRED", ""), ("INCLUDED", ""),
                                          ("ESTIMATED", "user-saved-revision"), ("MISSING", "manual:revision")])
def test_oa_refresh_preserves_user_amount_decisions_and_retains_quote_audit(monkeypatch, state, revision):
    original = dict(name="OA", rule_code="oa_logistics_freight", expense_category="国际物流费用",
                    amount=20360, amount_status=state, currency="USD", amount_revision=revision)
    db, audits = install(monkeypatch, [original])
    result = sync()
    assert result["action"] == "protected" and result["ok"]
    assert not db.writes and db.rows == [original]
    assert audits[-1]["new_value"]["candidate_fee"]["amount"] == 500


@pytest.mark.parametrize("manual", [dict(logical_fee_key="international_air_freight"), dict(expense_category="国际空运费")])
def test_existing_manual_primary_prevents_creating_oa_duplicate(monkeypatch, manual):
    original = dict(name="MANUAL", rule_code="manual-1", amount=123, amount_status="ESTIMATED", **manual)
    db, _ = install(monkeypatch, [original])
    assert sync()["action"] == "protected"
    assert db.rows == [original] and not db.writes


@pytest.mark.parametrize("flag", ["is_enabled", "is_active"])
def test_retired_oa_fee_is_not_resurrected(monkeypatch, flag):
    original = dict(name="RETIRED", rule_code="oa_logistics_freight", amount=200, **{flag: 0})
    db, _ = install(monkeypatch, [original])
    assert sync()["action"] == "retired"
    assert db.rows == [original] and not db.writes


def test_legacy_source_estimate_refresh_gets_identity_and_source_revision(monkeypatch):
    db, _ = install(monkeypatch, [dict(name="OA", rule_code="oa_logistics_freight", amount=200, amount_status="MISSING")])
    assert sync()["action"] == "updated"
    assert db.rows[0]["logical_fee_key"] == "international_air_freight"
    assert db.rows[0]["amount_status"] == "ESTIMATED" and db.rows[0]["amount"] == 500
    assert db.rows[0]["amount_revision"].startswith("oa:")


def test_duplicate_primary_oa_sync_is_blocked_without_writing_either_fee(monkeypatch):
    rows = [dict(name="OA", rule_code="oa_logistics_freight", amount=200),
            dict(name="MANUAL", logical_fee_key="international_air_freight", amount=300)]
    db, _ = install(monkeypatch, rows)
    result = sync()
    assert result["action"] == "conflict" and not result["ok"]
    assert set(result["duplicate_rule_names"]) == {"OA", "MANUAL"}
    assert db.rows == rows and not db.writes


@pytest.mark.parametrize("target,changes", [
    ("batch", dict(current_version="OTHER")), ("batch", dict(is_locked=1)),
    ("batch", dict(confirm_status="Confirmed")), ("version", dict(status="Confirmed")),
    ("version", dict(status="Archived")), ("version", dict(batch="OTHER")),
    ("batch", dict(confirm_status="Partially Confirmed")), ("batch", dict(status="Confirmed")),
    ("batch", dict(status="Written Back")), ("version", dict(status="")), ("version", dict(status="Draft")),
    ("batch", dict(edit_lock_owner="Administrator", edit_lock_expires_at=datetime.now() + timedelta(minutes=4))),
    ("batch", dict(transport_mode="")),
])
def test_oa_sync_respects_frozen_version_live_lease_and_unknown_transport(monkeypatch, target, changes):
    db, _ = install(monkeypatch)
    getattr(db, target).update(changes)
    result = sync(transport_mode="SEA")
    assert not result["ok"] and result["action"] == "blocked"
    assert not db.writes


@pytest.mark.parametrize("token,modified,allowed", [("OWNED", "2026-09-08 12:00:00", True),
    ("WRONG", "2026-09-08 12:00:00", False), ("OWNED", None, False), (None, "2026-09-08 12:00:00", False),
    ("OWNED", "2026-09-08 11:00:00", False)])
def test_manual_quote_sync_only_accepts_matching_live_edit_context(monkeypatch, token, modified, allowed):
    db, _ = install(monkeypatch)
    db.batch.update(edit_lock_owner="Administrator", edit_lock_token="OWNED", modified="2026-09-08 12:00:00",
                    edit_lock_expires_at=datetime.now() + timedelta(minutes=4))
    result = importer._sync_oa_logistics_allocation_rule(batch_name="B", version_name="V",
        approval_item={"logistics_fee": {"amount": 100}}, edit_token=token, expected_modified=modified, manual_entry=True)
    assert result["ok"] is allowed
    if allowed:
        assert db.rows[0]["amount_revision"].startswith("manual:")
    else:
        assert not db.writes


def test_manual_confirmation_of_unchanged_oa_estimate_marks_it_as_manually_saved(monkeypatch):
    db, _ = install(monkeypatch)
    sync()
    db.batch.update(edit_lock_owner="Administrator", edit_lock_token="OWNED",
                    edit_lock_expires_at=datetime.now() + timedelta(minutes=4))
    result = importer._sync_oa_logistics_allocation_rule(batch_name="B", version_name="V",
        approval_item={"logistics_fee": {"amount": 500, "currency": "RMB"}}, manual_entry=True,
        edit_token="OWNED", expected_modified=db.batch["modified"])
    assert result["action"] == "updated"
    assert db.rows[0]["amount_revision"].startswith("manual:")
    db.batch["edit_lock_expires_at"] = None
    assert sync(700)["action"] == "protected" and db.rows[0]["amount"] == 500


@pytest.mark.parametrize("value", [0, "0", "0.00 RMB"])
def test_explicit_zero_in_oa_form_field_does_not_create_an_estimated_fee(value):
    assert importer.extract_logistics_fee_from_approval({"form_fields": {"物流费用": value}}) == {}


@pytest.mark.parametrize("operation", ["confirm_logistics_quote_candidate", "save_manual_logistics_quote"])
def test_existing_quote_api_forwards_lease_context(monkeypatch, operation):
    import importlib
    import sys
    from types import ModuleType
    stub = ModuleType("frappe")
    stub.whitelist = lambda: (lambda fn: fn)
    monkeypatch.setitem(sys.modules, "frappe", stub)
    sys.modules.pop("overseas_costing.api.import_api", None)
    api = importlib.import_module("overseas_costing.api.import_api")
    monkeypatch.setattr(api, "require_batch_permission", lambda name, permission: "B")
    monkeypatch.setattr(api.import_service, operation, lambda **kwargs: kwargs)
    kwargs = {"candidate_index": 0} if operation.startswith("confirm") else {"amount": 100}
    result = getattr(api, operation)(batch_name="B", edit_token="OWNED", expected_modified="M", **kwargs)
    assert result["edit_token"] == "OWNED" and result["expected_modified"] == "M"


@pytest.mark.parametrize("operation", ["confirm_logistics_quote_candidate", "save_manual_logistics_quote"])
def test_quote_services_forward_owned_context_and_do_not_claim_protected_fee_was_changed(monkeypatch, operation):
    monkeypatch.setattr(import_service, "frappe", SimpleNamespace(db=SimpleNamespace(commit=lambda: None)))
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V")
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *args: None)
    monkeypatch.setattr(import_service, "_get_batch_trace_row", lambda name: {"extra_json": {
        "logistics_quote_candidates": [{"amount": 100, "currency": "RMB"}]}})
    captured = []
    def protected(**kwargs):
        captured.append(kwargs)
        return {"ok": True, "action": "protected", "message": "已保留报价供复核"}
    monkeypatch.setattr(importer, "_sync_oa_logistics_allocation_rule", protected)
    kwargs = {"candidate_index": 0} if operation.startswith("confirm") else {"amount": 100}
    result = getattr(import_service, operation)(batch_name="B", edit_token="OWNED", expected_modified="M", **kwargs)
    assert result["ok"] and result["rule_result"]["action"] == "protected"
    assert captured[0]["edit_token"] == "OWNED" and captured[0]["expected_modified"] == "M"
    assert captured[0]["manual_entry"] is True
    assert "生成" not in result["message"]


def test_oa_sync_rechecks_current_invalid_source_after_taking_parent_lock(monkeypatch):
    db, _ = install(monkeypatch)
    db.before_parent_lock = lambda: db.batch.update(source_approval_status="TERMINATED")
    result = sync()
    assert not result["ok"] and result["invalid_business"]
    assert not db.writes


def test_blocked_fee_sync_prevents_background_recalculation_from_bypassing_lease(monkeypatch):
    install(monkeypatch)
    monkeypatch.setattr("overseas_costing.services.calculate_service.recalculate_batch",
                        lambda **kwargs: pytest.fail("blocked fee sync must not trigger trusted calculation"))
    result = importer._recalculate_after_purchase_sync(batch_name="B", version_name="V",
        purchase_sync={"ok": True, "updated_count": 1}, logistics_fee_sync={"ok": False, "action": "blocked", "message": "租约占用"})
    assert not result["ok"] and result["action"] == "blocked"


@pytest.mark.parametrize("operation", ["confirm_logistics_quote_candidate", "save_manual_logistics_quote"])
def test_quote_service_saves_through_shared_sync_and_returns_new_modified(monkeypatch, operation):
    db, _ = install(monkeypatch)
    db.batch.update(modified="2026-09-08 12:00:00", edit_lock_owner="Administrator", edit_lock_token="OWNED",
                    edit_lock_expires_at=datetime.now() + timedelta(minutes=4))
    db.commit = lambda: None
    importer.frappe.utils = SimpleNamespace(now_datetime=datetime.now)
    monkeypatch.setattr(import_service, "frappe", importer.frappe)
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V")
    monkeypatch.setattr(import_service, "_get_batch_trace_row", lambda name: {"extra_json": {
        "logistics_quote_candidates": [{"amount": 100, "currency": "RMB"}]}})
    monkeypatch.setattr(import_service, "_create_audit_log", lambda **kwargs: None)
    monkeypatch.setattr(import_service, "_mark_batch_dirty", lambda *args: None)
    monkeypatch.setattr(import_service, "_recalculate_after_writeback", lambda **kwargs: {"ok": True})
    kwargs = {"candidate_index": 0} if operation.startswith("confirm") else {"amount": 100}
    result = getattr(import_service, operation)(batch_name="B", edit_token="OWNED", expected_modified="2026-09-08 12:00:00", **kwargs)
    assert result["ok"] and db.rows[0]["logical_fee_key"] == "international_air_freight"
    assert db.rows[0]["amount_status"] == "ESTIMATED" and db.rows[0]["amount_revision"].startswith("manual:")
    assert result["batch_modified"] == db.batch["modified"] == "2026-09-08 12:00:01"


@pytest.mark.parametrize("amount", [0, "0", "0.00", 200])
def test_quote_confirmation_cannot_replace_explicit_oa_fee_including_real_zero(monkeypatch, amount):
    monkeypatch.setattr(import_service, "frappe", SimpleNamespace())
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V")
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", lambda *args: None)
    monkeypatch.setattr(import_service, "_get_batch_trace_row", lambda name: {"extra_json": {
        "logistics_fee": {"amount": amount, "currency": "RMB"},
        "logistics_quote_candidates": [{"amount": 100, "currency": "RMB"}]}})
    calls = []
    monkeypatch.setattr(importer, "_sync_oa_logistics_allocation_rule", lambda **kwargs:
                        calls.append(kwargs) or {"ok": False, "message": "must not sync an alternative quote"})
    result = import_service.confirm_logistics_quote_candidate(batch_name="B", candidate_index=0)
    assert not result["ok"] and "明确物流费用" in result["message"]
    assert not calls


@pytest.mark.parametrize("operation", ["confirm_logistics_quote_candidate", "save_manual_logistics_quote"])
def test_manual_quote_service_without_tokens_cannot_write_even_without_an_active_lease(monkeypatch, operation):
    db, _ = install(monkeypatch)
    monkeypatch.setattr(import_service, "frappe", importer.frappe)
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V")
    monkeypatch.setattr(import_service, "_get_batch_trace_row", lambda name: {"extra_json": {
        "logistics_quote_candidates": [{"amount": 100, "currency": "RMB"}]}})
    # Stop immediately after the real sync in the old implementation, so the
    # regression reports its unauthorized fee write without unrelated mocks.
    writes_after_sync = []
    original_sync = importer._sync_oa_logistics_allocation_rule
    def sync_then_stop(**kwargs):
        result = original_sync(**kwargs)
        writes_after_sync.extend(db.writes)
        return result if not result.get("ok") else {"ok": False, "message": "unexpected fee write"}
    monkeypatch.setattr(importer, "_sync_oa_logistics_allocation_rule", sync_then_stop)
    kwargs = {"candidate_index": 0} if operation.startswith("confirm") else {"amount": 100}
    result = getattr(import_service, operation)(batch_name="B", **kwargs)
    assert not writes_after_sync
    assert not result["ok"] and "编辑会话" in result["message"]


@pytest.mark.parametrize("change,action", [("new_manual", "protected"), ("actual", "protected"),
                                         ("retired", "retired"), ("duplicate", "conflict")])
def test_oa_sync_uses_current_fee_rows_after_waiting_for_parent_under_repeatable_read(monkeypatch, change, action):
    old = [] if change == "new_manual" else [dict(name="OA", rule_code="oa_logistics_freight", amount=200,
        amount_status="ESTIMATED", amount_revision="oa:old", allocation_basis="chargeable_weight")]
    db, _ = install(monkeypatch, old)
    # The preliminary ordinary source read establishes the old RR snapshot.
    def old_source_check(*args):
        if db.snapshot_rows is None:
            db.snapshot_rows = deepcopy(db.rows)
        return None
    monkeypatch.setattr(import_service, "_invalid_batch_cost_write_response", old_source_check)
    def another_transaction_commits_before_parent_lock_is_granted():
        if change in {"new_manual", "duplicate"}:
            db.rows.append(dict(name="MANUAL", logical_fee_key="international_air_freight", rule_code="manual",
                                amount=333, amount_status="ACTUAL", amount_revision="manual:saved"))
        elif change == "actual":
            db.rows[0].update(amount=333, amount_status="ACTUAL", amount_revision="manual:saved")
        else:
            db.rows[0].update(is_enabled=0)
    db.before_parent_lock = another_transaction_commits_before_parent_lock_is_granted
    result = sync(500)
    assert result["action"] == action
    assert not db.writes
    assert any("tabOverseas Cost Allocation Rule" in sql for sql in db.locks)


def test_oa_sync_checks_current_item_provenance_after_parent_lock(monkeypatch):
    db, _ = install(monkeypatch)
    db.batch["extra_json"] = {"oa_logistics_trace": {"linked_purchase_approvals": [
        {"source_instance_id": "VALID", "approval_status": "COMPLETED"},
        {"source_instance_id": "REVOKED", "approval_status": "TERMINATED"}]}}
    db.before_parent_lock = lambda: db.items.append(dict(name="NEW-BAD-ITEM", source_type="purchase_expense_oa", dingtalk_instance_id="REVOKED"))
    result = sync()
    assert not result["ok"] and result["invalid_business"]
    assert not db.writes


def test_existing_approval_refresh_does_not_backfill_new_optional_fee_mappings(monkeypatch):
    descriptors = [
        {"logical_fee_key": "international_air_freight", "amount": 100},
        {"logical_fee_key": "customs_clearance_fee", "amount": 20},
    ]
    captured = []
    monkeypatch.setattr(importer, "extract_logistics_fees_from_approval", lambda approval: descriptors)
    monkeypatch.setattr(
        importer,
        "_sync_oa_fee_descriptor",
        lambda **kwargs: captured.append(kwargs["fee_descriptor"]) or {"ok": True, "action": "unchanged"},
    )

    result = importer._sync_oa_logistics_allocation_rules(
        batch_name="B",
        version_name="V",
        approval_item={},
        include_new_optional_fees=False,
    )

    assert result["ok"] is True
    assert captured == [descriptors[0]]


@pytest.mark.parametrize("basis", ["goods_value", "gross_weight", "volume", "chargeable_weight"])
def test_manual_quote_basis_is_saved_and_used_by_preview_and_calculation(monkeypatch, basis):
    from overseas_costing.services import cost_preview_service, fee_allocation_service
    db, _ = install(monkeypatch)
    db.batch.update(modified="2026-09-08 12:00:00", edit_lock_owner="Administrator", edit_lock_token="OWNED",
                    edit_lock_expires_at=datetime.now() + timedelta(minutes=4))
    db.commit = lambda: None
    importer.frappe.utils = SimpleNamespace(now_datetime=datetime.now)
    monkeypatch.setattr(import_service, "frappe", importer.frappe)
    monkeypatch.setattr(import_service, "_resolve_batch_name", lambda name: "B")
    monkeypatch.setattr(import_service, "_resolve_version_name", lambda *args: "V")
    monkeypatch.setattr(import_service, "_get_batch_trace_row", lambda name: {"extra_json": {}})
    monkeypatch.setattr(import_service, "_create_audit_log", lambda **kwargs: None)
    monkeypatch.setattr(import_service, "_mark_batch_dirty", lambda *args: None)
    monkeypatch.setattr(import_service, "_recalculate_after_writeback", lambda **kwargs: {"ok": True})
    result = import_service.save_manual_logistics_quote(batch_name="B", amount=100, allocation_basis=basis,
        edit_token="OWNED", expected_modified="2026-09-08 12:00:00")
    assert result["ok"]
    row = db.rows[0]
    assert row["allocation_basis"] == row["basis_field"] == basis
    assert fee_allocation_service.preferred_allocation_basis(row) == basis
    items = [dict(name="I", stable_line_key="I", goods_value=100, quantity=1, unit="个", gross_weight_kg=10, volume_m3=2, chargeable_weight_kg=12),
             dict(name="J", stable_line_key="J", goods_value=300, quantity=1, unit="个", gross_weight_kg=10, volume_m3=8, chargeable_weight_kg=4)]
    fees = fee_service.compose_fee_worklist_rows([row], "AIR")
    preview = cost_preview_service.preview_comprehensive_cost_data(items, fees, {})
    saved = cost_preview_service.build_saved_cost_data(items, fees, {}, "AIR")
    assert preview["included_fees"][0]["allocation_basis"] == saved["included_fees"][0]["allocation_basis"] == basis
    expected = {"goods_value": "25.00", "gross_weight": "50.00", "volume": "20.00", "chargeable_weight": "75.00"}[basis]
    assert preview["included_fees"][0]["allocations"]["I"] == saved["included_fees"][0]["allocations"]["I"] == expected
    before = deepcopy(db.rows)
    write_count = len(db.writes)
    db.batch.update(edit_lock_owner="", edit_lock_token="", edit_lock_expires_at=None)
    for _ in range(2):
        assert sync(999)["action"] == "protected"
        refreshed = fee_service.compose_fee_worklist_rows(db.rows, "AIR")
        assert fee_allocation_service.preferred_allocation_basis(refreshed[0]) == basis
    assert db.rows == before and len(db.writes) == write_count
