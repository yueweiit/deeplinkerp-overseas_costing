"""中文用途：重算服务测试。"""

import json
import ast
from pathlib import Path
import pytest

from overseas_costing.services.calculate_service import (
    _build_new_item_values,
    batch_update_items,
    calculate_item_rows,
    confirm_actual_shipped_qty_from_quantity,
    create_item,
    delete_batch,
    delete_item,
    recalculate_batch,
    update_item_field,
)


def test_new_manual_item_defaults_shipping_quantity_to_purchase_quantity() -> None:
    values = _build_new_item_values(
        "BATCH-1",
        "VERSION-1",
        {"material_code": "M1", "quantity": 34, "unit": "桶", "unit_price": 25},
        row_no=1,
    )

    assert values["stable_line_key"]
    assert values["purchase_uom"] == "桶"
    assert values["shipped_uom"] == "桶"
    assert values["cost_output_uom"] == "桶"
    assert values["actual_shipped_qty_mode"] == "DEFAULT_PURCHASE"
    assert values.get("actual_shipped_qty") in (None, "")


def test_calculate_service_has_single_public_mutation_implementation() -> None:
    source = Path(__file__).parents[1] / "services" / "calculate_service.py"
    definitions = [node.name for node in ast.parse(source.read_text(encoding="utf-8")).body
                   if isinstance(node, ast.FunctionDef)]

    for name in ("update_item_field", "batch_update_items", "recalculate_batch",
                 "update_allocation_rule", "create_version", "switch_version"):
        assert definitions.count(name) == 1, f"{name} has an overridden skeleton definition"


def test_new_manual_item_keeps_explicit_shipping_quantity() -> None:
    values = _build_new_item_values(
        "BATCH-1",
        "VERSION-1",
        {
            "material_code": "M1",
            "quantity": 34,
            "unit": "桶",
            "actual_shipped_qty": 32,
            "shipped_uom": "桶",
        },
        row_no=1,
    )

    assert values["actual_shipped_qty"] == 32
    assert values["actual_shipped_qty_mode"] == "MANUAL_CONFIRMED"


def test_recalculate_batch_rejects_invalid_main_approval_before_writing(monkeypatch) -> None:
    from overseas_costing.services import calculate_service as service

    class FakeDB:
        @staticmethod
        def get_value(doctype, name, fields, as_dict=False):
            assert doctype == "Overseas Cost Batch"
            assert name == "BATCH-INVALID"
            assert as_dict is True
            return {
                "name": "BATCH-INVALID",
                "source_approval_status": "REJECTED",
                "extra_json": "{}",
            }

        @staticmethod
        def set_value(*_args, **_kwargs):
            raise AssertionError("invalid approval must not persist recalculation")

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(service, "_frappe", FakeFrappe)
    monkeypatch.setattr(service, "_resolve_batch_name", lambda _name: "BATCH-INVALID")
    monkeypatch.setattr(service, "_resolve_version_name", lambda *_args: "VERSION-1")
    monkeypatch.setattr(service, "_get_items", lambda *_args: [{"name": "ITEM-1", "source_type": "oa_logistics"}])
    monkeypatch.setattr(
        service,
        "_get_version_context",
        lambda *_args: (_ for _ in ()).throw(AssertionError("calculation must stop before loading version context")),
    )

    result = recalculate_batch("BATCH-INVALID")

    assert result["ok"] is False
    assert result["invalid_business"] is True
    assert result["invalid_business_scope"] == "source_approval"


def test_recalculate_batch_rejects_all_excluded_linked_purchase_approvals(monkeypatch) -> None:
    from overseas_costing.services import calculate_service as service

    class FakeDB:
        @staticmethod
        def get_value(*_args, **_kwargs):
            return {
                "name": "BATCH-INVALID-PURCHASE",
                "source_approval_status": "COMPLETED",
                "extra_json": json.dumps({"linked_purchase_approvals": [{
                    "source_instance_id": "PROC-REJECTED",
                    "approval_status": "REJECTED",
                }]}),
            }

        @staticmethod
        def set_value(*_args, **_kwargs):
            raise AssertionError("excluded purchase approval must not persist recalculation")

    class FakeFrappe:
        db = FakeDB()

    monkeypatch.setattr(service, "_frappe", FakeFrappe)
    monkeypatch.setattr(service, "_resolve_batch_name", lambda _name: "BATCH-INVALID-PURCHASE")
    monkeypatch.setattr(service, "_resolve_version_name", lambda *_args: "VERSION-1")
    monkeypatch.setattr(service, "_get_items", lambda *_args: [{
        "name": "ITEM-1",
        "source_type": "PURCHASE_EXPENSE_OA",
        "dingtalk_instance_id": "PROC-REJECTED",
    }])
    monkeypatch.setattr(
        service,
        "_get_version_context",
        lambda *_args: (_ for _ in ()).throw(AssertionError("calculation must stop before loading version context")),
    )

    result = recalculate_batch("BATCH-INVALID-PURCHASE")

    assert result["ok"] is False
    assert result["invalid_business"] is True
    assert result["invalid_business_scope"] == "linked_purchase_approval"


def test_calculate_item_rows_allocates_by_goods_value_and_weight() -> None:
    items = [
        {
            "name": "ITEM-1",
            "unit_price": 10,
            "quantity": 10,
            "goods_value": 100,
            "gross_weight_kg": 20,
            "mexico_customs_rmb": 10,
        },
        {
            "name": "ITEM-2",
            "unit_price": 5,
            "quantity": 20,
            "goods_value": 100,
            "gross_weight_kg": 30,
            "mexico_customs_rmb": 10,
        },
    ]
    rules = [
        {
            "rule_code": "china_misc_rmb",
            "allocation_basis": "goods_value",
            "currency": "RMB",
            "amount": 20,
            "is_enabled": 1,
        },
        {
            "rule_code": "china_to_mexico_freight_rmb",
            "allocation_basis": "gross_weight",
            "currency": "RMB",
            "amount": 50,
            "is_enabled": 1,
        },
    ]

    rows, summary = calculate_item_rows(items, rules, fx_rmb_to_mxn=2.6)

    assert summary["total_goods_value"] == 200
    assert summary["total_gross_weight_kg"] == 50
    assert summary["calculation_review"]["status"] == "usable"
    assert summary["calculation_review"]["label"] == "可先采用"
    assert rows[0]["goods_value_ratio"] == 50
    assert rows[0]["weight_ratio"] == 40
    assert rows[0]["freight_alloc_rmb"] == 20
    assert rows[1]["freight_alloc_rmb"] == 30
    assert rows[0]["total_cost_rmb"] == 140
    assert rows[0]["total_unit_rmb"] == 14
    derived = json.loads(rows[0]["derived_json"])
    assert derived["allocated_rules"][0]["amount"] == 20
    assert derived["allocated_rules"][0]["currency"] == "RMB"
    assert derived["allocated_rules"][0]["basis"] == "goods_value"
    assert derived["allocated_rules"][1]["amount_rmb"] == 50


