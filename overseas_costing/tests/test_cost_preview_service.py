"""只读综合成本试算测试。"""

from copy import deepcopy
from decimal import Decimal
import json

import pytest

from overseas_costing.services import cost_preview_service, fx_rate_service
from overseas_costing.services.cost_preview_service import preview_comprehensive_cost_data


@pytest.fixture(autouse=True)
def use_plain_messages(monkeypatch):
    monkeypatch.setattr(cost_preview_service, "_", lambda message: message)
    monkeypatch.setattr(fx_rate_service, "_", lambda message: message)


def test_express_default_zero_fees_count_as_estimates_without_fx_or_physical_values():
    from overseas_costing.services.fee_service import build_default_fee_templates
    fees = build_default_fee_templates('EXPRESS')
    items = _items()
    for row in items:
        row.update(gross_weight_kg=0, volume_m3=0, chargeable_weight_kg=0)
    before = deepcopy((items, fees))
    result = preview_comprehensive_cost_data(items, fees, {})
    assert {fee['fee_key'] for fee in result['included_fees']} == {'express_surcharge', 'destination_delivery'}
    assert all(fee['amount_status'] == 'ESTIMATED' and fee['amount_rmb'] == '0.00' for fee in result['included_fees'])
    assert {fee['fee_key'] for fee in result['excluded_fees']} == {'international_express_fee', 'customs_clearance_fee', 'import_tax'}
    assert result['summary']['total_cost_rmb'] == '150.00'
    assert result['summary']['estimated_fee_count'] == 2
    assert result['summary']['is_complete'] is False
    assert (items, fees) == before
    delivery = next(fee for fee in fees if fee['logical_fee_key'] == 'destination_delivery')
    delivery.update(amount='100', amount_status='ACTUAL')
    result = preview_comprehensive_cost_data(items, fees, {})
    blocked = next(fee for fee in result['excluded_fees'] if fee['fee_key'] == 'destination_delivery')
    assert blocked['reason_code'] == 'FX_RATE_MISSING'


def test_saved_physical_fees_are_blocked_without_packing_data_while_other_fees_continue():
    from overseas_costing.services.fee_service import build_default_fee_templates

    items = _items()
    for index, row in enumerate(items):
        row.update(goods_value=["60400", "26000"][index], volume_m3=0, gross_weight_kg=0)
    fees = build_default_fee_templates("SEA")
    fees.insert(1, dict(name="SAVED-SURCHARGE", logical_fee_key="sea_port_forwarder_surcharge",
                        expense_category="港杂/货代附加费", allocation_basis="volume", currency="RMB"))
    for fee, amount in zip(fees, ["2004", "3000", "1000", "0", "0"]):
        fee.update(amount_status="ACTUAL", amount=amount, currency="RMB")
    before = deepcopy((items, fees))
    result = preview_comprehensive_cost_data(items, fees, {})
    assert result["summary"]["total_cost_rmb"] == "74400.00"
    assert result["summary"]["allocated_fees_rmb"] == "1000.00"
    assert result["summary"]["included_fee_count"] == 3
    assert {row["fee_key"] for row in result["excluded_fees"]} == {
        "international_sea_freight",
        "sea_port_forwarder_surcharge",
    }
    assert {row["reason_code"] for row in result["excluded_fees"]} == {"ALLOCATION_DENOMINATOR_ZERO"}
    assert (items, fees) == before


def test_zero_foreign_fee_needs_no_fx_but_unknown_amount_is_still_excluded():
    result = preview_comprehensive_cost_data(_items(), [
        {"logical_fee_key": "zero", "amount_status": "ACTUAL", "amount": "0", "currency": "USD", "allocation_basis": "chargeable_weight"},
        {"logical_fee_key": "missing", "amount_status": "MISSING", "amount": "", "currency": "RMB"},
    ], {})
    assert result["summary"]["included_fee_count"] == 1
    assert result["excluded_fees"][0]["reason_code"] == "AMOUNT_MISSING"
    assert len(result["excluded_fees"]) == 1


@pytest.mark.parametrize(("currency", "amount", "expected"), [("RMB", "10", "160.00"), ("USD", "10", "220.00"), ("MXN", "25", "160.00")])
def test_supported_currencies_convert_before_automatic_allocation(currency, amount, expected):
    result = preview_comprehensive_cost_data(_items(), [{"amount_status": "ACTUAL", "amount": amount, "currency": currency, "allocation_basis": "gross_weight"}], {"fx_usd_to_rmb": "7", "fx_rmb_to_mxn": "2.5"})
    assert result["summary"]["total_cost_rmb"] == expected


def _items():
    return [
        {
            "name": "ITEM-A",
            "stable_line_key": "A",
            "material_code": "SKU-1",
            "product_name": "A",
            "purchase_uom": "件",
            "unit_price_uom": "件",
            "quantity": "10",
            "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
            "shipped_uom": "件",
            "goods_value": "100",
            "gross_weight_kg": "20",
            "volume_m3": "1",
        },
        {
            "name": "ITEM-B",
            "stable_line_key": "B",
            "material_code": "SKU-1",
            "product_name": "B",
            "purchase_uom": "桶",
            "unit_price_uom": "kg",
            "quantity": "10",
            "actual_shipped_qty": "5",
            "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
            "shipped_uom": "桶",
            "goods_value": "100",
            "gross_weight_kg": "30",
            "volume_m3": "1",
        },
    ]


