"""逐费用范围和 Decimal 金额守恒分摊测试。"""

from copy import deepcopy
from decimal import Decimal

import pytest

from overseas_costing.services.fee_allocation_service import allocate_fee


@pytest.mark.parametrize("physical", [None, "", "0", "NaN", "Infinity", "-1"])
def test_incomplete_physical_basis_blocks_the_entire_fee(physical):
    items = [
        {"stable_line_key": "A", "gross_weight_kg": "1", "goods_value": "100"},
        {"stable_line_key": "B", "gross_weight_kg": physical, "goods_value": "300"},
    ]
    result = allocate_fee({"logical_fee_key": "international_sea_freight", "amount_status": "ACTUAL", "amount": "2004", "allocation_basis": "gross_weight"}, items)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "ALLOCATION_BASIS_INCOMPLETE"
    assert result["preferred_basis"] == "gross_weight"
    assert result["allocations"] == {}


@pytest.mark.parametrize(("fee_key", "field", "basis"), [
    ("international_sea_freight", "gross_weight_kg", "gross_weight"),
    ("sea_port_forwarder_surcharge", "gross_weight_kg", "gross_weight"),
    ("international_air_freight", "gross_weight_kg", "gross_weight"),
    ("international_express_fee", "gross_weight_kg", "gross_weight"),
    ("destination_delivery", "gross_weight_kg", "gross_weight"),
])
def test_system_prefers_complete_physical_data_over_saved_basis(fee_key, field, basis):
    result = allocate_fee({"logical_fee_key": fee_key, "amount_status": "ACTUAL", "amount": "100", "allocation_basis": "goods_value"}, [
        {"stable_line_key": "A", field: "1", "goods_value": "300"},
        {"stable_line_key": "B", field: "3", "goods_value": "100"},
    ])
    assert result["basis"] == basis
    assert result["allocations"] == {"A": "25.00", "B": "75.00"}


@pytest.mark.parametrize("revision_field", ["amount_revision", "scope_revision"])
@pytest.mark.parametrize("basis,field", [
    ("volume", "volume_m3"),
    ("chargeable_weight", "chargeable_weight_kg"),
    ("goods_value", "goods_value"),
])
def test_manual_freight_basis_takes_priority_over_new_gross_weight_default(revision_field, basis, field):
    fee = {"logical_fee_key": "international_air_freight", "amount_status": "ACTUAL", "amount": "100",
           "allocation_basis": basis, revision_field: "manual:existing"}
    items = [{"stable_line_key": "A", "gross_weight_kg": "10", field: "1"},
             {"stable_line_key": "B", "gross_weight_kg": "10", field: "3"}]
    original = deepcopy((fee, items))
    for _ in range(2):
        result = allocate_fee(fee, items)
        assert result["basis"] == basis
        assert result["allocations"] == {"A": "25.00", "B": "75.00"}
        assert result["allocated_total"] == "100.00"
    assert (fee, items) == original


def test_zero_amount_needs_no_allocation_measurements():
    result = allocate_fee({"amount_status": "ACTUAL", "amount": "0", "allocation_basis": "volume"}, [{"stable_line_key": "A"}])
    assert result["status"] == "ALLOCATED"
    assert result["allocations"] == {"A": "0.00"}


def test_incomplete_scoped_basis_does_not_switch_to_goods_value():
    items = [{"stable_line_key": key, "goods_value": "1"} for key in ["C", "B", "A", "OUT"]]
    fee = {"amount_status": "ACTUAL", "amount": "1", "allocation_basis": "volume", "scope_type": "ITEMS", "scope_item_keys": ["A", "B", "C"]}
    result = allocate_fee(fee, items)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "ALLOCATION_BASIS_INCOMPLETE"
    assert result["allocations"] == {}


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_fee_amount_is_blocked(amount):
    assert allocate_fee({"amount_status": "ACTUAL", "amount": amount}, [{"stable_line_key": "A", "goods_value": "100"}])["code"] == "FEE_AMOUNT_INVALID"


