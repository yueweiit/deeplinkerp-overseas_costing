"""One transport identity governs worklists, legacy OA fees and costing."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from overseas_costing.services import cost_preview_service, fee_allocation_service, fee_service


MODES = [
    ("EXPRESS", "international_express_fee", "国际快递费", "gross_weight", 5),
    ("AIR", "international_air_freight", "国际空运费", "gross_weight", 4),
    ("AIR_STANDARD", "international_air_freight", "国际空运费", "gross_weight", 4),
    ("AIR_DDP", "international_air_freight", "国际空运费", "gross_weight", 4),
    ("SEA", "international_sea_freight", "国际海运费", "gross_weight", 4),
    ("SEA_STANDARD", "international_sea_freight", "国际海运费", "gross_weight", 4),
    ("SEA_DDP", "international_sea_freight", "国际海运费", "gross_weight", 4),
]


@pytest.mark.parametrize("mode,key,label,basis,count", MODES)
def test_all_transport_modes_share_primary_identity_and_local_defaults(mode, key, label, basis, count):
    rows = fee_service.build_default_fee_templates(mode)
    assert len(rows) == count
    assert fee_service.primary_freight_definition(mode) == {
        "logical_fee_key": key, "expense_category": label, "allocation_basis": basis}
    assert rows[0]["logical_fee_key"] == key
    assert not any(row["logical_fee_key"] in {"air_forwarder_surcharge", "sea_port_forwarder_surcharge"} for row in rows)
    local = {row["logical_fee_key"]: row for row in rows if row["logical_fee_key"] in {
        "customs_clearance_fee", "import_tax", "destination_delivery"}}
    assert all(row["currency"] == "MXN" and row["entry_responsibility"] == "MEXICO" for row in local.values())
    assert all(local[key]["amount_status"] == "MISSING" and local[key]["amount"] == "" for key in ("customs_clearance_fee", "import_tax"))
    delivery = local["destination_delivery"]
    assert (delivery["amount_status"], delivery["amount"]) == ("ESTIMATED", "0")
    assert delivery["expense_category"] == ("当地快递费" if mode == "EXPRESS" else "当地配送费")


@pytest.mark.parametrize("mode,key,label,basis,count", MODES)
def test_known_legacy_oa_amount_maps_without_mutating_saved_values(mode, key, label, basis, count):
    raw = dict(name="OA-1", rule_code="oa_logistics_freight", expense_category="国际物流费用",
               amount="20360", currency="", amount_status="MISSING", is_enabled=1, is_active=1)
    original = deepcopy(raw)
    rows = fee_service.compose_fee_worklist_rows([raw], mode)
    row = next(row for row in rows if row.get("name") == "OA-1")
    assert len(rows) == count
    assert row["logical_fee_key"] == key and row["expense_category"] == label
    assert row["amount_status"] == "ESTIMATED" and row["currency"] == "RMB"
    assert fee_allocation_service.preferred_allocation_basis(row) == basis
    assert raw == original
    updates = fee_service.legacy_oa_freight_updates(raw, mode)
    assert updates["logical_fee_key"] == key and updates["amount_status"] == "ESTIMATED"
    assert not {"amount", "currency", "remark"}.intersection(updates)


@pytest.mark.parametrize("status", ["ACTUAL", "NOT_INCURRED", "INCLUDED"])
def test_legacy_oa_correction_preserves_declared_amount_states(status):
    raw = dict(name="OA", rule_code="oa_logistics_freight", expense_category="国际物流费用", amount=50, amount_status=status)
    assert "amount_status" not in fee_service.legacy_oa_freight_updates(raw, "AIR")


@pytest.mark.parametrize("amount", [0, "0", "", None])
def test_legacy_oa_zero_or_absent_money_does_not_guess_a_declared_zero(amount):
    raw = dict(rule_code="oa_logistics_freight", expense_category="国际物流费用", amount=amount, amount_status="MISSING")
    assert "amount_status" not in fee_service.legacy_oa_freight_updates(raw, "AIR")


def test_explicit_manual_missing_state_is_not_reinterpreted_from_nonzero_money():
    raw = dict(name="MANUAL", logical_fee_key="international_air_freight", amount=200, amount_status="MISSING")
    row = fee_service.compose_fee_worklist_rows([raw], "AIR")[0]
    assert row["amount_status"] == "MISSING"


@pytest.mark.parametrize("mode", ["", None, "BOAT_UNKNOWN"])
def test_unknown_transport_never_invents_sea_identity(mode):
    with pytest.raises(ValueError, match="运输方式"):
        fee_service.primary_freight_definition(mode)
    rows = fee_service.compose_fee_worklist_rows([
        dict(name="OA", rule_code="oa_logistics_freight", expense_category="国际物流费用", amount=200, amount_status="MISSING")], mode)
    assert not any(row["logical_fee_key"] == "international_sea_freight" for row in rows)
    assert next(row for row in rows if row.get("name") == "OA")["legacy_unmapped"]


@pytest.mark.parametrize("key,label", [("air_forwarder_surcharge", "空运附加费"), ("sea_port_forwarder_surcharge", "港杂/货代附加费")])
def test_historical_surcharges_remain_recognized_even_without_default_row(key, label):
    assert fee_service.map_historical_fee_key({"expense_category": label}, "AIR") == key


def conflicting_rows():
    return [dict(name="OA", rule_code="oa_logistics_freight", expense_category="国际物流费用", amount=200, amount_status="MISSING"),
            dict(name="MANUAL", logical_fee_key="international_air_freight", expense_category="国际空运费", amount=300, amount_status="ACTUAL")]


def test_primary_conflicts_preserve_both_rows_and_exclude_both_from_preview():
    raw = conflicting_rows()
    original = deepcopy(raw)
    rows = fee_service.compose_fee_worklist_rows(raw, "AIR")
    freight = [row for row in rows if row["logical_fee_key"] == "international_air_freight"]
    assert {row["name"] for row in freight} == {"OA", "MANUAL"}
    assert all(set(row["duplicate_rule_names"]) == {"OA", "MANUAL"} and row["requires_review"] for row in freight)
    preview = cost_preview_service.preview_comprehensive_cost_data(
        [dict(name="I", stable_line_key="I", goods_value=100, quantity=1, unit="个")], rows, {})
    excluded = [row for row in preview["excluded_fees"] if row["reason_code"] == "DUPLICATE_LOGICAL_FEE"]
    assert {row["rule_name"] for row in excluded} == {"OA", "MANUAL"}
    assert preview["summary"]["total_cost_rmb"] == "100.00"
    assert raw == original


def test_duplicate_logical_fee_prevents_saving_or_persisted_costing():
    rows = fee_service._decorate_historical_rules(conflicting_rows(), "AIR")
    with pytest.raises(ValueError, match="重复.*OA.*MANUAL"):
        fee_service.merge_logical_fee(rows, dict(logical_fee_key="international_air_freight", amount=500))
    with pytest.raises(ValueError, match="重复"):
        cost_preview_service.build_saved_cost_data([], rows, {}, "AIR")


@pytest.mark.parametrize("flag", ["is_enabled", "is_active"])
def test_retired_primary_fee_is_excluded_without_creating_a_replacement_default(flag):
    raw = dict(name="OLD", logical_fee_key="international_air_freight", amount=200, amount_status="ACTUAL", **{flag: 0})
    rows = fee_service.compose_fee_worklist_rows([raw], "AIR")
    assert not any(row["logical_fee_key"] == "international_air_freight" for row in rows)


def test_conflict_status_explains_the_duplicate_and_blocks_allocation():
    from overseas_costing.services import fee_status_service
    row = fee_service.compose_fee_worklist_rows(conflicting_rows(), "AIR")[0]
    allocation = fee_allocation_service.allocate_fee(row, [])
    assert allocation["code"] == "DUPLICATE_LOGICAL_FEE"
    status = fee_status_service.build_fee_status(fee=row, allocation=allocation, evidence=[])
    assert any(todo["code"] == "DUPLICATE_LOGICAL_FEE" for todo in status["todos"])


def test_manual_fee_save_marks_oa_estimate_as_protected_even_when_amount_is_unchanged(monkeypatch):
    saved = dict(name="OA", logical_fee_key="international_air_freight", rule_code="oa_logistics_freight",
                 expense_category="国际空运费", amount=200, amount_status="ESTIMATED", currency="RMB",
                 amount_revision="oa:source", allocation_basis="chargeable_weight", scope_type="ALL_ITEMS")
    writes = []
    db = SimpleNamespace(get_value=lambda *args: "AIR", set_value=lambda *args, **kwargs: writes.append(args), commit=lambda: None)
    monkeypatch.setattr(fee_service, "frappe", SimpleNamespace(db=db))
    monkeypatch.setattr(fee_service, "_assert_write_context", lambda *args: None)
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: [saved])
    result = fee_service.save_fee("B", "V", saved)
    assert result["fee"]["amount_revision"].startswith("manual:")
    assert result["fee"]["rule_code"] == "oa_logistics_freight"


def test_saved_calculation_rejects_duplicate_rows_before_repository_writes():
    from overseas_costing.tests.test_saved_comprehensive_cost import Repository, save
    repo = Repository()
    repo.fees = conflicting_rows()
    with pytest.raises(ValueError, match="重复"):
        save(repo)
    assert not repo.writes and not repo.committed and repo.rolled_back


@pytest.mark.parametrize("mode,key,label,basis,count", MODES)
def test_saved_costing_of_raw_legacy_oa_uses_shared_identity_and_amount_state(mode, key, label, basis, count):
    fee = dict(name="OA", rule_code="oa_logistics_freight", expense_category="国际物流费用", amount=200, amount_status="MISSING")
    result = cost_preview_service.build_saved_cost_data(
        [dict(name="I", goods_value=100, quantity=1, unit="个", gross_weight_kg=1, chargeable_weight_kg=1, volume_m3=1)], [fee], {}, mode)
    assert result["summary"]["total_cost_rmb"] == "300.00"
    assert result["included_fees"][0]["fee_key"] == key
    assert result["item_updates"][0]["transport_mode"] == fee_service.resolve_transport_mode(mode)


@pytest.mark.parametrize("mode", ["AIR_STANDARD", "AIR_DDP", "SEA_STANDARD", "SEA_DDP"])
def test_saved_repository_accepts_transport_family_variants(monkeypatch, mode):
    from overseas_costing.services import batch_service, edit_session_service
    batch = dict(name="B", current_version="V", transport_mode=mode, modified="M")
    version = dict(name="V", batch="B", status="Active", modified="M")
    db = SimpleNamespace(sql=lambda *args: [], get_value=lambda doctype, *args, **kwargs:
                         batch if doctype == "Overseas Cost Batch" else version)
    monkeypatch.setattr(cost_preview_service, "frappe", SimpleNamespace(db=db, get_all=lambda *args, **kwargs: []))
    monkeypatch.setattr(batch_service, "_resolve_batch_name", lambda name: name)
    monkeypatch.setattr(batch_service, "_build_invalid_business_state", lambda *args: {})
    monkeypatch.setattr(edit_session_service, "_lock_row", lambda name: {})
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: [])
    context, _items, fees, _fx = cost_preview_service.FrappeCostRepository().lock_and_load(
        "B", "V", edit_token=None, expected_modified=None, trusted=True)
    assert fees[0]["logical_fee_key"] == fee_service.primary_freight_definition(mode)["logical_fee_key"]
    assert context["transport_mode"] == fee_service.resolve_transport_mode(mode)


def test_save_chooses_active_primary_and_does_not_resurrect_retired_record():
    rows = [dict(name="RETIRED", logical_fee_key="international_air_freight", amount=200, is_enabled=0),
            dict(name="ACTIVE", logical_fee_key="international_air_freight", amount=300, is_enabled=1)]
    result = fee_service.merge_logical_fee(rows, dict(logical_fee_key="international_air_freight", amount=350, is_enabled=1))
    assert result["fee"]["name"] == "ACTIVE" and result["fees"][0] == rows[0]
    with pytest.raises(ValueError, match="停用"):
        fee_service.merge_logical_fee(rows[:1], dict(logical_fee_key="international_air_freight", amount=350, is_enabled=1))