def test_formal_cost_inputs_preserve_purchase_value_identity_fields() -> None:
    from overseas_costing.services.purchase_value_evidence import META_KEY
    from overseas_costing.services.material_input_service import present_material_row

    row = {
        **_items()[0],
        "material_code": "FL004195",
        "spec_model": "PIGMENT-RED",
        "quantity": "4",
        "actual_shipped_qty": "4",
        "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
        "purchase_uom": "kg",
        "shipped_uom": "kg",
        "goods_value": 0,
        "extra_json": json.dumps({
            META_KEY: {
                "amount_rmb": "800",
                "quantity": "4",
                "uom": "kg",
                "material_code": "FL004195",
                "spec_model": "PIGMENT-RED",
                "source_refs": [{"document_id": "DOC-1", "row": 1}],
            }
        }),
    }
    formal_input = {
        field: row.get(field)
        for field in cost_preview_service.COST_INPUT_FIELDS
    }

    presented = present_material_row(formal_input)

    assert formal_input["spec_model"] == "PIGMENT-RED"
    assert presented["shipment_value_rmb"] == "800"
    assert presented["shipment_valuation"]["error"] == ""


def test_spec_model_query_fix_does_not_invalidate_legacy_snapshot_hash() -> None:
    item = _items()[0]
    legacy = {key: value for key, value in item.items() if key != "spec_model"}
    current = {**legacy, "spec_model": "PIGMENT-RED"}

    assert cost_preview_service.cost_input_hash(
        [legacy], [], {}, "SEA"
    ) == cost_preview_service.cost_input_hash([current], [], {}, "SEA")


def test_preview_conserves_direct_and_allocated_fees_with_dual_unit_output() -> None:
    result = preview_comprehensive_cost_data(
        _items(),
        [
            {
                "logical_fee_key": "freight",
                "expense_category": "国际海运费",
                "amount_status": "ACTUAL",
                "amount": "100",
                "currency": "RMB",
                "allocation_basis": "goods_value",
                "scope_type": "ALL_ITEMS",
            },
            {
                "logical_fee_key": "inspection",
                "expense_category": "检查费",
                "amount_status": "ACTUAL",
                "amount": "20",
                "currency": "RMB",
                "allocation_basis": "gross_weight",
                "scope_type": "DIRECT_ITEM",
                "scope_item_keys": ["A"],
            },
        ],
        {"fx_usd_to_rmb": "7", "fx_rmb_to_mxn": "2.5"},
    )

    assert result["summary"] == {
        "purchase_goods_value_rmb": "150.00",
        "direct_fees_rmb": "20.00",
        "allocated_fees_rmb": "100.00",
        "total_cost_rmb": "270.00",
        "included_fee_count": 2,
        "excluded_fee_count": 0,
        "is_complete": True,
    }
    assert result["items"][0]["total_cost_rmb"] == "186.67"
    assert result["items"][0]["shipping_unit_price"] == {
        "amount_rmb": "10.000000",
        "uom": "件",
    }
    assert result["items"][0]["shipping_unit_cost"] == {"amount_rmb": "18.667000", "uom": "件"}
    assert result["items"][0]["purchase_pricing_unit_cost"] == {
        "amount_rmb": "18.667000",
        "uom": "件",
    }
    assert result["items"][1]["goods_value_rmb"] == "50.00"
    assert result["items"][1]["total_cost_rmb"] == "83.33"
    assert result["items"][1]["shipping_unit_price"] == {
        "amount_rmb": "10.000000",
        "uom": "桶",
    }
    assert result["items"][1]["shipping_unit_cost"] == {"amount_rmb": "16.666000", "uom": "桶"}
    assert result["items"][1]["purchase_pricing_unit_cost"] is None


def test_shipping_unit_price_distinguishes_explicit_zero_from_missing_goods_value() -> None:
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    explicit_zero = {
        "name": "ITEM-ZERO",
        "stable_line_key": "ZERO",
        "material_code": "SKU-ZERO",
        "product_name": "Zero",
        "quantity": "4",
        "purchase_uom": "个",
        "unit_price_uom": "个",
        "actual_shipped_qty": "4",
        "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
        "shipped_uom": "个",
        "goods_value": "99",
    }
    explicit_zero["extra_json"] = json.dumps(
        {
            "manual_shipment_valuation": build_manual_shipment_valuation(
                explicit_zero,
                "0",
                actor="tester@example.com",
                reason="确认本次发货为零货值",
            )
        }
    )
    missing_value = {
        "name": "ITEM-MISSING",
        "stable_line_key": "MISSING",
        "material_code": "SKU-MISSING",
        "product_name": "Missing",
        "quantity": "5",
        "purchase_uom": "个",
        "unit_price_uom": "个",
        "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
        "shipped_uom": "个",
        "goods_value": "",
    }

    result = preview_comprehensive_cost_data([explicit_zero, missing_value], [], {})

    assert result["items"][0]["shipping_unit_price"] == {
        "amount_rmb": "0.000000",
        "uom": "个",
    }
    assert result["items"][1]["shipping_unit_price"] is None