def test_calculate_item_rows_uses_direct_tax_fields_as_customs_cost() -> None:
    rows, summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 10,
                "quantity": 5,
                "goods_value": 50,
                "gross_weight_kg": 8,
                "import_tax_total": 26,
                "service_aa": 10,
            }
        ],
        [],
        fx_rmb_to_mxn=2.6,
    )

    assert rows[0]["total_logistics_mxn"] == 36
    assert rows[0]["total_cost_rmb"] == 63.846154
    assert summary["total_cost_rmb"] == 63.846154
    assert summary["calculation_review"]["status"] == "review"
    assert "费用分摊金额为 0" in summary["calculation_review"]["reason"]
    derived = json.loads(rows[0]["derived_json"])
    assert derived["direct_customs"]["amount_mxn"] == 36
    assert derived["direct_customs"]["source"] == "税费/清关明细字段"
    assert derived["direct_customs"]["source_type"] == "customs_components"
    assert derived["direct_customs"]["tax_mxn"] == 26
    assert derived["direct_customs"]["service_mxn"] == 10


def test_calculate_item_rows_does_not_add_customs_components_twice() -> None:
    rows, _summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 10,
                "quantity": 5,
                "goods_value": 50,
                "gross_weight_kg": 8,
                "mexico_customs_mxn": 100,
                "import_tax_total": 80,
                "service_aa": 20,
            }
        ],
        [],
        fx_rmb_to_mxn=2.5,
    )

    assert rows[0]["total_logistics_mxn"] == 100
    assert rows[0]["total_cost_rmb"] == 90
    derived = json.loads(rows[0]["derived_json"])
    assert derived["direct_customs"]["source_type"] == "customs_total"
    assert "不再叠加" in derived["direct_customs"]["policy"]
    assert "CIF 价值加关税后乘 16%" in derived["direct_customs"]["tax_policy"]


def test_calculate_item_rows_allocates_transport_by_chargeable_weight() -> None:
    rows, summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 100,
                "quantity": 1,
                "gross_weight_kg": 10,
                "volume_weight_kg": 40,
            },
            {
                "name": "ITEM-2",
                "unit_price": 100,
                "quantity": 1,
                "gross_weight_kg": 5,
                "volume_weight_kg": 12,
                "chargeable_weight_kg": 20,
            },
        ],
        [
            {
                "rule_code": "china_to_mexico_freight_rmb",
                "allocation_basis": "chargeable_weight",
                "currency": "RMB",
                "amount": 90,
                "is_enabled": 1,
            }
        ],
        fx_rmb_to_mxn=2.6,
    )

    assert summary["total_chargeable_weight_kg"] == 60
    assert rows[0]["freight_alloc_rmb"] == 60
    assert rows[1]["freight_alloc_rmb"] == 30
    derived = json.loads(rows[0]["derived_json"])
    assert derived["chargeable_weight_kg"] == 40
    assert derived["allocated_rules"][0]["basis"] == "chargeable_weight"


def test_fallback_transport_rule_prefers_gross_weight_first() -> None:
    rows, summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 10,
                "quantity": 10,
                "goods_value": 100,
                "gross_weight_kg": 10,
                "volume_m3": 1,
                "volume_weight_kg": 30,
                "china_to_mexico_freight_rmb": 120,
            },
            {
                "name": "ITEM-2",
                "unit_price": 10,
                "quantity": 10,
                "goods_value": 100,
                "gross_weight_kg": 30,
                "volume_m3": 10,
                "volume_weight_kg": 10,
            },
        ],
        [],
        fx_rmb_to_mxn=2.6,
    )

    assert summary["rule_count"] == 1
    assert rows[0]["freight_alloc_rmb"] == 30
    assert rows[1]["freight_alloc_rmb"] == 90
    derived = json.loads(rows[0]["derived_json"])
    assert derived["allocated_rules"][0]["basis"] == "gross_weight"


def test_calculate_item_rows_does_not_equal_split_when_weight_is_missing() -> None:
    rows, summary = calculate_item_rows(
        [
            {"name": "ITEM-1", "unit_price": 10, "quantity": 5, "goods_value": 50},
            {"name": "ITEM-2", "unit_price": 10, "quantity": 5, "goods_value": 50},
        ],
        [
            {
                "rule_code": "mexico_inland_misc_rmb",
                "allocation_basis": "gross_weight",
                "currency": "RMB",
                "amount": 100,
                "is_enabled": 1,
            }
        ],
        fx_rmb_to_mxn=2.5,
    )

    assert rows[0]["total_cost_rmb"] == 50
    assert rows[1]["total_cost_rmb"] == 50
    assert summary["fee_pool_rmb"] == 100
    assert summary["calculation_review"]["status"] == "blocked"
    assert summary["calculation_review"]["unallocated_fee_rmb"] == 100
    assert "整批缺少对应数据" in summary["calculation_review"]["reason"]


def test_mexico_misc_fallback_keeps_weight_basis_when_weight_is_missing() -> None:
    rows, summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 10,
                "quantity": 5,
                "goods_value": 50,
                "mexico_misc_mxn": 250,
            }
        ],
        [],
        fx_rmb_to_mxn=2.5,
    )

    assert rows[0]["total_cost_rmb"] == 50
    assert summary["fee_pool_rmb"] == 100
    assert summary["calculation_review"]["status"] == "blocked"


def test_calculate_item_rows_marks_missing_goods_value_as_blocked() -> None:
    rows, summary = calculate_item_rows(
        [
            {
                "name": "ITEM-1",
                "unit_price": 0,
                "quantity": 3,
                "goods_value": 0,
            }
        ],
        [],
        fx_rmb_to_mxn=2.6,
    )

    assert rows[0]["total_cost_rmb"] == 0
    assert summary["calculation_review"]["status"] == "blocked"
    assert summary["calculation_review"]["label"] == "待补数据"
    assert "采购货值为空" in summary["calculation_review"]["reason"]


