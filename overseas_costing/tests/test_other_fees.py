"""Optional other fees use the existing save and real saved-cost calculation paths."""

from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from overseas_costing.services import cost_preview_service, fee_allocation_service, fee_service


KEYS = ("other_port_misc", "other_terminal", "other_agency", "other_inspection", "other_custom")
INSTANCE_KEYS = ("other_custom:" + "a" * 32, "other_custom:" + "b" * 32)


def other_fee(key, amount="0", **overrides):
    return fee_service.normalize_fee_payload({"logical_fee_key": key, "amount": amount, **overrides})


def material_rows():
    return [
        dict(name="I1", stable_line_key="L1", material_code="SKU1", quantity=10,
             goods_value=100, gross_weight_kg=1, volume_m3=1, chargeable_weight_kg=1,
             unit="个", purchase_uom="个", unit_price_uom="个", shipped_uom="个"),
        dict(name="I2", stable_line_key="L2", material_code="SKU2", quantity=20,
             goods_value=300, gross_weight_kg=3, volume_m3=1, chargeable_weight_kg=3,
             unit="个", purchase_uom="个", unit_price_uom="个", shipped_uom="个"),
    ]


@pytest.mark.parametrize("mode", ["SEA", "AIR", "EXPRESS", "SEA_DDP", "AIR_DDP"])
def test_other_fee_menu_matches_local_delivery_defaults_for_every_transport(mode):
    delivery = next(row for row in fee_service.build_default_fee_templates(mode)
                    if row["logical_fee_key"] == "destination_delivery")
    options = fee_service.build_other_fee_templates()
    assert tuple(row["logical_fee_key"] for row in options) == KEYS
    assert [row["expense_category"] for row in options] == ["港口杂费", "码头费用", "代理费", "海关查验费", "其他费用"]
    for row in options:
        for field in ("currency", "amount", "amount_status", "allocation_basis", "scope_type", "required_evidence_role"):
            assert row[field] == delivery[field]
    # Unselected menu entries must not become extra fees or missing requirements.
    assert not any(row["logical_fee_key"] in KEYS for row in fee_service.compose_fee_worklist_rows([], mode))


@pytest.mark.parametrize("key", (*KEYS, *INSTANCE_KEYS))
def test_other_fee_server_defaults_are_mxn_estimated_and_gross_weight(key):
    row = other_fee(key, "100.25")
    assert (row["currency"], row["amount_status"], row["allocation_basis"]) == ("MXN", "ESTIMATED", "gross_weight")
    assert row["scope_type"] == "ALL_ITEMS"
    assert row["required_evidence_role"] == "expense_invoice"
    assert row["logical_fee_key"] == row["rule_code"] == key


def test_other_fee_defaults_do_not_overwrite_explicit_values_or_legacy_defaults():
    row = other_fee("other_custom", "14", currency="USD", expense_category="查验费", allocation_basis="volume")
    assert (row["currency"], row["expense_category"], row["allocation_basis"]) == ("USD", "查验费", "volume")
    old = fee_service.normalize_fee_payload({"logical_fee_key": "old_fee", "amount": "5"})
    assert old["currency"] == "RMB"
    with pytest.raises(ValueError, match="币种"):
        other_fee("other_agency", "10", currency="")


def test_other_fee_respects_explicit_legacy_basis_field_and_validates_custom_names():
    assert other_fee("other_custom", "10", basis_field="volume")["allocation_basis"] == "volume"
    for label in ("", "x" * 141):
        with pytest.raises(ValueError, match="名称"):
            other_fee("other_custom", "10", expense_category=label)


@pytest.mark.parametrize("mode", ["SEA", "AIR", "EXPRESS"])
def test_each_other_fee_reaches_saved_material_cost_exactly_once(mode):
    items = material_rows()
    fees = [other_fee(key, amount, expense_category="海关查验费") for key, amount in zip(INSTANCE_KEYS, ("100", "200"))]
    original = deepcopy((items, fees))
    result = cost_preview_service.build_saved_cost_data(items, fees, {"fx_rmb_to_mxn": 2, "fx_usd_to_rmb": 7}, mode)
    assert result["summary"]["total_cost_rmb"] == "550.00"
    assert len(result["included_fees"]) == 2
    updates = result["item_updates"]
    assert [Decimal(row["total_cost_rmb"]) for row in updates] == [Decimal("137.5"), Decimal("412.5")]
    assert [Decimal(row["total_logistics_mxn"]) for row in updates] == [Decimal("75"), Decimal("225")]
    assert sum(Decimal(row["total_logistics_mxn"]) for row in updates) == Decimal("300")
    for row in updates:
        derived = json.loads(row["derived_json"])
        assert {fee["rule_code"] for fee in derived["allocated_rules"]} == set(INSTANCE_KEYS)
    assert (items, fees) == original


def test_other_fee_uses_existing_missing_weight_guard_and_conserves_rounding():
    row = other_fee("other_terminal", "0.05")
    allocated = fee_allocation_service.allocate_fee(row, material_rows())
    assert allocated["basis"] == "gross_weight"
    assert sum(Decimal(value) for value in allocated["allocations"].values()) == Decimal("0.05")
    items = material_rows()
    items[0]["gross_weight_kg"] = None
    blocked = fee_allocation_service.allocate_fee(other_fee("other_terminal", "100"), items)
    assert blocked["status"] == "BLOCKED"
    assert blocked["code"] == "ALLOCATION_BASIS_INCOMPLETE"
    assert not blocked["allocations"]