def test_shipping_unit_prices_are_unavailable_without_effective_quantity_or_unit() -> None:
    item = {
        "name": "ITEM-NO-QUANTITY",
        "stable_line_key": "NO-QUANTITY",
        "material_code": "SKU-NO-QUANTITY",
        "product_name": "No quantity",
        "quantity": "",
        "purchase_uom": "",
        "unit_price_uom": "",
        "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
        "shipped_uom": "",
        "goods_value": "100",
    }

    result = preview_comprehensive_cost_data([item], [], {})

    assert result["items"][0]["shipping_unit_price"] is None
    assert result["items"][0]["shipping_unit_cost"] is None
    quantity_reason = next(
        reason
        for reason in result["incomplete_reasons"]
        if reason["reason_code"] == "SHIPPING_UNIT_REQUIRED"
    )
    assert quantity_reason["message"] == "发货数量或单位缺失，无法计算单价和综合单价。"


def test_old_saved_snapshot_unit_price_is_enriched_without_rewriting_history() -> None:
    snapshot = {
        "calculation_schema": 2,
        "comprehensive_cost": {
            "items": [
                {
                    "name": "ITEM-1",
                    "goods_value_rmb": "2067.00",
                    "total_cost_rmb": "4795.57",
                    "shipping_unit_cost": {"amount_rmb": "2.820924", "uom": "个"},
                },
                {
                    "name": "ITEM-2",
                    "goods_value_rmb": "30.00",
                    "shipping_unit_price": {"amount_rmb": "3.000000", "uom": "件"},
                },
                {
                    "name": "ITEM-MISSING",
                    "goods_value_rmb": "0.00",
                    "valuation_source": {"status": "missing", "error": "GOODS_VALUE_MISSING"},
                    "shipping_unit_cost": {"amount_rmb": "10.000000", "uom": "个"},
                },
                {
                    "name": "ITEM-ZERO",
                    "goods_value_rmb": "0.00",
                    "valuation_source": {"amount_rmb": "0", "status": "manual", "error": ""},
                    "shipping_unit_cost": {"amount_rmb": "2.000000", "uom": "个"},
                },
            ]
        },
    }
    rows = [
        {
            "name": "ITEM-1",
            "derived_json": json.dumps(
                {"shipping_quantity": "1700", "shipping_unit_cost": {"uom": "个"}}
            ),
        },
        {"name": "ITEM-MISSING", "derived_json": json.dumps({"shipping_quantity": "5"})},
        {"name": "ITEM-ZERO", "derived_json": json.dumps({"shipping_quantity": "5"})},
    ]
    original_snapshot = deepcopy(snapshot)
    original_rows = deepcopy(rows)

    enriched = cost_preview_service.enrich_saved_unit_prices(snapshot, rows)

    assert enriched["comprehensive_cost"]["items"][0]["shipping_unit_price"] == {
        "amount_rmb": "1.215882",
        "uom": "个",
    }
    assert enriched["comprehensive_cost"]["items"][1]["shipping_unit_price"] == {
        "amount_rmb": "3.000000",
        "uom": "件",
    }
    assert "shipping_unit_price" not in enriched["comprehensive_cost"]["items"][2]
    assert enriched["comprehensive_cost"]["items"][3]["shipping_unit_price"] == {
        "amount_rmb": "0.000000",
        "uom": "个",
    }
    assert snapshot == original_snapshot
    assert rows == original_rows


def test_partial_shipment_unit_prices_use_unrounded_goods_value() -> None:
    item = {
        "name": "ITEM-1",
        "stable_line_key": "ITEM-1",
        "material_code": "CW000214",
        "quantity": "1700",
        "purchase_uom": "个",
        "actual_shipped_qty": "1500",
        "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
        "shipped_uom": "个",
        "goods_value": "2067",
    }

    result = preview_comprehensive_cost_data([item], [], {})

    assert result["items"][0]["goods_value_rmb"] == "1823.82"
    assert result["items"][0]["shipping_unit_price"] == {
        "amount_rmb": "1.215882",
        "uom": "个",
    }
    assert result["items"][0]["shipping_unit_cost"] == {
        "amount_rmb": "1.215882",
        "uom": "个",
    }