def test_update_item_field_dry_run_allows_editable_field_and_coerces_numeric_value() -> None:
    result = update_item_field(
        item_name="ITEM-1",
        fieldname="quantity",
        value="12.5",
        remark="修正装箱单数量",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert result["value"] == 12.5
    assert result["manual_override_reason"] == "修正装箱单数量"


def test_existing_purchase_value_requires_reason_before_server_save(monkeypatch) -> None:
    from overseas_costing.services import calculate_service as service

    class Item:
        batch = "B1"
        version = "V1"
        row_no = 1
        goods_value = 100

        def save(self, **_kwargs):
            raise AssertionError("missing correction reason must not save")

    class DB:
        @staticmethod
        def set_value(*_args, **_kwargs):
            raise AssertionError("missing correction reason must not mutate batch")

    class FakeFrappe:
        db = DB()

        @staticmethod
        def get_doc(doctype, name):
            assert (doctype, name) == ("Overseas Cost Item", "ITEM-1")
            return Item()

    monkeypatch.setattr(service, "_frappe", FakeFrappe)

    result = update_item_field(
        "ITEM-1",
        "goods_value",
        "120",
        _skip_edit_check=True,
    )

    assert result["ok"] is False
    assert result["edit_mode"] == "reason_required"
    assert "修改原因" in result["message"]


def test_existing_physical_value_requires_reason_before_server_save(monkeypatch) -> None:
    from overseas_costing.services import calculate_service as service

    class Item:
        batch = "B1"
        version = "V1"
        row_no = 1
        gross_weight_kg = 388
        extra_json = "{}"
        is_excluded = 0

        def as_dict(self):
            return vars(self.__class__).copy()

        def save(self, **_kwargs):
            raise AssertionError("missing correction reason must not save")

    class FakeFrappe:
        @staticmethod
        def get_doc(*_args):
            return Item()

    monkeypatch.setattr(service, "_frappe", FakeFrappe)
    monkeypatch.setattr(
        "overseas_costing.services.effective_source_values.batch_source_context",
        lambda *_args, **_kwargs: {},
    )

    result = update_item_field(
        "ITEM-1",
        "gross_weight_kg",
        "390",
        _skip_edit_check=True,
    )

    assert result["ok"] is False
    assert result["edit_mode"] == "reason_required"
    assert "修改原因" in result["message"]


def test_supplier_fill_is_editable_but_changing_existing_supplier_requires_reason(monkeypatch) -> None:
    items = {
        "EMPTY": {"supplier": "", "extra_json": json.dumps({
            "supplier_field_present": True,
            "supplier_raw_value": "Supplier A typo",
            "supplier_match_status": "SUGGESTED",
        })},
        "EXISTING": {"supplier": "SUPPLIER-A"},
    }
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    monkeypatch.setattr(
        service,
        "validate_canonical_supplier",
        lambda raw, suppliers=None: raw
        if raw in {"SUPPLIER-A", "SUPPLIER-B"}
        else (_ for _ in ()).throw(ValueError("供应商必须从启用中的 ERP 供应商列表选择。")),
    )

    filled = service.update_item_field(
        "EMPTY", "supplier", "SUPPLIER-A", _skip_edit_check=True,
    )
    rejected = service.update_item_field(
        "EXISTING", "supplier", "SUPPLIER-B", _skip_edit_check=True,
    )
    corrected = service.update_item_field(
        "EXISTING", "supplier", "SUPPLIER-B", remark="供应商抬头核对", _skip_edit_check=True,
    )

    assert filled["ok"] is True and items["EMPTY"].supplier == "SUPPLIER-A"
    filled_metadata = json.loads(items["EMPTY"].extra_json)
    assert filled_metadata["supplier_match_status"] == "MANUAL_CONFIRMED"
    assert filled_metadata["supplier_canonical_name"] == "SUPPLIER-A"
    assert rejected["ok"] is False and rejected["edit_mode"] == "reason_required"
    assert items["EXISTING"].supplier == "SUPPLIER-B"
    assert corrected["ok"] is True
    assert corrected["old_value"] == "SUPPLIER-A"
    assert corrected["manual_override_reason"] == "供应商抬头核对"


def test_supplier_edit_rejects_text_that_is_not_an_active_erp_supplier(monkeypatch) -> None:
    from overseas_costing.services import calculate_service as service

    monkeypatch.setattr(
        service,
        "validate_canonical_supplier",
        lambda _raw, suppliers=None: (_ for _ in ()).throw(
            ValueError("供应商必须从启用中的 ERP 供应商列表选择。")
        ),
    )

    result = service.update_item_field("ITEM-1", "supplier", "Supplier A typo")

    assert result["ok"] is False
    assert "ERP 供应商列表" in result["message"]


def test_batch_supplier_updates_load_active_supplier_catalog_once(monkeypatch) -> None:
    items = {"I1": {"supplier": ""}, "I2": {"supplier": ""}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service

    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)
    load_calls = []
    monkeypatch.setattr(
        service,
        "load_active_suppliers",
        lambda: load_calls.append("load") or [
            {"name": "SUPPLIER-A", "supplier_name": "Supplier A", "disabled": 0},
            {"name": "SUPPLIER-B", "supplier_name": "Supplier B", "disabled": 0},
        ],
    )

    result = service.batch_update_items(
        "B1",
        json.dumps([
            {"item_name": "I1", "fieldname": "supplier", "value": "SUPPLIER-A"},
            {"item_name": "I2", "fieldname": "supplier", "value": "SUPPLIER-B"},
        ]),
        version_name="V1",
        edit_token="TOKEN",
        expected_modified="OLD",
    )

    assert result["ok"] is True
    assert load_calls == ["load"]
    assert items["I1"].supplier == "SUPPLIER-A"
    assert items["I2"].supplier == "SUPPLIER-B"


def test_net_weight_is_an_editable_numeric_material_field() -> None:
    result = update_item_field(
        item_name="ITEM-1",
        fieldname="net_weight_kg",
        value="8.25",
        remark="核对装箱计划净重",
    )

    assert result["ok"] is True
    assert result["value"] == 8.25


def test_shipping_quantity_edit_requires_positive_value_and_records_manual_provenance() -> None:
    invalid = update_item_field(
        item_name="ITEM-1",
        fieldname="actual_shipped_qty",
        value="0",
    )
    valid = update_item_field(
        item_name="ITEM-1",
        fieldname="actual_shipped_qty",
        value="12",
    )

    assert invalid["ok"] is False
    assert "大于 0" in invalid["message"]
    assert valid["ok"] is True
    assert valid["companion_updates"]["actual_shipped_qty_mode"] == "MANUAL_CONFIRMED"


def test_update_item_field_dry_run_rejects_calculated_field_without_reason() -> None:
    result = update_item_field(
        item_name="ITEM-1",
        fieldname="total_cost_rmb",
        value="100",
    )

    assert result["ok"] is False
    assert result["edit_mode"] == "reason_required"
    assert "人工覆盖需要填写修改原因" in result["message"]


def test_update_item_field_dry_run_allows_special_override_with_reason() -> None:
    result = update_item_field(
        item_name="ITEM-1",
        fieldname="total_cost_rmb",
        value="100",
        manual_override_reason="财务手工确认",
    )

    assert result["ok"] is True
    assert result["edit_mode"] == "special_override"
    assert result["value"] == 100


def test_update_item_field_dry_run_rejects_readonly_calc_field() -> None:
    result = update_item_field(
        item_name="ITEM-1",
        fieldname="freight_alloc_rmb",
        value="10",
        remark="测试",
    )

    assert result["ok"] is False
    assert result["edit_mode"] == "readonly_calc"


def test_batch_update_items_dry_run_counts_success_and_errors() -> None:
    updates = json.dumps(
        [
            {"item_name": "ITEM-1", "field_name": "quantity", "field_value": "12"},
            {"item_name": "ITEM-2", "fieldname": "freight_alloc_rmb", "value": "1"},
        ],
        ensure_ascii=False,
    )

    result = batch_update_items(batch_name="BATCH-001", updates=updates)

    assert result["ok"] is False
    assert result["dry_run"] is True
    assert result["changed_count"] == 1
    assert result["error_count"] == 1
    assert result["results"][0]["ok"] is True
    assert result["results"][1]["edit_mode"] == "readonly_calc"


def test_confirm_actual_shipped_qty_from_quantity_dry_run_counts_fillable_rows() -> None:
    items = [
        {"name": "ITEM-1", "quantity": "12", "actual_shipped_qty": ""},
        {"name": "ITEM-2", "quantity": "3", "actual_shipped_qty": "2"},
        {"name": "ITEM-3", "quantity": "", "actual_shipped_qty": ""},
    ]

    result = confirm_actual_shipped_qty_from_quantity(
        batch_name="BATCH-001",
        version_name="VERSION-001",
        preview_items=json.dumps(items, ensure_ascii=False),
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["changed_count"] == 1
    assert result["skipped_count"] == 2
    assert result["missing_quantity_count"] == 1
    assert result["results"][0]["value"] == 12
    assert result["results"][1]["skip_reason"] == "actual_qty_exists"
    assert result["results"][2]["skip_reason"] == "quantity_missing"


def test_create_item_dry_run_builds_insert_payload() -> None:
    result = create_item(
        batch_name="BATCH-001",
        version_name="VERSION-001",
        item_payload=json.dumps(
            {
                "material_code": "NEW-001",
                "product_name": "新增物料",
                "unit_price": "2.5",
                "quantity": "4",
                "transport_mode": "海运",
            },
            ensure_ascii=False,
        ),
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["item"]["material_code"] == "NEW-001"
    assert result["item"]["goods_value"] == 10
    assert result["item"]["transport_mode"] == "SEA"


def test_delete_item_dry_run_returns_preview() -> None:
    result = delete_item(item_name="ITEM-1", batch_name="BATCH-001", version_name="VERSION-001")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["item_name"] == "ITEM-1"


def test_delete_item_soft_excludes_and_restore_reverses_it(monkeypatch) -> None:
    items = {"I1": {"extra_json": json.dumps({"settlement_cargo": {"quantity": 2}})}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    excluded = service.delete_item(
        "I1", batch_name="B1", version_name="V1", remark="不属于本票",
        edit_token="TOKEN", expected_modified="OLD",
    )
    restored = service.restore_item(
        "I1", batch_name="B1", version_name="V1", remark="已核对",
        edit_token="TOKEN", expected_modified="2026-09-11 10:00:00",
    )

    assert excluded["ok"] is True and excluded["soft_excluded"] is True
    assert excluded["batch_modified"] == "2026-09-11 10:00:00"
    assert restored["ok"] is True and restored["restored"] is True
    assert items["I1"].is_excluded == 0
    assert items["I1"].excluded_at is None
    assert items["I1"].excluded_by == ""
    assert items["I1"].exclusion_reason == ""


def test_delete_batch_dry_run_returns_preview() -> None:
    result = delete_batch(batch_name="BATCH-001")

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["batch_name"] == "BATCH-001"


def _install_item_edit_frappe(monkeypatch, items):
    from types import SimpleNamespace
    from overseas_costing.services import calculate_service as service
    from overseas_costing.services import effective_source_values

    class DB:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def sql(self, *_args, **_kwargs):
            return [{"current_version": "V1", "confirm_status": "Pending",
                     "writeback_status": "Not Started", "version_status": "Active"}]

        def set_value(self, *_args, **_kwargs):
            return None

        def get_value(self, doctype, name, fieldname=None, **_kwargs):
            if doctype == "Overseas Cost Item":
                return items[name].batch
            if doctype == "Overseas Cost Batch" and fieldname == ["name"]:
                return {"name": "B1"}
            if doctype == "Overseas Cost Batch" and fieldname == "modified":
                return "2026-09-11 10:00:00"
            return None

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    class Item:
        def __init__(self, name, **values):
            self.name = name
            self.batch = "B1"
            self.version = "V1"
            self.row_no = 1
            self.actual_shipped_qty = 2
            self.shipped_uom = "件"
            self.unit = "件"
            self.goods_value = 20
            self.extra_json = "{}"
            self.manual_override_flag = 0
            self.manual_override_reason = ""
            self.__dict__.update(values)

        def as_dict(self):
            return dict(self.__dict__)

        def save(self, **_kwargs):
            return self

    normalized = {}
    for name, values in list(items.items()):
        normalized[name] = values if isinstance(values, Item) else Item(name, **values)
    items.clear()
    items.update(normalized)
    db = DB()
    fake = SimpleNamespace(
        db=db,
        session=SimpleNamespace(user="finance@example.com"),
        utils=SimpleNamespace(now=lambda: "2026-09-11 10:00:00"),
        get_doc=lambda doctype, name=None: items[name] if isinstance(doctype, str) else SimpleNamespace(insert=lambda **_kwargs: None),
    )
    monkeypatch.setattr(service, "_frappe", fake)
    monkeypatch.setattr(service, "_insert_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(effective_source_values, "batch_source_context", lambda *_args, **_kwargs: {})
    return service, db


def _automatic_settlement_item(**changes):
    from overseas_costing.services.logistics_settlement.valuation import value_final_cargo

    values = {"source_doc_no": "PUR-1", "unit_price": 10, "purchase_currency": "RMB",
              "purchase_uom": "件", "unit_price_uom": "件", "quantity": 2,
              "actual_shipped_qty": 2, "shipped_uom": "件", "unit": "件", **changes}
    cargo = {"material_code": "", "quantity": 2, "unit": "件", "source_snapshot": "S"}
    valuation = value_final_cargo({**values, "extra_json": "{}"}, cargo, {})
    return {**values, "goods_value": 20, "extra_json": json.dumps({
        "settlement_cargo": cargo, "settlement_valuation": valuation,
    })}


def _legacy_manual_item(*, prior=False, manual_flag="manual_override_flag"):
    valuation = {
        "amount_rmb": "25", "currency": "RMB", "quantity": "2", "uom": "件",
        "method": "manual", "status": "manual", "confirmed": True,
        manual_flag: 1, "error": "",
    }
    if prior:
        valuation.update(
            prior_amount_rmb="20",
            prior_evidence={"amount_rmb": "20", "currency": "RMB", "quantity": "2",
                            "uom": "件", "method": "SYSTEM_EXCEL", "status": "automatic", "error": ""},
        )
    return {"goods_value": 25, "extra_json": json.dumps({"shipment_valuation": valuation})}


def test_update_virtual_shipment_value_saves_server_manual_metadata(monkeypatch) -> None:
    items = {"I1": {}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", "shipment_value_rmb", "25", version_name="V1", remark="财务核对",
        _skip_edit_check=True,
    )

    metadata = json.loads(items["I1"].extra_json)
    manual = metadata["manual_shipment_valuation"]
    assert result["ok"] is True and result["changed"] is True
    assert result["valuation"]["status"] == "manual"
    assert result["goods_value"] == 25
    assert result["batch_modified"] == "2026-09-11 10:00:00"
    assert items["I1"].goods_value == 25
    assert manual["amount_rmb"] == "25"
    assert manual["currency"] == "RMB"
    assert manual["quantity"] == "2" and manual["uom"] == "件"
    assert manual["input_fingerprint"]
    assert manual["confirmed"] is True and manual["manual"] is True
    assert manual["actor"] == "finance@example.com"
    assert manual["confirmed_at"] == "2026-09-11 10:00:00"
    assert manual["reason"] == "财务核对"


def test_update_legacy_goods_value_routes_to_manual_shipment_valuation(monkeypatch) -> None:
    from overseas_costing.services.shipment_cost_service import shipment_value

    items = {"I1": _automatic_settlement_item()}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", "goods_value", "99", version_name="V1", remark="财务确认", _skip_edit_check=True,
    )

    valuation = shipment_value(items["I1"].as_dict())
    metadata = json.loads(items["I1"].extra_json)
    assert result["ok"] is True and result["fieldname"] == "goods_value"
    assert result["valuation"]["status"] == "manual"
    assert result["valuation"]["amount_rmb"] == "99"
    assert result["goods_value"] == 99 and items["I1"].goods_value == 99
    assert metadata["manual_shipment_valuation"]["amount_rmb"] == "99"
    assert valuation["status"] == "manual" and valuation["amount_rmb"] == "99"

    cleared = service.update_item_field(
        "I1", "goods_value", "", version_name="V1", remark="撤销人工确认", _skip_edit_check=True,
    )
    assert "manual_shipment_valuation" not in json.loads(items["I1"].extra_json)
    assert cleared["valuation"]["status"] == "automatic"
    assert cleared["valuation"]["amount_rmb"] == "20.000000"
    assert cleared["goods_value"] == 20 and items["I1"].goods_value == 20


@pytest.mark.parametrize(("initial", "fieldname", "new_value", "expected_status", "expected_goods"), [
    ({}, "unit_price", "12", "stale", 0),
    ({}, "purchase_currency", "USD", "stale", 0),
    ({}, "unit_price_uom", "箱", "stale", 0),
    ({"unit_price_uom": ""}, "purchase_uom", "箱", "stale", 0),
    ({}, "source_doc_no", "PUR-2", "stale", 0),
])
def test_automatic_valuation_input_edits_resolve_and_sync_goods_value(
    monkeypatch, initial, fieldname, new_value, expected_status, expected_goods,
) -> None:
    items = {"I1": _automatic_settlement_item(**initial)}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", fieldname, new_value, remark="采购更正", _skip_edit_check=True,
    )

    assert result["valuation"]["status"] == expected_status
    assert result["valuation"]["amount_rmb"] is None
    assert result["goods_value"] == expected_goods
    assert items["I1"].goods_value == expected_goods


@pytest.mark.parametrize("fieldname", ["shipment_value_rmb", "goods_value"])
def test_clearing_manual_value_restores_structured_legacy_prior(monkeypatch, fieldname) -> None:
    from overseas_costing.services.shipment_cost_service import shipment_value

    items = {"I1": {"goods_value": 20, "extra_json": "{}"}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    saved = service.update_item_field(
        "I1", fieldname, "99", remark="财务确认", _skip_edit_check=True,
    )
    metadata = json.loads(items["I1"].extra_json)
    cleared = service.update_item_field(
        "I1", fieldname, "", remark="撤销人工确认", _skip_edit_check=True,
    )

    assert saved["valuation"]["status"] == "manual"
    assert saved["goods_value"] == 99
    assert metadata["shipment_valuation"]["amount_rmb"] == "20"
    assert metadata["shipment_valuation"]["method"] == "LEGACY_PURCHASE"
    assert "manual_shipment_valuation" not in json.loads(items["I1"].extra_json)
    assert cleared["valuation"]["status"] == "automatic"
    assert cleared["valuation"]["amount_rmb"] == "20"
    assert cleared["goods_value"] == 20 and items["I1"].goods_value == 20
    assert shipment_value(items["I1"].as_dict())["amount_rmb"] == "20"


@pytest.mark.parametrize("fieldname", ["shipment_value_rmb", "goods_value"])
def test_clearing_manual_value_with_no_prior_returns_to_missing(monkeypatch, fieldname) -> None:
    items = {"I1": {"goods_value": 0, "extra_json": "{}"}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    service.update_item_field("I1", fieldname, "99", remark="财务确认", _skip_edit_check=True)
    cleared = service.update_item_field(
        "I1", fieldname, "", remark="撤销人工确认", _skip_edit_check=True,
    )

    assert cleared["valuation"]["status"] == "missing"
    assert cleared["valuation"]["amount_rmb"] is None
    assert cleared["valuation"]["error"] == "GOODS_VALUE_MISSING"
    assert cleared["goods_value"] == 0 and items["I1"].goods_value == 0


@pytest.mark.parametrize("fieldname", ["shipment_value_rmb", "goods_value"])
@pytest.mark.parametrize("manual_flag", ["manual", "manual_override", "manual_override_flag"])
def test_clearing_legacy_shipment_valuation_manual_removes_manual_state(
    monkeypatch, fieldname, manual_flag,
) -> None:
    items = {"I1": _legacy_manual_item(manual_flag=manual_flag)}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", fieldname, "", remark="撤销历史人工值", _skip_edit_check=True,
    )

    metadata = json.loads(items["I1"].extra_json)
    assert result["ok"] is True and result["changed"] is True
    assert result["old_value"] == "25"
    assert result["valuation"]["status"] == "missing"
    assert result["valuation"]["amount_rmb"] is None
    assert result["goods_value"] == 0 and items["I1"].goods_value == 0
    assert "shipment_valuation" not in metadata


@pytest.mark.parametrize("fieldname", ["shipment_value_rmb", "goods_value"])
def test_new_manual_override_does_not_resurrect_legacy_manual_after_clear(monkeypatch, fieldname) -> None:
    items = {"I1": _legacy_manual_item()}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    saved = service.update_item_field(
        "I1", fieldname, "99", remark="新人工值", _skip_edit_check=True,
    )
    cleared = service.update_item_field(
        "I1", fieldname, "", remark="撤销新人工值", _skip_edit_check=True,
    )

    assert saved["valuation"]["status"] == "manual"
    assert saved["valuation"]["amount_rmb"] == "99"
    assert cleared["valuation"]["status"] == "missing"
    assert cleared["valuation"]["amount_rmb"] is None
    assert cleared["goods_value"] == 0 and items["I1"].goods_value == 0
    assert "shipment_valuation" not in json.loads(items["I1"].extra_json)


@pytest.mark.parametrize("fieldname", ["shipment_value_rmb", "goods_value"])
def test_clearing_legacy_manual_restores_only_structured_automatic_prior(monkeypatch, fieldname) -> None:
    items = {"I1": _legacy_manual_item(prior=True)}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", fieldname, "", remark="恢复系统证据", _skip_edit_check=True,
    )

    metadata = json.loads(items["I1"].extra_json)
    assert result["changed"] is True
    assert result["valuation"]["status"] == "automatic"
    assert result["valuation"]["amount_rmb"] == "20"
    assert result["goods_value"] == 20 and items["I1"].goods_value == 20
    assert metadata["shipment_valuation"]["status"] == "automatic"
    assert not any(metadata["shipment_valuation"].get(flag)
                   for flag in ("manual", "manual_override", "manual_override_flag"))


def test_legacy_positive_goods_mirror_survives_unstructured_price_edit(monkeypatch) -> None:
    items = {"I1": {"goods_value": 20, "unit_price": 10, "extra_json": "{}"}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field(
        "I1", "unit_price", "12", remark="采购更正", _skip_edit_check=True,
    )

    assert result["valuation"]["status"] == "automatic"
    assert result["valuation"]["amount_rmb"] == 20
    assert result["goods_value"] == 20 and items["I1"].goods_value == 20


def test_update_virtual_shipment_value_preserves_explicit_zero(monkeypatch) -> None:
    items = {"I1": {}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field("I1", "shipment_value_rmb", "0", _skip_edit_check=True)

    assert result["valuation"]["status"] == "manual"
    assert result["valuation"]["amount_rmb"] == "0"
    assert result["goods_value"] == 0
    assert items["I1"].goods_value == 0


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "1e999", "not-a-number"])
def test_update_virtual_shipment_value_rejects_invalid_numbers(value) -> None:
    result = update_item_field("I1", "shipment_value_rmb", value)

    assert result["ok"] is False
    assert "有限非负数字" in result["message"]


def test_clearing_manual_shipment_value_returns_to_automatic_value(monkeypatch) -> None:
    from overseas_costing.services.logistics_settlement.valuation import value_final_cargo

    item_values = {"source_doc_no": "PUR-1", "unit_price": 10, "purchase_currency": "RMB",
                   "purchase_uom": "件", "unit_price_uom": "件"}
    cargo = {"material_code": "", "quantity": 2, "unit": "件", "source_snapshot": "S"}
    automatic = value_final_cargo({**item_values, "extra_json": "{}"}, cargo, {})
    items = {"I1": {**item_values, "extra_json": json.dumps({"settlement_cargo": cargo,
              "settlement_valuation": automatic})}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    service.update_item_field("I1", "shipment_value_rmb", "25", _skip_edit_check=True)

    result = service.update_item_field("I1", "shipment_value_rmb", "", _skip_edit_check=True)

    metadata = json.loads(items["I1"].extra_json)
    assert "manual_shipment_valuation" not in metadata
    assert result["valuation"]["status"] == "automatic"
    assert result["valuation"]["amount_rmb"] == "20.000000"
    assert result["goods_value"] == 20
    assert items["I1"].goods_value == 20


def test_clearing_manual_shipment_value_returns_to_existing_conflict(monkeypatch) -> None:
    from overseas_costing.services.logistics_settlement.valuation import reconcile_replacement_value
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    prior = {"goods_value": 20, "source_doc_no": "PUR-1", "unit_price": 10,
             "purchase_currency": "RMB", "purchase_uom": "件", "unit_price_uom": "件"}
    current = {**prior, "actual_shipped_qty": 3, "shipped_uom": "件", "unit": "件"}
    cargo = {"material_code": "", "quantity": 3, "unit": "件", "source_snapshot": "S"}
    automatic = reconcile_replacement_value(current, cargo, {}, prior)
    manual = build_manual_shipment_valuation(current, 25, actor="finance", confirmed_at="now")
    items = {"I1": {**current, "extra_json": json.dumps({"settlement_cargo": cargo,
              "settlement_valuation": automatic, "manual_shipment_valuation": manual})}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field("I1", "shipment_value_rmb", "", _skip_edit_check=True)

    assert result["valuation"]["status"] == "conflict"
    assert result["valuation"]["error"] == "SETTLEMENT_SHIPMENT_VALUE_CONFLICT"
    assert result["valuation"]["prior_amount_rmb"] == "20"
    assert result["valuation"]["calculated_amount_rmb"] == "30.000000"
    assert result["goods_value"] == 0 and items["I1"].goods_value == 0


def test_quantity_and_unit_edits_stale_manual_value_without_deleting_it(monkeypatch) -> None:
    items = {"I1": {}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    service.update_item_field("I1", "shipment_value_rmb", "25", _skip_edit_check=True)

    quantity_result = service.update_item_field("I1", "actual_shipped_qty", "3", _skip_edit_check=True)
    stored_after_quantity = json.loads(items["I1"].extra_json)["manual_shipment_valuation"]
    unit_result = service.update_item_field("I1", "shipped_uom", "箱", _skip_edit_check=True)

    assert quantity_result["valuation"]["status"] == "stale"
    assert quantity_result["valuation"]["amount_rmb"] is None
    assert quantity_result["goods_value"] == 0
    assert stored_after_quantity["amount_rmb"] == "25"
    assert unit_result["valuation"]["status"] == "stale"
    assert items["I1"].goods_value == 0
    assert json.loads(items["I1"].extra_json)["manual_shipment_valuation"]["amount_rmb"] == "25"


@pytest.mark.parametrize(('initial','fieldname','new_value'), [
    ({'quantity': 2, 'actual_shipped_qty_mode': 'DEFAULT_PURCHASE'}, 'quantity', '3'),
    ({'quantity': 2, 'actual_shipped_qty_mode': 'DEFAULT_PURCHASE', 'shipped_uom': '',
      'purchase_uom': '件', 'unit': '件'}, 'purchase_uom', '箱'),
    ({'quantity': 2, 'actual_shipped_qty_mode': 'DEFAULT_PURCHASE', 'shipped_uom': '',
      'purchase_uom': '', 'unit': '件'}, 'unit', '箱'),
])
def test_effective_shipping_fallback_edits_stale_manual_value_and_zero_goods(
    monkeypatch, initial, fieldname, new_value,
) -> None:
    items = {'I1': initial}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    service.update_item_field('I1', 'shipment_value_rmb', '25', _skip_edit_check=True)

    result = service.update_item_field('I1', fieldname, new_value, remark='核对发货口径', _skip_edit_check=True)

    assert result['valuation']['status'] == 'stale'
    assert result['valuation']['amount_rmb'] is None
    assert result['goods_value'] == 0
    assert items['I1'].goods_value == 0
    assert json.loads(items['I1'].extra_json)['manual_shipment_valuation']['amount_rmb'] == '25'


def test_batch_update_supports_manual_shipment_value_set_and_clear(monkeypatch) -> None:
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    prior_row = {"actual_shipped_qty": 2, "shipped_uom": "件", "unit": "件"}
    manual = build_manual_shipment_valuation(prior_row, 30, actor="old", confirmed_at="old")
    items = {"I1": {}, "I2": {"extra_json": json.dumps({"manual_shipment_valuation": manual})}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    result = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "fieldname": "shipment_value_rmb", "value": "12"},
        {"item_name": "I2", "fieldname": "shipment_value_rmb", "value": ""},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")

    assert result["ok"] is True and result["changed_count"] == 2
    assert result["batch_modified"] == "2026-09-11 10:00:00"
    assert result["results"][0]["valuation"]["status"] == "manual"
    assert result["results"][1]["valuation"]["status"] == "automatic"
    assert items["I1"].goods_value == 12
    assert items["I2"].goods_value == 20


def test_batch_update_routes_goods_value_and_syncs_automatic_staleness(monkeypatch) -> None:
    items = {"I1": _automatic_settlement_item(), "I2": _automatic_settlement_item(),
             "I3": _automatic_settlement_item()}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    result = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "fieldname": "goods_value", "value": "99", "remark": "财务确认"},
        {"item_name": "I2", "fieldname": "unit_price", "value": "12", "remark": "采购更正"},
        {"item_name": "I3", "fieldname": "source_doc_no", "value": "PUR-2"},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")

    assert result["ok"] is True and result["changed_count"] == 3
    assert result["results"][0]["valuation"]["status"] == "manual"
    assert result["results"][0]["valuation"]["amount_rmb"] == "99"
    assert result["results"][1]["valuation"]["status"] == "stale"
    assert result["results"][2]["valuation"]["status"] == "stale"
    assert items["I1"].goods_value == 99
    assert items["I2"].goods_value == 0
    assert items["I3"].goods_value == 0


def test_batch_manual_aliases_restore_legacy_priors_after_clear(monkeypatch) -> None:
    items = {"I1": {"goods_value": 20, "extra_json": "{}"},
             "I2": {"goods_value": 30, "extra_json": "{}"}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    saved = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "fieldname": "shipment_value_rmb", "value": "99"},
        {"item_name": "I2", "fieldname": "goods_value", "value": "88", "remark": "财务确认"},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")
    cleared = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "fieldname": "shipment_value_rmb", "value": ""},
        {"item_name": "I2", "fieldname": "goods_value", "value": "", "remark": "撤销人工确认"},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")

    assert saved["ok"] is True and [row["valuation"]["status"] for row in saved["results"]] == ["manual", "manual"]
    assert cleared["ok"] is True
    assert [row["valuation"]["amount_rmb"] for row in cleared["results"]] == ["20", "30"]
    assert items["I1"].goods_value == 20 and items["I2"].goods_value == 30


def test_batch_update_rollback_message_carries_the_per_row_cause(monkeypatch) -> None:
    """整体回滚只说明结果，不说明为什么；原因必须随 message 一起回来。

    回归背景：逐行失败被折叠成一句「批量字段更新存在错误，已整体回滚」，用户看到的
    只有「失败了」三个字（哪一行、为什么都没有），于是只能反复点保存。
    """

    items = {"I1": {}, "I2": {}}
    service, db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    result = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "fieldname": "derived_json", "value": "{}"},
        {"item_name": "I2", "fieldname": "derived_json", "value": "{}"},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")

    assert result["ok"] is False
    assert result["changed_count"] == 0
    assert db.rollbacks == 1
    assert result["message"].startswith("批量字段更新存在错误，已整体回滚。")
    assert "物料 I1" in result["message"]
    assert "由重算服务生成，不能直接手工编辑" in result["message"]
    assert "（共 2 行失败）" in result["message"]


def test_batch_update_rollback_message_stays_short_without_a_row_reason(monkeypatch) -> None:
    """逐行结果里没有原因时（缺 item_name/fieldname 这类参数错误）不再硬拼半句。"""

    items = {"I1": {}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import edit_session_service
    monkeypatch.setattr(edit_session_service, "assert_batch_write", lambda *_args, **_kwargs: None)

    result = service.batch_update_items("B1", json.dumps([
        {"item_name": "I1", "value": "x"},
    ]), version_name="V1", edit_token="TOKEN", expected_modified="OLD")

    assert result["ok"] is False
    assert "缺少 item_name/name 或 fieldname/field_name" in result["message"]


def test_expense_physical_overlay_quantity_edit_stales_manual_value(monkeypatch) -> None:
    context = {"root_kind": "expense", "available": True, "approved": True, "invalid": False,
               "fingerprint": "CTX", "source_snapshot": "SOURCE"}
    metadata = {
        "effective_logistics_source": context,
        "settlement_cargo": {"quantity": 2, "unit": "件", "source_snapshot": "SOURCE"},
        "settlement_physical": {
            "source_context_fingerprint": "CTX", "source_snapshot": "SOURCE",
            "values": {"actual_shipped_qty": 2, "shipped_uom": "件"}, "evidence": {},
        },
    }
    items = {"I1": {"extra_json": json.dumps(metadata)}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import effective_source_values
    from overseas_costing.services import effective_logistics_source
    monkeypatch.setattr(effective_source_values, "batch_source_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(effective_logistics_source, "resolve_source_context", lambda *_args, **_kwargs: context)
    service.update_item_field("I1", "shipment_value_rmb", "25", _skip_edit_check=True)

    result = service.update_item_field("I1", "actual_shipped_qty", "3", _skip_edit_check=True)

    saved_metadata = json.loads(items["I1"].extra_json)
    assert result["valuation"]["status"] == "stale"
    assert result["goods_value"] == 0
    assert saved_metadata["manual_shipment_valuation"]["amount_rmb"] == "25"
    assert saved_metadata["settlement_physical"]["values"]["actual_shipped_qty"] == 3
    assert saved_metadata["settlement_cargo"]["quantity"] == 3


def test_missing_expense_physical_cell_can_attach_current_context_and_save(monkeypatch) -> None:
    context = {"root_kind": "expense", "available": True, "approved": True, "invalid": False,
               "fingerprint": "CTX", "source_snapshot": "SOURCE",
               "packing": {"selected_source": "PACKING-1"}}
    items = {"I1": {"extra_json": "{}", "gross_weight_kg": None}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)
    from overseas_costing.services import effective_source_values
    from overseas_costing.services import effective_logistics_source
    monkeypatch.setattr(effective_source_values, "batch_source_context", lambda *_args, **_kwargs: context)
    monkeypatch.setattr(effective_logistics_source, "resolve_source_context", lambda *_args, **_kwargs: context)

    result = service.update_item_field("I1", "gross_weight_kg", "9.7", _skip_edit_check=True)

    metadata = json.loads(items["I1"].extra_json)
    assert result["ok"] is True and result["changed"] is True
    assert metadata["effective_logistics_source"]["fingerprint"] == "CTX"
    assert metadata["settlement_physical"]["values"]["gross_weight_kg"] == 9.7
    assert metadata["settlement_physical"]["evidence"]["gross_weight_kg"]["kind"] == "manual_override"
    assert effective_source_values.project_source_values(items["I1"].as_dict(), context)["gross_weight_kg"] == 9.7


@pytest.mark.parametrize("field", ["adopted_purchase_value", "purchase_value_history"])
@pytest.mark.parametrize("operation", ["create", "replace", "remove"])
def test_normal_item_edit_cannot_change_adopted_purchase_metadata(monkeypatch, field, operation):
    original_fact = {
        "material_code": "A", "source_refs": [{"source_id": "ORIGINAL", "row": 1}],
        "amount_rmb": "20", "quantity": "2", "uom": "件",
    }
    forged_fact = {**original_fact, "source_refs": [{"source_id": "FORGED", "row": 1}], "amount_rmb": "1"}
    original = original_fact if field == "adopted_purchase_value" else [original_fact]
    forged = forged_fact if field == "adopted_purchase_value" else [forged_fact]
    before = {"user_note": "before", **({field: original} if operation != "create" else {})}
    after = {"user_note": "after", **({field: forged} if operation != "remove" else {})}
    items = {"I1": {"extra_json": json.dumps(before, ensure_ascii=False)}}
    service, db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field("I1", "extra_json", after, version_name="V1", _skip_edit_check=True)

    assert not result["ok"] and not result["changed"]
    assert result["edit_mode"] == "server_metadata"
    assert json.loads(items["I1"].extra_json) == before
    assert db.commits == 0


def test_normal_item_metadata_edit_can_keep_adopted_purchase_evidence(monkeypatch):
    fact = {"material_code": "A", "source_refs": [{"source_id": "ORIGINAL", "row": 1}],
            "amount_rmb": "20", "quantity": "2", "uom": "件"}
    before = {"adopted_purchase_value": fact, "purchase_value_history": [fact], "user_note": "before"}
    after = {**before, "user_note": "after"}
    items = {"I1": {"extra_json": json.dumps(before, ensure_ascii=False)}}
    service, _db = _install_item_edit_frappe(monkeypatch, items)

    result = service.update_item_field("I1", "extra_json", after, version_name="V1", _skip_edit_check=True)

    assert result["ok"] and result["changed"]
    assert json.loads(items["I1"].extra_json) == after