def test_item_scoped_fee_excludes_unrelated_items() -> None:
    result = allocate_fee(
        fee={
            "logical_fee_key": "PRODUCTION-EXTRA",
            "amount": "90",
            "currency": "CNY",
            "amount_status": "ACTUAL",
            "scope_type": "ITEMS",
            "scope_item_keys": ["P1", "P2"],
            "allocation_basis": "goods_value",
        },
        items=[
            {"stable_line_key": "P1", "goods_value": "100"},
            {"stable_line_key": "P2", "goods_value": "200"},
            {"stable_line_key": "E1", "goods_value": "300"},
        ],
    )

    assert result["allocations"] == {"P1": "30.00", "P2": "60.00", "E1": "0.00"}
    assert result["allocated_total"] == "90.00"


def test_missing_basis_on_one_eligible_item_blocks_entire_fee() -> None:
    result = allocate_fee(
        fee={
            "amount": "100",
            "amount_status": "ACTUAL",
            "scope_type": "ITEMS",
            "scope_item_keys": ["P1", "P2"],
            "allocation_basis": "gross_weight",
        },
        items=[
            {"stable_line_key": "P1", "gross_weight_kg": 10},
            {"stable_line_key": "P2", "gross_weight_kg": ""},
        ],
    )

    assert result["status"] == "BLOCKED"
    assert result["allocations"] == {}


def test_chargeable_weight_does_not_silently_fall_back_to_gross_weight() -> None:
    result = allocate_fee(
        fee={
            "amount": "100",
            "amount_status": "ACTUAL",
            "scope_type": "ALL_ITEMS",
            "allocation_basis": "chargeable_weight",
        },
        items=[{"stable_line_key": "P1", "gross_weight_kg": 10, "chargeable_weight_kg": ""}],
    )

    assert result["status"] == "BLOCKED"
    assert result["code"] == "ALLOCATION_BASIS_INCOMPLETE"


def test_direct_item_fee_does_not_require_an_unrelated_allocation_basis() -> None:
    result = allocate_fee(
        fee={
            "amount": "100",
            "amount_status": "ACTUAL",
            "scope_type": "DIRECT_ITEM",
            "scope_item_keys": ["P1"],
            "allocation_basis": "volume",
        },
        items=[{"stable_line_key": "P1", "volume_m3": ""}],
    )

    assert result["status"] == "ALLOCATED"
    assert result["allocations"] == {"P1": "100.00"}


def test_stable_remainder_preserves_the_fee_amount() -> None:
    result = allocate_fee(
        fee={
            "amount": "1.00",
            "amount_status": "ACTUAL",
            "scope_type": "ALL_ITEMS",
            "allocation_basis": "goods_value",
        },
        items=[
            {"stable_line_key": "C", "goods_value": "1"},
            {"stable_line_key": "A", "goods_value": "1"},
            {"stable_line_key": "B", "goods_value": "1"},
        ],
    )

    assert result["allocations"] == {"A": "0.34", "B": "0.33", "C": "0.33"}
    assert result["allocated_total"] == "1.00"


def test_non_counted_amount_states_do_not_enter_fee_pool() -> None:
    for status in ("MISSING", "NOT_INCURRED", "INCLUDED"):
        result = allocate_fee(
            fee={
                "amount": "100",
                "amount_status": status,
                "scope_type": "ALL_ITEMS",
                "allocation_basis": "goods_value",
            },
            items=[{"stable_line_key": "P1", "goods_value": "100"}],
        )
        assert result["status"] == "NOT_COUNTED"
        assert result["allocated_total"] == "0.00"


def test_direct_item_scope_requires_exactly_one_item() -> None:
    result = allocate_fee(
        fee={
            "amount": "100",
            "amount_status": "ACTUAL",
            "scope_type": "DIRECT_ITEM",
            "scope_item_keys": ["P1", "P2"],
            "allocation_basis": "goods_value",
        },
        items=[
            {"stable_line_key": "P1", "goods_value": "100"},
            {"stable_line_key": "P2", "goods_value": "100"},
        ],
    )

    assert result["status"] == "BLOCKED"
    assert result["code"] == "DIRECT_ITEM_SCOPE_INVALID"