def test_preview_lists_missing_fx_and_unknown_amount_but_counts_complete_estimate() -> None:
    result = preview_comprehensive_cost_data(
        _items(),
        [
            {
                "logical_fee_key": "missing",
                "amount_status": "MISSING",
                "currency": "RMB",
                "allocation_basis": "goods_value",
            },
            {
                "logical_fee_key": "usd",
                "amount_status": "ACTUAL",
                "amount": "10",
                "currency": "USD",
                "allocation_basis": "goods_value",
            },
            {
                "logical_fee_key": "volume",
                "amount_status": "ESTIMATED",
                "amount": "50",
                "currency": "RMB",
                "allocation_basis": "gross_weight",
            },
        ],
        {"fx_usd_to_rmb": "", "fx_rmb_to_mxn": "2.5"},
    )

    assert result["summary"]["total_cost_rmb"] == "200.00"
    assert result["summary"]["excluded_fee_count"] == 2
    assert result["summary"]["estimated_fee_count"] == 1
    assert result["summary"]["is_complete"] is False
    assert {row["reason_code"] for row in result["excluded_fees"]} == {
        "AMOUNT_MISSING",
        "FX_RATE_MISSING",
    }


def test_preview_excludes_fee_when_both_physical_and_goods_values_are_incomplete():
    items = _items()
    items[0].update(goods_value="", volume_m3="")
    result = preview_comprehensive_cost_data(items, [{"amount_status": "ACTUAL", "amount": "100", "allocation_basis": "volume"}], {})
    assert result["summary"]["is_complete"] is False
    assert result["summary"]["allocated_fees_rmb"] == "0.00"
    assert result["excluded_fees"][0]["reason_code"] == "ALLOCATION_BASIS_INCOMPLETE"


def test_complete_confirmed_customs_components_do_not_require_generic_basis():
    items = _items()
    for row in items:
        row.update(goods_value=0, gross_weight_kg=0, volume_m3=0, chargeable_weight_kg=0)
    fee = {
        "name": "TAX-RULE",
        "logical_fee_key": "import_tax",
        "expense_category": "进口税费",
        "amount_status": "ACTUAL",
        "amount": "100",
        "currency": "RMB",
        "allocation_basis": "volume",
    }
    components = [
        {"fee_rule": "TAX-RULE", "stable_line_key": "A", "amount_rmb": "30", "status": "CONFIRMED", "is_active": 1, "cost_effect": "COST"},
        {"fee_rule": "TAX-RULE", "stable_line_key": "B", "amount_rmb": "70", "status": "CONFIRMED", "is_active": 1, "cost_effect": "COST"},
    ]

    result = preview_comprehensive_cost_data(items, [fee], {}, fee_components=components)

    assert result["excluded_fees"] == []
    assert result["included_fees"][0]["allocations"] == {"A": "30.00", "B": "70.00"}
    assert result["included_fees"][0]["component_source"] == "EVIDENCE_SKU_COMPONENT"


def test_preview_does_not_mutate_input_or_block_on_missing_project() -> None:
    items = _items()
    for row in items:
        row["project_collection"] = ""
    fees = [
        {
            "logical_fee_key": "delivery",
            "amount_status": "ACTUAL",
            "amount": "10",
            "currency": "RMB",
            "allocation_basis": "gross_weight",
        }
    ]
    before = (deepcopy(items), deepcopy(fees))

    result = preview_comprehensive_cost_data(items, fees, {})

    assert result["summary"]["is_complete"] is True
    assert (items, fees) == before
    assert all("project_collection" not in reason.get("field", "") for reason in result["incomplete_reasons"])


def test_preview_rejects_a_version_from_another_batch(monkeypatch) -> None:
    class FakeDb:
        @staticmethod
        def get_value(doctype, name, fieldname, **_kwargs):
            if doctype == "Overseas Cost Version" and fieldname == "batch":
                return "OTHER-BATCH"
            return None

    class FakeFrappe:
        db = FakeDb()

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe())

    with pytest.raises(ValueError, match="不属于当前批次"):
        cost_preview_service.preview_comprehensive_cost("BATCH-1", "VERSION-OTHER")


def test_empty_materials_cannot_be_a_complete_cost():
    result = preview_comprehensive_cost_data([], [{"amount_status": "NOT_INCURRED"}], {})
    assert result["summary"]["is_complete"] is False
    assert result["incomplete_reasons"][0]["reason_code"] == "MATERIAL_ITEMS_REQUIRED"


def test_bare_legacy_zero_blocks_preview_but_structured_automatic_zero_is_explicit():
    bare = {**_items()[0], "name": "ITEM-BARE", "stable_line_key": "BARE",
            "goods_value": 0, "extra_json": "{}"}
    automatic = {**_items()[0], "name": "ITEM-AUTO", "stable_line_key": "AUTO",
                 "goods_value": 0, "extra_json": json.dumps({"shipment_valuation": {
                     "amount_rmb": "0", "currency": "RMB", "quantity": "10", "uom": "件",
                     "method": "SYSTEM_EXCEL", "status": "automatic", "error": "",
                 }})}

    result = preview_comprehensive_cost_data([bare, automatic], [], {})

    assert result["summary"]["is_complete"] is False
    missing = [reason for reason in result["incomplete_reasons"]
               if reason.get("reason_code") == "GOODS_VALUE_MISSING"]
    assert [reason["item_key"] for reason in missing] == ["BARE"]
    assert result["items"][0]["valuation_source"]["status"] == "missing"
    assert result["items"][1]["valuation_source"]["status"] == "automatic"