def test_unchecking_and_rechecking_preserves_one_fee_and_does_not_charge_unchecked():
    first = fee_service.merge_logical_fee([], other_fee("other_agency", "100"), revision="one")
    inactive = other_fee("other_agency", "100", amount_status="NOT_INCURRED", remark="取消勾选其他费用")
    second = fee_service.merge_logical_fee(first["fees"], inactive, revision="two")
    assert len(second["fees"]) == 1
    result = cost_preview_service.build_saved_cost_data(material_rows(), second["fees"], {"fx_rmb_to_mxn": 2}, "SEA")
    assert result["summary"]["total_cost_rmb"] == "400.00"
    restored = fee_service.merge_logical_fee(second["fees"], other_fee("other_agency", "100"), revision="three")
    assert len(restored["fees"]) == 1
    assert restored["fee"]["amount"] == "100"


def test_other_fee_menu_is_available_in_read_only_worklist_without_writes(monkeypatch):
    monkeypatch.setattr(fee_service, "frappe", None)
    result = fee_service.get_fee_worklist("BATCH", "VERSION")
    assert [row["logical_fee_key"] for row in result["other_fee_options"]] == list(KEYS)


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("key", ["other_agency", INSTANCE_KEYS[0]])
def test_other_fee_evidence_reuses_selected_row_and_never_revives_retired_row(monkeypatch, active, key):
    saved = {**other_fee(key, "100"), "name": "EXISTING", "is_active": int(active)}
    fake = SimpleNamespace(db=SimpleNamespace(get_value=lambda *args: "SEA"))
    monkeypatch.setattr(fee_service, "frappe", fake)
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: [saved])
    if active:
        assert fee_service.materialize_fee_rule("B", "V", key)["name"] == "EXISTING"
    else:
        with pytest.raises(ValueError, match="没有该逻辑费用项|停用"):
            fee_service.materialize_fee_rule("B", "V", key)


@pytest.mark.parametrize("key", ["other_custom:", "other_custom:short", "other_custom:" + "a" * 33, "other_custom:" + "G" * 32])
def test_instance_identity_rejects_malformed_reserved_keys(key):
    with pytest.raises(ValueError, match="标识"):
        other_fee(key, "10", expense_category="任意名称")


def test_same_named_instances_merge_retry_and_evidence_stay_independent():
    first = other_fee(INSTANCE_KEYS[0], "100", expense_category="查验费")
    second = other_fee(INSTANCE_KEYS[1], "200", expense_category="查验费")
    rows = fee_service.merge_logical_fee([], first)["fees"]
    rows[0]["evidence"] = [{"attachment": "FIRST"}]
    rows = fee_service.merge_logical_fee(rows, second)["fees"]
    retried = fee_service.merge_logical_fee(rows, {**second, "amount": "250"})["fees"]
    assert len(retried) == 2
    assert [(row["logical_fee_key"], row["amount"]) for row in retried] == [(INSTANCE_KEYS[0], "100"), (INSTANCE_KEYS[1], "250")]
    assert retried[0]["evidence"] == [{"attachment": "FIRST"}]
    assert not retried[1].get("evidence")
    visible = fee_service.compose_fee_worklist_rows(retried, "SEA")
    assert not any(row.get("legacy_unmapped") or row.get("duplicate_rule_names") for row in visible if row["logical_fee_key"] in INSTANCE_KEYS)


@pytest.mark.parametrize("label", ["", "x" * 141])
def test_instance_name_has_same_backend_validation_as_legacy_custom_fee(label):
    with pytest.raises(ValueError, match="名称"):
        other_fee(INSTANCE_KEYS[0], "10", expense_category=label)


def test_instance_defaults_and_missing_weight_protection_match_delivery():
    row = other_fee(INSTANCE_KEYS[0], "100", expense_category="自定义装卸费")
    assert fee_allocation_service.preferred_allocation_basis({"logical_fee_key": INSTANCE_KEYS[0]}) == "gross_weight"
    items = material_rows()
    items[0]["gross_weight_kg"] = None
    assert fee_allocation_service.allocate_fee(row, items)["status"] == "BLOCKED"


@pytest.mark.parametrize("key", ["other_agency", INSTANCE_KEYS[0]])
def test_retiring_other_fee_preserves_original_data_but_removes_allocated_cost(key):
    row = {**other_fee(key, "100", expense_category="查验费"), "name": "RULE", "evidence": [{"attachment": "A"}]}
    retired = fee_service.normalize_fee_payload({**row, "is_active": 0, "is_enabled": 0, "status_change_reason": "移除其他费用"})
    merged = fee_service.merge_logical_fee([row], retired)
    assert merged["cost_inputs_changed"] is True
    assert len(merged["fees"]) == 1
    assert merged["fee"]["evidence"] == row["evidence"]
    assert merged["fee"]["amount"] == "100"
    assert not any(fee["logical_fee_key"] == key for fee in fee_service.compose_fee_worklist_rows(merged["fees"], "SEA"))
    result = cost_preview_service.build_saved_cost_data(material_rows(), merged["fees"], {"fx_rmb_to_mxn": 2}, "SEA")
    assert result["summary"]["total_cost_rmb"] == "400.00"