def _import_tax_fee(amount="100", currency="RMB"):
    return {
        "name": "F1",
        "logical_fee_key": "import_tax",
        "expense_category": "进口税费",
        "amount_status": "ACTUAL",
        "amount": amount,
        "currency": currency,
        "allocation_basis": "goods_value",
        "scope_type": "ALL_ITEMS",
        "is_enabled": 1,
    }


def test_ledger_only_components_never_enter_cost() -> None:
    result = preview_comprehensive_cost_data(
        _items(),
        [_import_tax_fee()],
        {},
        fee_components=[
            {
                "fee_rule": "F1",
                "item": "ITEM-A",
                "stable_line_key": "A",
                "amount_rmb": "30",
                "status": "CONFIRMED",
                "is_active": 1,
                "cost_effect": "LEDGER_ONLY",
            }
        ],
    )

    fee = result["included_fees"][0]
    assert fee["component_allocations"] == {}
    assert sum(Decimal(value) for value in fee["allocations"].values()) == Decimal(
        "100.00"
    )


def test_new_components_suppress_legacy_tax_fields_and_allocate_only_residual() -> None:
    items = _items()
    items[0]["igi_amount"] = "40"
    items[1]["igi_amount"] = "60"

    result = preview_comprehensive_cost_data(
        items,
        [_import_tax_fee("120")],
        {},
        fee_components=[
            {
                "fee_rule": "F1",
                "item": "ITEM-A",
                "stable_line_key": "A",
                "amount_rmb": "50",
                "status": "CONFIRMED",
                "is_active": 1,
                "cost_effect": "COST",
            }
        ],
    )

    fee = result["included_fees"][0]
    assert fee["component_allocations"] == {"A": "50.00"}
    assert fee["residual_amount_rmb"] == "70.00"
    assert fee["allocations"] == {"A": "96.67", "B": "23.33"}


def test_legacy_tax_fields_are_compatibility_components_only_without_new_rows() -> None:
    items = _items()
    items[0]["igi_amount"] = "20"
    items[1]["igi_amount"] = "10"

    result = preview_comprehensive_cost_data(
        items,
        [_import_tax_fee("40", "MXN")],
        {"fx_rmb_to_mxn": "2"},
    )

    fee = result["included_fees"][0]
    assert fee["component_source"] == "LEGACY_ITEM_FIELDS"
    assert fee["component_allocations"] == {"A": "10.00", "B": "5.00"}
    assert fee["residual_amount_rmb"] == "5.00"
    assert fee["allocations"] == {"A": "13.34", "B": "6.66"}


def test_component_without_rmb_conversion_blocks_fee_with_chinese_fx_reason() -> None:
    result = preview_comprehensive_cost_data(
        _items(),
        [_import_tax_fee("100", "MXN")],
        {},
        fee_components=[
            {
                "fee_rule": "F1",
                "item": "ITEM-A",
                "stable_line_key": "A",
                "currency": "MXN",
                "original_amount": "100",
                "amount_rmb": None,
                "status": "CONFIRMED",
                "is_active": 1,
                "cost_effect": "COST",
            }
        ],
    )

    assert result["excluded_fees"][0]["reason_code"] == "EVIDENCE_COMPONENT_FX_MISSING"
    reason = next(
        row
        for row in result["incomplete_reasons"]
        if row["reason_code"] == "EVIDENCE_COMPONENT_FX_MISSING"
    )
    assert "汇率" in reason["message"]


def test_cost_input_hash_sorts_components_but_changes_when_a_component_changes() -> None:
    components = [
        {"name": "C2", "amount_rmb": "20", "cost_effect": "COST"},
        {"name": "C1", "amount_rmb": "10", "cost_effect": "COST"},
    ]
    first = cost_preview_service.cost_input_hash([], [], {}, "AIR", components)
    reordered = cost_preview_service.cost_input_hash(
        [], [], {}, "AIR", list(reversed(components))
    )
    changed = cost_preview_service.cost_input_hash(
        [], [], {}, "AIR", [{**components[0], "amount_rmb": "21"}, components[1]]
    )

    assert first == reordered
    assert first != changed


class AutoFxCalculationRepository:
    def __init__(self, *, persist_error=None):
        self.persist_error = persist_error
        self.prepared = 0
        self.persisted = 0
        self.saved = None
        self.commits = 0
        self.rollbacks = 0
        self.items = _items()

    def prepare_fx_resolution(self, batch_name, version_name):
        assert batch_name == "B1" and version_name == "V1"
        self.prepared += 1
        return {
            "ok": True,
            "batch_name": "B1",
            "version_name": "V1",
            "calculation_date": "2026-09-20",
            "fx_usd_to_rmb": 6.71,
            "fx_rmb_to_mxn": 2.564103,
            "rates": {},
            "blocking_errors": [],
        }

    def lock_and_load(self, batch_name, version_name, **_kwargs):
        return (
            {
                "batch": batch_name,
                "version": version_name,
                "current_version": version_name,
                "version_status": "Active",
                "confirm_status": "Pending",
                "is_locked": 0,
                "transport_mode": "AIR",
            },
            deepcopy(self.items),
            [{
                "name": "F1", "logical_fee_key": "international_air_freight",
                "expense_category": "国际空运费", "amount_status": "ACTUAL", "amount": "100",
                "currency": "RMB", "allocation_basis": "goods_value", "scope_type": "ALL_ITEMS",
            }],
            {"fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0},
        )

    def load_fee_components(self, _context):
        return []

    def persist_fx_resolution(self, context, resolution):
        assert context["version"] == "V1"
        self.persisted += 1
        if self.persist_error:
            raise self.persist_error
        return {
            "fx_context": {
                "fx_usd_to_rmb": resolution["fx_usd_to_rmb"],
                "fx_rmb_to_mxn": resolution["fx_rmb_to_mxn"],
            },
            "fx_resolution": dict(resolution),
            "changed_fields": ["fx_usd_to_rmb", "fx_rmb_to_mxn"],
        }

    def assert_unchanged(self, _context):
        return None

    def save(self, _context, result):
        self.saved = deepcopy(result)
        return "m2"

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_direct_calculation_persists_resolved_fx_before_saving_non_null_mxn_fields() -> None:
    repository = AutoFxCalculationRepository()

    result = cost_preview_service.calculate_comprehensive_cost(
        "B1", "V1", repository=repository,
    )

    assert repository.prepared == 1
    assert repository.persisted == 1
    assert repository.commits == 1
    assert result["fx_resolution"]["calculation_date"] == "2026-09-20"
    assert all(row["freight_alloc_mxn"] is not None for row in repository.saved["item_updates"])
    assert all(row["total_logistics_mxn"] is not None for row in repository.saved["item_updates"])


def test_direct_calculation_rolls_back_when_fx_persistence_fails() -> None:
    repository = AutoFxCalculationRepository(persist_error=ValueError("当日汇率已变化"))

    with pytest.raises(ValueError, match="当日汇率已变化"):
        cost_preview_service.calculate_comprehensive_cost("B1", "V1", repository=repository)

    assert repository.saved is None
    assert repository.commits == 0
    assert repository.rollbacks == 1


def test_direct_calculation_rejects_missing_purchase_values_before_any_write() -> None:
    repository = AutoFxCalculationRepository()
    repository.items[1]["goods_value"] = ""

    with pytest.raises(
        ValueError,
        match="还有 1 行本次发货货值缺失或失效，请先补齐后再试算",
    ):
        cost_preview_service.calculate_comprehensive_cost("B1", "V1", repository=repository)

    assert repository.persisted == 0
    assert repository.saved is None
    assert repository.commits == 0
    assert repository.rollbacks == 1


def test_persistable_item_updates_reject_null_mxn_before_database_write() -> None:
    with pytest.raises(ValueError, match="人民币兑比索汇率"):
        cost_preview_service.assert_persistable_item_updates(
            {"item_updates": [{"name": "ITEM-1", "freight_alloc_mxn": None, "total_logistics_mxn": None}]}
        )


def test_frappe_cost_repository_prepares_payment_day_fx_without_writing(monkeypatch) -> None:
    from overseas_costing.services import batch_service

    calls = []

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            calls.append((doctype, name, fields, as_dict))
            if doctype == "Overseas Cost Batch":
                return "V1" if fields == "current_version" else {"source_finished_at": "2026-09-19"}
            return {
                "name": "V1", "batch": "B1", "status": "Active",
                "fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "extra_json": "{}",
            }

    class FakeFrappe:
        db = FakeDB()

        @staticmethod
        def get_all(doctype, **_kwargs):
            assert doctype == "Overseas Cost Attachment"
            return [{"parse_result_json": '{"header":{"payment_date":"2026-09-20","payment_date_source":"explicit_label"}}'}]

    captured = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True, "calculation_date": kwargs["calculation_date"],
            "fx_usd_to_rmb": 6.71, "fx_rmb_to_mxn": 2.564103,
            "rates": {}, "blocking_errors": [],
        }

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    monkeypatch.setattr(batch_service, "_resolve_batch_name", lambda name: name)
    monkeypatch.setattr(cost_preview_service.fx_rate_service, "resolve_costing_fx", fake_resolve)

    result = cost_preview_service.FrappeCostRepository().prepare_fx_resolution("B1", None)

    assert captured["calculation_date"] == "2026-09-20"
    assert captured["version_fx"]["fx_rmb_to_mxn"] == 0
    assert result["batch_name"] == "B1"
    assert result["version_name"] == "V1"
    assert result["date_source"] == "payment_date"
    assert all(call[0] != "Currency Exchange" for call in calls)


def test_costing_fx_date_blocks_missing_and_conflicting_payment_dates(monkeypatch) -> None:
    monkeypatch.setattr(cost_preview_service, "_", lambda message: message)
    class FakeFrappe:
        class db:
            @staticmethod
            def get_value(doctype, *_args, **_kwargs):
                if doctype == "Overseas Cost Version":
                    return {"extra_json": '{"costing_fx_snapshot":{"date_source":"payment_date","calculation_date":"2026-10-30"}}'}
                return {"source_finished_at": ""}

        rows = []

        @classmethod
        def get_all(cls, *_args, **_kwargs):
            return cls.rows

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    assert cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1")["ok"] is False
    assert cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1", "V1")["ok"] is False

    FakeFrappe.rows = [
        {"parse_result_json": '{"header":{"payment_date":"2026-10-29","payment_date_source":"explicit_label"}}'},
        {"parse_result_json": '{"header":{"payment_date":"2026-10-30","payment_date_source":"explicit_label"}}'},
    ]
    result = cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1")
    assert result["ok"] is False
    assert result["date_source"] == "ambiguous"

    FakeFrappe.rows = []
    assert cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1", "V1")["ok"] is False

    FakeFrappe.rows = [{"parse_result_json": '{"header":{"payment_date":"2026-10-30"}}'}]
    assert cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1")["date_source"] == "unverified"


@pytest.mark.parametrize(("approval_status", "approval_result"), [
    ("COMPLETED", "agree"),
    ("RUNNING", "agree"),
    ("RUNNING", ""),
])
def test_costing_fx_date_uses_valid_logistics_start_for_legacy_batch(monkeypatch, approval_status, approval_result) -> None:
    class FakeFrappe:
        rows = [{"parse_result_json": '{"header":{"payment_date":"2026-04-01"}}'}]
        batch = {
            "source_type": "oa_logistics",
            "source_created_at": "2026-01-29 10:20:00",
            "source_instance_id": "OA-INSTANCE-1",
            "source_approval_status": approval_status,
            "extra_json": json.dumps({"approval_result": approval_result, "excluded": False}),
        }

        class db:
            @staticmethod
            def get_value(_doctype, _name, _fields, **_kwargs):
                return FakeFrappe.batch

        @classmethod
        def get_all(cls, *_args, **_kwargs):
            return cls.rows

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    repo = cost_preview_service.FrappeCostRepository
    if approval_status == "RUNNING":
        assert repo.costing_fx_date_context("B1")["ok"] is False
    context = repo.costing_fx_date_context("B1", allow_running_logistics=True)
    assert context["normalized_date"] == "2026-01-29"
    assert context["date_source"] == "oa_logistics_started_at"
    assert context["date_source_ref"] == "OA-INSTANCE-1"
    assert context["is_estimated_rate"] is True
    assert "未经核实" in context["date_warning"]

    FakeFrappe.rows = [{"parse_result_json": '{"header":{"payment_date":"2026-04-01","payment_date_source":"explicit_label"}}'}]
    context = repo.costing_fx_date_context("B1", allow_running_logistics=True)
    assert context["normalized_date"] == "2026-04-01"
    assert context["date_source"] == "payment_date"

    FakeFrappe.rows = []
    if approval_status == "RUNNING":
        from types import SimpleNamespace
        from overseas_costing.services.logistics_settlement import runtime
        mapping = [{"source_id": "S1"}]
        archive = {"kind": "logistics", "instance": "OA-INSTANCE-1", "invalid": True}
        monkeypatch.setattr(runtime, "installed", lambda: True)
        monkeypatch.setattr(runtime, "store", lambda: SimpleNamespace(
            find=lambda *_a, **_kw: mapping,
            get=lambda *_a, **_kw: archive,
        ))
        assert repo.costing_fx_date_context("B1", allow_running_logistics=True)["ok"] is False
        archive["invalid"] = False
        assert repo.costing_fx_date_context("B1", allow_running_logistics=True)["ok"] is True
    FakeFrappe.batch = {**FakeFrappe.batch, "source_approval_status": "REJECTED"}
    assert repo.costing_fx_date_context("B1", allow_running_logistics=True)["ok"] is False

    FakeFrappe.batch = {
        **FakeFrappe.batch, "source_approval_status": "RUNNING",
        "extra_json": '{"approval_result":"refuse"}',
    }
    assert repo.costing_fx_date_context("B1", allow_running_logistics=True)["ok"] is False


def test_costing_fx_date_uses_current_locked_approval_and_rejects_source_change(monkeypatch) -> None:
    reads = []

    class FakeFrappe:
        session = type("Session", (), {"user": "test@example.com"})()

        class db:
            @staticmethod
            def sql(query, *_args, **_kwargs):
                reads.append(query)
                return []

            @staticmethod
            def get_value(_doctype, _name, _fields, **kwargs):
                reads.append(kwargs)
                return {
                    "source_type": "oa_logistics",
                    "source_created_at": "2026-01-29 10:20:00",
                    "source_instance_id": "OA-INSTANCE-NEW",
                    "source_approval_status": "COMPLETED",
                    "extra_json": '{"approval_result":"agree"}',
                }

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    current = cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1", lock=True)
    assert current["date_source_ref"] == "OA-INSTANCE-NEW"
    assert reads[0].endswith("FOR UPDATE")
    assert reads[1]["for_update"] is True

    with pytest.raises(ValueError, match="来源审批已变化"):
        cost_preview_service.FrappeCostRepository().persist_fx_resolution(
            {"batch": "B1", "version": "V1"},
            {"batch_name": "B1", "version_name": "V1", "date_source": current["date_source"],
             "date_source_ref": "OA-INSTANCE-OLD"},
        )


def test_costing_fx_date_checks_attachments_after_first_page(monkeypatch) -> None:
    monkeypatch.setattr(cost_preview_service, "_", lambda message: message)
    rows = [{"parse_result_json": '{"header":{"payment_date":"2026-10-29","payment_date_source":"explicit_label"}}'}] * 200
    rows.append({"parse_result_json": '{"header":{"payment_date":"2026-10-30","payment_date_source":"explicit_label"}}'})

    class FakeFrappe:
        @staticmethod
        def get_all(_doctype, **kwargs):
            start = kwargs["limit_start"]
            return rows[start:start + kwargs["limit_page_length"]]

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    result = cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1")
    assert result["ok"] is False
    assert result["date_source"] == "ambiguous"


def test_costing_fx_date_uses_locked_current_read_before_saving(monkeypatch) -> None:
    queries = []

    class FakeFrappe:
        class db:
            @staticmethod
            def sql(query, values, as_dict=False):
                queries.append((query, values, as_dict))
                return [
                    {"parse_result_json": '{"header":{"payment_date":"2026-10-29","payment_date_source":"explicit_label"}}'},
                    {"parse_result_json": '{"header":{"payment_date":"2026-10-30","payment_date_source":"explicit_label"}}'},
                ]

        @staticmethod
        def get_all(*_args, **_kwargs):
            raise AssertionError("locked check must use a current read")

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    result = cost_preview_service.FrappeCostRepository.costing_fx_date_context("B1", lock=True)
    assert result["date_source"] == "ambiguous"
    assert "FOR UPDATE" in queries[0][0]
    assert queries[0][1][0] == "B1"


def test_read_only_preview_uses_resolved_fx_and_returns_provenance(monkeypatch) -> None:
    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            if doctype == "Overseas Cost Batch" and fields == "current_version":
                return "V1"
            if doctype == "Overseas Cost Version" and fields == "batch":
                return "B1"
            if doctype == "Overseas Cost Batch" and fields == "transport_mode":
                return "AIR"
            if doctype == "Overseas Cost Version" and as_dict:
                return {"fx_usd_to_rmb": 0, "fx_rmb_to_mxn": 0, "extra_json": "{}"}
            raise AssertionError((doctype, name, fields, as_dict))

    class FakeFrappe:
        db = FakeDB()
        utils = type("Utils", (), {"nowdate": staticmethod(lambda: "2026-09-20")})

        @staticmethod
        def get_all(doctype, **_kwargs):
            if doctype == "Overseas Cost Item":
                return _items()
            if doctype == "Overseas Cost Fee SKU Component":
                return []
            raise AssertionError(doctype)

    resolution = {
        "ok": True, "calculation_date": "2026-09-20",
        "fx_usd_to_rmb": 6.71, "fx_rmb_to_mxn": 2.564103,
        "rates": {
            "USD": {"source": "fx_api", "rate_date": "2026-09-20", "cny_per_unit": 6.71},
            "MXN": {"source": "fx_api", "rate_date": "2026-09-20", "cny_per_unit": 0.39},
        },
        "blocking_errors": [], "is_estimated": False,
    }
    fee = {
        "name": "F1", "logical_fee_key": "import_tax", "expense_category": "进口税费",
        "amount_status": "ACTUAL", "amount": "100", "currency": "MXN",
        "allocation_basis": "goods_value", "scope_type": "ALL_ITEMS",
    }

    monkeypatch.setattr(cost_preview_service, "frappe", FakeFrappe)
    monkeypatch.setattr(cost_preview_service.FrappeCostRepository, "prepare_fx_resolution", lambda _self, _batch, _version: dict(resolution))
    monkeypatch.setattr(cost_preview_service.fx_rate_service, "resolve_costing_fx", lambda **_kwargs: dict(resolution))
    monkeypatch.setattr(cost_preview_service, "project_batch_items", lambda rows, _batch, _version: (rows, {}))
    monkeypatch.setattr(cost_preview_service.fee_service, "_query_rules", lambda _batch, _version: [fee])
    monkeypatch.setattr(
        cost_preview_service.fee_service,
        "compose_fee_worklist_rows",
        lambda rows, _mode, source_context=None: rows,
    )

    result = cost_preview_service.preview_comprehensive_cost("B1", "V1")

    assert result["read_only"] is True
    assert result["fx_resolution"]["rates"]["MXN"]["source"] == "fx_api"
    assert result["fx_context"]["fx_rmb_to_mxn"] == pytest.approx(2.564103)
    assert result["excluded_fees"] == []
