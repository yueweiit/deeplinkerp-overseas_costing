"""Regression: observed archived approval 202609032107000062462 (2026-09-09)."""
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from overseas_costing.scripts.import_oa_logistics import extract_logistics_quote_candidates_from_approval

QUOTES = """计费重量预估875.7kg/350=2.502立方
大墨仓报价：3100元/立方（1:350），2.502立方费用：7756.2元
SISA报价：3420元/立方（1:300），2.919立方费用：9982.98元
重量 重量占比 ¥7,756.20
超队1.0项目 668.7kg 76.36% ¥5,922.77
亮甲2.0项目 77kg 8.79% ¥520.79
TK宠物用品项目130kg 14.85% ¥77.31
875.7 100%"""
GOODS = [("FL000429", 96000), ("FL000429", 4000), ("FL000427", 4000),
         ("FL000427", 96000), ("FL000428", 100000), ("FL000430", 100000),
         ("FL003377", 22000), ("CW000191", 2400)]

def approval():
    return {"source_kind": "approval_form", "source_id": "approval:LOG-6262:form",
            "process_instance_id": "LOG-6262", "approval_no": "202609032107000062462",
            "approval_role": "international_logistics", "source_label": "国际物流正文",
            "form_fields": {"货物信息Bienes": [
                {"物料编码": code, "物料名称": code, "数量": qty, "单位": "个"}
                for code, qty in GOODS], "重量Peso（KG）": 875.7, "物流报价": QUOTES},
            "approval_decisions": [{"remark": "走大墨仓", "operation_result": "AGREE",
                                    "operation_time": "2026-09-04 09:45", "user_name": "审批人"}]}

def existing_items():
    return [{"name": f"ITEM-{code}", "material_code": code, "quantity": qty,
             "actual_shipped_qty": qty, "actual_shipped_qty_mode": "DEFAULT_PURCHASE",
             "goods_value": value, "source_doc_no": "PURCHASE-1", "purchase_uom": "个",
             "source_type": "PURCHASE_EXPENSE_OA", "extra_json": "{}"}
            for code, qty, value in [("FL000427", 100000, 15100), ("FL000428", 100000, 15100),
                                     ("FL000429", 100000, 15100), ("FL000430", 100000, 15100),
                                     ("FL003377", 200000, 26000)]]

def test_quote_rate_and_total_on_same_line():
    rows = extract_logistics_quote_candidates_from_approval(approval())
    assert [(r["carrier"], r["amount"]) for r in rows] == [("大墨仓", 7756.2), ("SISA", 9982.98)]
    assert [r["unit_rate"] for r in rows] == [3100, 3420]

def test_logistics_rows_keep_duplicates_missing_purchase_and_purchase_totals():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    original = existing_items()
    before = deepcopy(original)
    proposal = build_logistics_reconciliation(original, approval())
    rows = proposal["payload"]["rows"]
    assert [(r["material_code"], int(Decimal(str(r["actual_shipped_qty"])))) for r in rows] == GOODS
    assert len({r["stable_line_key"] for r in rows}) == 8
    assert sum(Decimal(str(r.get("goods_value") or 0)) for r in rows) == Decimal("86400")
    assert sum(Decimal(str(r.get("quantity") or 0)) for r in rows) == Decimal("600000")
    assert rows[-1].get("quantity") is None and rows[-1].get("goods_value") is None
    assert original == before
    assert proposal["default_selected"] is True
    assert json.loads(rows[0]["extra_json"])["logistics_row"]["purchase_fact"]["quantity"] == 100000

def test_reconcile_again_does_not_split_or_multiply_purchase_facts():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    first = build_logistics_reconciliation(existing_items(), approval())["payload"]["rows"]
    second = build_logistics_reconciliation(first, approval())["payload"]["rows"]
    assert [(r["stable_line_key"], r["quantity"], r["goods_value"]) for r in second] == [
        (r["stable_line_key"], r["quantity"], r["goods_value"]) for r in first]


def test_authoritative_logistics_rows_soft_exclude_unmatched_purchase_materials():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144", "quantity": 1,
         "actual_shipped_qty": 1, "source_type": "PURCHASE_EXPENSE_OA", "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145", "quantity": 1,
         "actual_shipped_qty": 1, "source_type": "PURCHASE_EXPENSE_OA", "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "approval_role": "international_logistics", "approval_no": "LOG-1",
        "source_label": "国际物流正文",
        "form_fields": {"货物信息": [{"物料编码": "MWV101145", "物料名称": "IP17 PRO MAX", "数量": 1, "单位": "套"}]},
    }

    proposal = build_logistics_reconciliation(items, source)

    assert [row["material_code"] for row in proposal["payload"]["rows"]] == ["MWV101145"]
    assert proposal["payload"]["excluded_item_names"] == ["I-144"]
    assert proposal["payload"]["scope_status"] == "AUTHORITATIVE"


def test_authoritative_logistics_code_wins_without_overwriting_canonical_name():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144",
         "product_name": "薇武士 IP17 PRO", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145",
         "product_name": "薇武士 IP17 PRO MAX", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-78258:form",
        "source_hash": "source-hash-78258",
        "approval_role": "international_logistics",
        "approval_no": "202607211417000078258",
        "source_label": "国际物流正文",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "MHA超队模具",
            "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source)

    assert [row["material_code"] for row in proposal["payload"]["rows"]] == ["MWV101144"]
    assert proposal["payload"]["rows"][0]["product_name"] == "薇武士 IP17 PRO"
    assert proposal["payload"]["excluded_item_names"] == ["I-145"]
    assert proposal["payload"]["name_mismatches"] == [{
        "material_code": "MWV101144",
        "source_name": "MHA超队模具",
        "canonical_name": "薇武士 IP17 PRO",
        "item_name": "I-144",
    }]
    assert proposal["payload"]["scope_origin"] == "international_logistics"
    assert proposal["payload"]["source_fingerprint"]


def test_code_uses_shared_purchase_name_even_when_purchase_rows_are_ambiguous():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "P-1", "material_code": "SKU-1", "product_name": "规范名称",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "P-2", "material_code": "SKU-1", "product_name": "规范名称",
         "quantity": 2, "actual_shipped_qty": 2, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-NAME:form",
        "approval_role": "international_logistics", "approval_no": "LOG-NAME",
        "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "错误名称", "数量": 3, "单位": "个",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)

    assert proposal["payload"]["rows"][0]["product_name"] == "规范名称"
    assert proposal["payload"]["name_mismatches"][0]["source_name"] == "错误名称"


def test_material_code_matching_normalizes_width_case_and_whitespace():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [{
        "name": "I-144", "material_code": "MWV101144",
        "product_name": "规范名称", "quantity": 1,
        "actual_shipped_qty": 1, "extra_json": "{}",
    }]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-CODE:form",
        "approval_role": "international_logistics", "approval_no": "LOG-CODE",
        "form_fields": {"货物信息": [{
            "物料编码": " ｍｗｖ１０１１４４ ", "物料名称": "错误名称",
            "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)

    assert proposal["payload"]["rows"][0]["material_code"] == "MWV101144"
    assert proposal["payload"]["rows"][0]["product_name"] == "规范名称"


def test_conflicting_existing_names_for_one_code_are_not_replaced_by_source_name():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "P-1", "material_code": "SKU-1", "product_name": "规范名称 A",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "P-2", "material_code": "SKU-1", "product_name": "规范名称 B",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-CONFLICT:form",
        "approval_role": "international_logistics", "approval_no": "LOG-CONFLICT",
        "form_fields": {"货物信息": [{
            "物料编码": "SKU-1", "物料名称": "错误名称", "数量": 2, "单位": "个",
        }]},
    }

    assert build_logistics_reconciliation(
        items, source, reset_manual_scope=True) is None


def test_initialization_reset_does_not_protect_old_manual_rows():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "I-MANUAL", "material_code": "MWV101145", "quantity": 1,
         "actual_shipped_qty": 1, "manual_override_flag": 1,
         "manual_override_reason": "测试期人工加入", "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "approval_role": "international_logistics", "approval_no": "LOG-1",
        "source_label": "国际物流正文",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "IP17 PRO",
            "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)

    assert proposal["payload"]["excluded_item_names"] == ["I-MANUAL"]
    assert [row["material_code"] for row in proposal["payload"]["rows"]] == ["MWV101144"]


def test_initialization_reset_replaces_old_manual_quantity_with_logistics_quantity():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [{
        "name": "I-144", "material_code": "MWV101144", "quantity": 9,
        "actual_shipped_qty": 9, "actual_shipped_qty_mode": "MANUAL_CONFIRMED",
        "shipped_uom": "箱", "extra_json": "{}",
    }]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-QTY:form",
        "approval_role": "international_logistics", "approval_no": "LOG-QTY",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "IP17 PRO",
            "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)
    row = proposal["payload"]["rows"][0]

    assert row["actual_shipped_qty"] == "1"
    assert row["actual_shipped_qty_mode"] == "EXPLICIT_SOURCE"
    assert row["shipped_uom"] == "套"


def test_logistics_row_without_code_uses_only_unique_exact_normalized_name():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144",
         "product_name": "薇 武士 IP17 PRO", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145",
         "product_name": "薇武士 IP17 PRO MAX", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-NAME:form",
        "approval_role": "international_logistics", "approval_no": "LOG-NAME",
        "form_fields": {"货物信息": [{
            "物料名称": " 薇武士  ip17 pro ", "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)

    assert proposal["payload"]["scope_status"] == "AUTHORITATIVE"
    assert proposal["payload"]["rows"][0]["material_code"] == "MWV101144"
    assert proposal["payload"]["rows"][0]["product_name"] == "薇 武士 IP17 PRO"
    assert proposal["payload"]["excluded_item_names"] == ["I-145"]


@pytest.mark.parametrize("placeholder_code", ["/", "//", "无", "NEW"])
def test_logistics_placeholder_code_falls_back_to_unique_exact_name(placeholder_code):
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144",
         "product_name": "薇武士 IP17 PRO", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145",
         "product_name": "薇武士 IP17 PRO MAX", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-PLACEHOLDER:form",
        "approval_role": "international_logistics", "approval_no": "LOG-PLACEHOLDER",
        "form_fields": {"货物信息": [{
            "物料编码": placeholder_code, "物料名称": " 薇武士  ip17 pro ",
            "数量": 1, "单位": "套",
        }]},
    }

    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)

    assert proposal["payload"]["scope_status"] == "AUTHORITATIVE"
    assert [row["material_code"] for row in proposal["payload"]["rows"]] == ["MWV101144"]
    assert proposal["payload"]["excluded_item_names"] == ["I-145"]


@pytest.mark.parametrize("role", ["purchase", "payment"])
def test_logistics_placeholder_code_uses_lower_stage_identity_without_adding_rows(role):
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-HINT:form",
        "approval_role": "international_logistics", "approval_no": "LOG-HINT",
        "form_fields": {"货物信息": [{
            "物料编码": "/", "物料名称": "薇武士 IP17 PRO",
            "数量": 1, "单位": "套",
        }]},
    }
    hints = [{
        "material_code": "MWV101144", "product_name": "薇 武士 ip17 pro",
        "source_id": f"{role}:IDENTITY:1", "approval_role": role,
    }]

    proposal = build_logistics_reconciliation(
        [], source, reset_manual_scope=True, identity_hints=hints,
    )

    rows = proposal["payload"]["rows"]
    assert len(rows) == 1
    assert rows[0]["material_code"] == "MWV101144"
    assert rows[0]["product_name"] == "薇 武士 ip17 pro"
    assert rows[0]["_existing_name"] == ""
    assert proposal["payload"]["source_fact_ids"] == [
        "approval:LOG-HINT:form:1", f"{role}:IDENTITY:1",
    ]


def test_logistics_placeholder_code_with_conflicting_lower_stage_identities_is_not_authoritative():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-CONFLICT:form",
        "approval_role": "international_logistics", "approval_no": "LOG-CONFLICT",
        "form_fields": {"货物信息": [{
            "物料编码": "无", "物料名称": "同名模具", "数量": 1, "单位": "套",
        }]},
    }
    hints = [
        {"material_code": "SKU-A", "product_name": "同名模具", "source_id": "PURCHASE:1"},
        {"material_code": "SKU-B", "product_name": "同 名 模具", "source_id": "PAYMENT:1"},
    ]

    assert build_logistics_reconciliation(
        [], source, reset_manual_scope=True, identity_hints=hints,
    ) is None


def test_logistics_valid_code_uses_purchase_canonical_name_when_row_must_be_created():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-CANONICAL:form",
        "approval_role": "international_logistics", "approval_no": "LOG-CANONICAL",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "MHA超队模具",
            "数量": 1, "单位": "套",
        }]},
    }
    hints = [{
        "material_code": "MWV101144", "product_name": "薇武士 IP17 PRO",
        "source_id": "purchase:CANONICAL:1", "approval_role": "purchase",
    }]

    proposal = build_logistics_reconciliation(
        [], source, reset_manual_scope=True, identity_hints=hints,
    )

    assert proposal["payload"]["rows"][0]["product_name"] == "薇武士 IP17 PRO"
    assert proposal["payload"]["name_mismatches"] == [{
        "material_code": "MWV101144", "source_name": "MHA超队模具",
        "canonical_name": "薇武士 IP17 PRO", "item_name": "",
    }]
    assert "purchase:CANONICAL:1" in proposal["payload"]["source_fact_ids"]


def test_identity_hints_only_use_purchase_or_shipment_scoped_payment_goods():
    from overseas_costing.services.logistics_autofill_service import build_material_identity_hints

    sources = [
        {
            "source_id": "PURCHASE", "approval_role": "purchase", "available": True,
            "form_fields": {"货物信息": [{
                "物料编码": "SKU-P", "物料名称": "采购物料", "数量": 1,
            }]},
        },
        {
            "source_id": "PAYMENT-SCOPED", "approval_role": "payment", "available": True,
            "scoped_packing": True,
            "scoped_goods": [{"material_code": "SKU-M", "product_name": "付款物料", "quantity": 1}],
        },
        {
            "source_id": "PAYMENT-UNSCOPED", "approval_role": "payment", "available": True,
            "form_fields": {"货物信息": [{
                "物料编码": "SKU-X", "物料名称": "其他运单物料", "数量": 1,
            }]},
        },
    ]

    hints = build_material_identity_hints(sources)

    assert [(row["material_code"], row["source_id"]) for row in hints] == [
        ("SKU-P", "PURCHASE:1"), ("SKU-M", "PAYMENT-SCOPED:1"),
    ]


def test_logistics_row_without_code_and_ambiguous_name_is_not_authoritative():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-1", "material_code": "SKU-1", "product_name": "同名物料",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
        {"name": "I-2", "material_code": "SKU-2", "product_name": "同 名 物料",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-DUP:form",
        "approval_role": "international_logistics", "approval_no": "LOG-DUP",
        "form_fields": {"货物信息": [{"物料名称": "同名物料", "数量": 1, "单位": "个"}]},
    }

    assert build_logistics_reconciliation(items, source, reset_manual_scope=True) is None


def test_initialization_apply_can_reset_test_settlement_rows():
    from overseas_costing.services.logistics_autofill_service import (
        apply_reconciliation, build_logistics_reconciliation,
    )

    items = [
        {"name": "I-144", "material_code": "MWV101144", "product_name": "IP17 PRO",
         "quantity": 1, "actual_shipped_qty": 1,
         "extra_json": json.dumps({"settlement_cargo": {"source": "test"}})},
        {"name": "I-145", "material_code": "MWV101145", "product_name": "IP17 PRO MAX",
         "quantity": 1, "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-RESET:form",
        "approval_role": "international_logistics", "approval_no": "LOG-RESET",
        "form_fields": {"货物信息": [{
            "物料编码": "MWV101144", "物料名称": "IP17 PRO",
            "数量": 1, "单位": "套",
        }]},
    }
    proposal = build_logistics_reconciliation(items, source, reset_manual_scope=True)
    writes = []

    class DB:
        @staticmethod
        def set_value(doctype, name, values, **_kwargs):
            writes.append((doctype, name, values))

    apply_reconciliation(
        SimpleNamespace(db=DB()), proposal, batch="B-1", version="V-1",
        current=items, run_id="RESET-1", update_batch_count=False,
        initialization_reset=True,
    )

    excluded = next(values for doctype, name, values in writes
                    if doctype == "Overseas Cost Item" and name == "I-145")
    assert excluded["is_excluded"] == 1


def test_ai_review_uses_authoritative_logistics_scope_when_it_contracts_existing_rows():
    from overseas_costing.services.material_ai_fill_service import _use_logistics_reconciliation

    proposal = {
        "proposal_type": "logistics_reconcile",
        "blocked": False,
        "payload": {
            "scope_status": "AUTHORITATIVE",
            "rows": [{"name": "I-145", "material_code": "MWV101145"}],
            "excluded_item_names": ["I-144"],
        },
    }

    assert _use_logistics_reconciliation(proposal, [
        {"name": "I-144", "material_code": "MWV101144", "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145", "extra_json": "{}"},
    ]) is True


def test_manual_material_outside_logistics_scope_is_retained_and_not_auto_excluded():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    items = [
        {"name": "I-144", "material_code": "MWV101144", "quantity": 1,
         "actual_shipped_qty": 1, "manual_override_flag": 1,
         "manual_override_reason": "人工确认本次一起发货", "extra_json": "{}"},
        {"name": "I-145", "material_code": "MWV101145", "quantity": 1,
         "actual_shipped_qty": 1, "extra_json": "{}"},
    ]
    source = {
        "source_kind": "approval_form", "source_id": "approval:LOG-1:form",
        "approval_role": "international_logistics", "approval_no": "LOG-1",
        "source_label": "国际物流正文",
        "form_fields": {"货物信息": [{"物料编码": "MWV101145", "物料名称": "IP17 PRO MAX", "数量": 1, "单位": "套"}]},
    }

    proposal = build_logistics_reconciliation(items, source)

    assert {row["material_code"] for row in proposal["payload"]["rows"]} == {"MWV101144", "MWV101145"}
    assert proposal["payload"]["excluded_item_names"] == []
    retained = next(row for row in proposal["payload"]["rows"] if row["material_code"] == "MWV101144")
    assert retained["_review_origin"] == "current"


def test_authoritative_scope_backfill_plan_restores_only_previous_system_exclusions():
    from overseas_costing.services import logistics_autofill_service as service

    proposal = {
        'payload': {
            'original_item_names':['I-KEEP','I-REMOVE','I-RESTORE'],
            'excluded_item_names':['I-REMOVE'],
            'rows':[
                {'_existing_name':'I-KEEP'},
                {'_existing_name':'I-RESTORE'},
            ],
        },
    }
    items = [
        {'name':'I-KEEP','is_excluded':0},
        {'name':'I-REMOVE','is_excluded':0},
        {'name':'I-RESTORE','is_excluded':1,
         'exclusion_reason':service.AUTO_SCOPE_EXCLUSION_REASON},
        {'name':'I-MANUAL-EXCLUDED','is_excluded':1,
         'exclusion_reason':'人工排除'},
    ]

    plan=service.plan_authoritative_scope_membership(items,proposal)

    assert plan=={
        'exclude':['I-REMOVE'],
        'restore':['I-RESTORE'],
        'active_item_names':['I-KEEP','I-RESTORE'],
        'create_rows':[],
        'name_mismatches':[],
        'scope_status':'AUTHORITATIVE',
        'scope_origin':'international_logistics',
        'source_fact_ids':[],
        'source_fingerprint':'',
        'plan_hash':plan['plan_hash'],
    }


def test_scope_reset_plan_includes_manually_excluded_rows_and_missing_logistics_rows():
    from overseas_costing.services import logistics_autofill_service as service

    items = [
        {'name':'I-KEEP','material_code':'SKU-KEEP','is_excluded':0},
        {'name':'I-OLD-MANUAL','material_code':'SKU-OLD','is_excluded':1,
         'exclusion_reason':'人工排除'},
    ]
    proposal = {
        'payload': {
            'scope_status':'AUTHORITATIVE',
            'scope_origin':'international_logistics',
            'source_fact_ids':['LOG:1','LOG:2'],
            'source_fingerprint':'SOURCE-FP',
            'name_mismatches':[],
            'original_item_names':['I-KEEP','I-OLD-MANUAL'],
            'excluded_item_names':['I-OLD-MANUAL'],
            'rows':[
                {'_existing_name':'I-KEEP','material_code':'SKU-KEEP'},
                {'_existing_name':'','name':'draft-new','material_code':'SKU-NEW'},
            ],
        },
    }

    plan = service.plan_authoritative_scope_membership(
        items, proposal, reset_all_existing=True)

    assert plan['exclude'] == []
    assert plan['restore'] == []
    assert [row['material_code'] for row in plan['create_rows']] == ['SKU-NEW']
    assert plan['active_item_names'] == ['I-KEEP']
    assert plan['scope_status'] == 'AUTHORITATIVE'
    assert plan['source_fact_ids'] == ['LOG:1','LOG:2']
    assert plan['source_fingerprint'] == 'SOURCE-FP'
    assert plan['plan_hash']

def test_carrier_decision_selects_final_freight_without_choosing_cheapest():
    from overseas_costing.services.material_ai_fill_service import build_approval_fee_proposals
    rows = build_approval_fee_proposals(approval(), transport_mode="SEA", existing_fees=[
        {"logical_fee_key": "international_sea_freight", "amount_status": "ACTUAL", "amount": 2004}])
    assert len(rows) == 1
    assert Decimal(rows[0]["payload"]["amount"]) == Decimal("7756.20")
    assert rows[0]["default_selected"] is True
    source = approval()
    source["approval_decisions"][0]["remark"] = "走SISA"
    rows = build_approval_fee_proposals(source, transport_mode="SEA")
    assert len(rows) == 1 and Decimal(rows[0]["payload"]["amount"]) == Decimal("9982.98")

def test_browser_cannot_edit_logistics_reconciliation_payload():
    from overseas_costing.services.material_ai_fill_service import validate_source_review_application
    import pytest
    with pytest.raises(ValueError, match="物流行"):
        validate_source_review_application([
            {"proposal_id": "R", "proposal_type": "logistics_reconcile", "payload": {"rows": []}}],
            ["R"], {"R": {"rows": [{"material_code": "FORGED"}]}}, [])

def test_supplement_budget_interrupts_even_when_inner_code_catches_exception():
    import time
    from overseas_costing.services.logistics_autofill_service import run_supplement
    def slow():
        try:
            time.sleep(0.2)
        except Exception:
            return {"ok": True}
    start = time.monotonic()
    result = run_supplement(slow, seconds=0.01)
    assert result["ok"] is False and "超时" in result["warning"]
    assert time.monotonic() - start < 0.15

def test_comment_reads_server_manifest_without_reloading_approval(monkeypatch):
    from overseas_costing.services import material_ai_fill_service as service, packing_source_service
    def unexpected(*args, **kwargs):
        raise AssertionError("must not reload complete approval")
    monkeypatch.setattr(packing_source_service, "_find_comment_source", unexpected)
    _, document = service._read_source([], {"source_kind": "approval_comment", "comment_text": "走大墨仓"})
    assert document["text"] == "走大墨仓"


def test_read_packing_comment_with_one_exact_sku_defaults_only_that_sku():
    from overseas_costing.services import material_ai_fill_service as service
    items = [
        {"name": "I1", "stable_line_key": "L1", "material_code": "MWV101144", "product_name": "薇武士", "unit": "件"},
        {"name": "I2", "stable_line_key": "L2", "material_code": "MOLD-1", "product_name": "模具", "unit": "套"},
        {"name": "I3", "stable_line_key": "L3", "material_code": "CASE-1", "product_name": "手机壳", "unit": "个"},
    ]
    comment = (
        "DHL 单号1841361513\nETA 2026.7.27已到工厂\n发货明细：\n"
        "薇武士 MWV101144 IP17PRO -TPU\n规格33*20*23,重量：42.05kg\n"
        "1套模具+3个手机壳"
    )

    candidates, document = service._read_source(
        items,
        {"source_kind": "approval_comment", "source_id": "COMMENT", "source_hash": "HASH", "comment_text": comment},
    )

    assert candidates == []
    group = document["packing_group_candidates"][0]
    assert group["gross_weight_kg"] == "42.05"
    assert group["volume_m3"] == "0.01518"
    assert group["package_count"] is None
    assert group["member_keys"] == ["L1"]
    assert group["default_selected"] is True
    assert group["weight_basis"] == "inferred_unqualified_weight_as_gross"
    assert group["assignment_options"] == [{
        "assignment_id": group["assignment_options"][0]["assignment_id"],
        "mode": "single_item",
        "member_keys": ["L1"],
        "label": "仅归属 MWV101144",
        "default_selected": True,
        "can_apply": True,
        "resolution_reason": "将评论中的整组装箱事实仅用于该物料。",
    }]


def test_plus_sign_in_cargo_description_never_offers_a_group_assignment():
    from overseas_costing.services import material_ai_fill_service as service
    items = [
        {"name": "I1", "stable_line_key": "L1", "material_code": "MWV101144", "product_name": "薇武士IP17 PRO"},
        {"name": "I2", "stable_line_key": "L2", "material_code": "MWV101145", "product_name": "薇武士IP17 PRO MAX"},
    ]
    comment = (
        "DHL 单号1841361513\n发货明细：\n"
        "薇武士 MWV101144 IP17PRO -TPU\n规格33*20*23,重量：42.05kg\n"
        "1套模具+3个手机壳"
    )

    _candidates, document = service._read_source(
        items,
        {"source_kind": "approval_comment", "source_id": "COMMENT", "source_hash": "HASH", "comment_text": comment},
    )

    group = document["packing_group_candidates"][0]
    options = group["assignment_options"]
    assert [(option["mode"], option["member_keys"]) for option in options] == [
        ("single_item", ["L1"]),
    ]
    assert sum(bool(option["default_selected"]) for option in options) == 1
    assert next(option for option in options if option["default_selected"])["mode"] == "single_item"
    assert all(option["mode"] != "one_box_group" for option in options)
    assert group["can_apply"] is True
    assert group["needs_member_confirmation"] is False


def test_two_exact_codes_without_waybill_or_joint_language_do_not_form_one_box_group():
    from overseas_costing.services import material_ai_fill_service as service

    items = [
        {"name": "I1", "stable_line_key": "L1", "material_code": "MWV101144", "product_name": "IP17 PRO"},
        {"name": "I2", "stable_line_key": "L2", "material_code": "MWV101145", "product_name": "IP17 PRO MAX"},
    ]
    comment = "MWV101144 MWV101145\n规格33*20*23,重量42.05kg"

    _candidates, document = service._read_source(
        items,
        {"source_kind": "approval_comment", "source_id": "COMMENT", "source_hash": "HASH", "comment_text": comment},
    )

    candidate = document["packing_group_candidates"][0]
    assert candidate["default_selected"] is False
    assert candidate["needs_member_confirmation"] is True
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def _group_candidate_for_comment(comment):
    from overseas_costing.services import material_ai_fill_service as service

    items = [
        {"name": "I1", "stable_line_key": "L1", "material_code": "MWV101144", "product_name": "IP17 PRO"},
        {"name": "I2", "stable_line_key": "L2", "material_code": "MWV101145", "product_name": "IP17 PRO MAX"},
        {"name": "I3", "stable_line_key": "L3", "material_code": "MOLD-1", "product_name": "模具"},
    ]
    _candidates, document = service._read_source(
        items,
        {"source_kind": "approval_comment", "source_id": "COMMENT", "source_hash": comment, "comment_text": comment},
    )
    return document["packing_group_candidates"][0]


def test_two_exact_skus_with_distinct_waybills_never_form_one_box_group():
    candidate = _group_candidate_for_comment(
        "MWV101144 运单号 WB11111\nMWV101145 运单号 WB22222\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])
    assert {tuple(option["member_keys"]) for option in candidate["assignment_options"]} == {
        ("L1",),
        ("L2",),
    }


def test_carrier_name_without_labeled_waybill_is_not_a_package_identity():
    candidate = _group_candidate_for_comment(
        "DHL\nMWV101144 MWV101145\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_joint_language_without_exact_material_identifiers_is_not_actionable():
    candidate = _group_candidate_for_comment(
        "与其他货物一起共同装箱\n规格33*20*23,重量42.05kg"
    )

    assert candidate["member_keys"] == []
    assert candidate["assignment_options"] == []
    assert candidate["can_apply"] is False
    assert candidate["default_selected"] is False


def test_joint_language_with_one_exact_sku_never_broadens_to_baseline():
    candidate = _group_candidate_for_comment(
        "MWV101144 与其他货物一起共同装箱\n规格33*20*23,重量42.05kg"
    )

    assert candidate["member_keys"] == ["L1"]
    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("single_item", ["L1"]),
    ]


def test_joint_language_groups_only_two_exact_identified_members():
    candidate = _group_candidate_for_comment(
        "MWV101144 与 MWV101145 共同装箱\n规格33*20*23,重量42.05kg"
    )

    assert candidate["member_keys"] == ["L1", "L2"]
    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


@pytest.mark.parametrize(
    "wording",
    [
        "不一起装箱",
        "不共同装箱",
        "未合箱",
        "不要合箱",
        "并非一起装箱",
        "不是共同装箱",
        "无需合箱",
        "不能一起装箱",
        "不可以共同装箱",
        "禁止一起装箱",
        "请勿合箱",
        "没有共同装箱",
        "不再一起装箱",
        "不得一起装箱",
        "不允许共同装箱",
        "严禁合箱",
        "分开装箱",
        "分别装箱",
        "单独装箱",
        "不需要一起装箱",
        "不可一起装箱",
        "无法一起装箱",
        "没一起装箱",
        "不能够一起装箱",
    ],
)
@pytest.mark.parametrize("waybill", ["", "DHL 单号1841361513\n"])
def test_negated_joint_language_vetoes_one_box_group_even_with_shared_waybill(wording, waybill):
    candidate = _group_candidate_for_comment(
        f"{waybill}MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_shared_waybill_still_groups_two_exact_members_without_negative_language():
    candidate = _group_candidate_for_comment(
        "DHL 单号1841361513\nMWV101144 MWV101145\n规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


def test_unrelated_negation_in_an_earlier_clause_does_not_veto_affirmative_joint_packing():
    candidate = _group_candidate_for_comment(
        "不接受破损包装；MWV101144 MWV101145 一起装箱\n规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


@pytest.mark.parametrize("wording", ["不影响一起装箱", "不妨碍共同装箱"])
def test_non_negating_construction_keeps_exact_member_joint_packing_affirmative(wording):
    candidate = _group_candidate_for_comment(
        f"MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


def test_unrelated_cargo_negative_clause_does_not_veto_later_exact_member_affirmative_clause():
    candidate = _group_candidate_for_comment(
        "其他货物不需要一起装箱；MWV101144 MWV101145 一起装箱\n"
        "规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


def test_joint_language_without_two_exact_members_in_same_clause_is_not_actionable():
    candidate = _group_candidate_for_comment(
        "MWV101144 MWV101145\n其他货物一起装箱\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_unrelated_negative_clause_does_not_veto_shared_waybill_inference():
    candidate = _group_candidate_for_comment(
        "其他货物不需要一起装箱；DHL 单号1841361513 MWV101144 MWV101145\n"
        "规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


def test_exact_member_negative_clause_vetoes_shared_waybill_inference():
    candidate = _group_candidate_for_comment(
        "DHL 单号1841361513 MWV101144 MWV101145 不需要一起装箱\n"
        "规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


@pytest.mark.parametrize("wording", ["各装一箱", "每款一箱", "各自一箱", "一箱一个产品", "一箱"])
def test_bare_or_separate_one_box_wording_never_authorizes_joint_group(wording):
    candidate = _group_candidate_for_comment(
        f"MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


@pytest.mark.parametrize(
    "wording",
    ["一箱", "每个产品一箱", "每件一箱", "各装一箱", "每款一箱", "各自一箱", "一箱一个产品"],
)
def test_ambiguous_or_separate_one_box_wording_vetoes_shared_waybill_inference(wording):
    candidate = _group_candidate_for_comment(
        f"DHL 单号1841361513 MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


@pytest.mark.parametrize(
    "wording",
    [
        "一起装箱",
        "共同装箱",
        "共同装成一箱",
        "合箱",
        "合并装箱",
        "同箱",
        "合为一箱",
        "合成一箱",
        "装在同一箱",
        "装入同一箱",
        "放在同一箱",
        "合 为 一箱",
        "装-在-同-一-箱",
    ],
)
def test_affirmative_joint_language_groups_exact_members(wording):
    candidate = _group_candidate_for_comment(
        f"MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


@pytest.mark.parametrize(
    "wording",
    ["不合为一箱", "不要装在同一箱", "不 要 装-入-同-一-箱"],
)
def test_negated_shared_one_box_construction_vetoes_shared_waybill(wording):
    candidate = _group_candidate_for_comment(
        f"DHL 单号1841361513 MWV101144 MWV101145 {wording}\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_negative_clause_for_different_member_pair_does_not_veto_candidate_pair():
    candidate = _group_candidate_for_comment(
        "MWV101144 MOLD-1 不一起装箱；MWV101144 MWV101145 一起装箱\n"
        "规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]


def test_negative_clause_for_same_member_pair_vetoes_candidate_pair():
    candidate = _group_candidate_for_comment(
        "MWV101144 MWV101145 不一起装箱；MWV101144 MWV101145 一起装箱\n"
        "规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_three_member_candidate_is_vetoed_when_it_contains_negative_pair_relationship():
    candidate = _group_candidate_for_comment(
        "MWV101144 MOLD-1 不一起装箱；"
        "MWV101144 MWV101145 MOLD-1 一起装箱\n规格33*20*23,重量42.05kg"
    )

    assert candidate["default_selected"] is False
    assert all(option["mode"] != "one_box_group" for option in candidate["assignment_options"])


def test_negative_superset_relationship_does_not_veto_candidate_subset():
    candidate = _group_candidate_for_comment(
        "MWV101144 MWV101145 MOLD-1 不一起装箱；"
        "MWV101144 MWV101145 一起装箱\n规格33*20*23,重量42.05kg"
    )

    assert [(option["mode"], option["member_keys"]) for option in candidate["assignment_options"]] == [
        ("one_box_group", ["L1", "L2"]),
    ]

def test_worker_previews_eight_rows_and_final_freight_without_business_write(monkeypatch):
    from overseas_costing.services import material_ai_fill_service as service
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository
    from overseas_costing.services.source_review_manifest_service import prepare_source_manifest
    repo = _LifecycleRepository(status="QUEUED")
    repo.sources = [approval()]
    repo.get_items = lambda *args: existing_items()
    repo.get_context = lambda *args: {"batch": "B1", "version": "V1", "transport_mode": "SEA"}
    manifest = prepare_source_manifest(repo.sources)
    repo.run.update(proposal_version=1, source_manifest_json=manifest,
                    input_fingerprint=service._source_review_fingerprint(
                        "B1", "V1", existing_items(), manifest, "", context=repo.get_context("B1", "V1")))
    monkeypatch.setattr(service, "_call_source_review_ai", lambda *args, **kwargs: {"ok": False, "proposals": [], "warning": "AI 不可用"})
    result = service.execute_material_ai_fill("RUN-1", repository=repo)
    assert result["status"] == "READY", repo.run.get("error_message")
    preview = repo.run["draft_json"]["autofill_preview"]
    assert len(preview["items"]) == 8
    assert Decimal(preview["fees"][0]["amount"]) == Decimal("7756.20")
    assert repo.applied == []

def test_packing_matches_duplicate_sku_by_shipping_quantity():
    from overseas_costing.services.material_ai_fill_service import _projection_candidates
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    items = build_logistics_reconciliation(existing_items(), approval())["payload"]["rows"]
    preview = {"material_rows": [{"source_row": 3, "material_code": "FL000429", "quantity": 96000, "gross_weight_kg": 96},
                                  {"source_row": 4, "material_code": "FL000429", "quantity": 4000, "gross_weight_kg": 4}], "groups": []}
    result = _projection_candidates(items, {"source_id": "X", "source_kind": "approval_attachment"}, preview)
    gross = [r for r in result if r["fieldname"] == "gross_weight_kg"]
    assert [(r["item_name"], Decimal(str(r["suggested_value"]))) for r in gross] == [(items[0]["name"], Decimal(96)), (items[1]["name"], Decimal(4))]


def test_manual_aggregate_shipping_is_not_copied_to_two_split_rows():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    items = existing_items()
    items[2].update(actual_shipped_qty_mode="MANUAL_CONFIRMED", actual_shipped_qty=100000)
    proposal = build_logistics_reconciliation(items, approval())
    assert proposal["default_selected"] is False
    assert proposal["payload"]["unresolved"]
    assert proposal["payload"]["rows"] == items


def test_reanalysis_preserves_manual_purchase_correction_after_split():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    rows = build_logistics_reconciliation(existing_items(), approval())["payload"]["rows"]
    rows[0].update(goods_value=20000, manual_override_flag=1)
    again = build_logistics_reconciliation(rows, approval())["payload"]["rows"]
    assert again[0]["goods_value"] == 20000


def test_negated_or_cancelled_carrier_decisions_are_not_adopted():
    from overseas_costing.services.logistics_autofill_service import selected_carrier
    quotes = [{"carrier": "大墨仓"}, {"carrier": "SISA"}]
    for remark in ["不走大墨仓", "不采用大墨仓", "暂不选择SISA", "走大墨仓的方案取消"]:
        assert selected_carrier(quotes, [{"result": "AGREE", "remark": remark}]) == ""


def test_apply_reconciliation_preserves_names_and_stable_ids_without_committing():
    from types import SimpleNamespace
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation, apply_reconciliation
    items = existing_items()
    for row in items:
        row["stable_line_key"] = "stable-" + row["name"]
    proposal = build_logistics_reconciliation(items, approval())
    writes, inserts = [], []
    class Doc:
        def __init__(self, payload):
            self.payload = payload
            self.name = "NEW-" + str(len(inserts))
        def insert(self, **kwargs):
            inserts.append(self.payload)
            return self
    fake = SimpleNamespace(db=SimpleNamespace(set_value=lambda *args, **kwargs: writes.append(args)), get_doc=Doc)
    created = apply_reconciliation(fake, proposal, batch="B1", version="V1", current=items, run_id="R1")
    assert len(created) == 3
    updates = [args for args in writes if args[0] == "Overseas Cost Item"]
    assert {args[1] for args in updates} == {row["name"] for row in items}
    assert all(args[2]["stable_line_key"] == "stable-" + args[1] for args in updates)
    assert all(row["actual_shipped_qty_mode"] == "EXPLICIT_SOURCE" for row in inserts)


def test_real_archived_excel_rows_and_one_shared_carton_are_read_independently():
    """Minimal exact cell fixture from archived 9.4日指环扣双清 (2026-09-09)."""
    from overseas_costing.services.packing_parse_service import parse_packing_grid
    from overseas_costing.services.material_ai_fill_service import _projection_candidates
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    headers = ["品目编码Item code", "中文品名 Chinese Name", "申报单位", "总个数 The total number of", "总净重", "总毛重Gross weight", "总体积total capacity", "件数Number of pieces"]
    physical = [(380,388,.4002,20),(9,9.7,.00864,1),(0,0,0,None),(31.2,32,.032016,4),
                (145,150,.0828,10),(85,89,.04071,5),(76,77,.16687,2),(125,130,.507375,5)]
    raw = [headers] + [[code, code, "个", qty, *values] for (code, qty), values in zip(GOODS, physical)]
    grid = {"cells": [[{"raw_value": value, "display_value": str(value) if value is not None else ""} for value in row] for row in raw],
            "merge_ranges_available": True, "merge_ranges": [{"start_row":3,"end_row":4,"start_column":8,"end_column":8}]}
    preview = parse_packing_grid(grid)
    items = build_logistics_reconciliation(existing_items(), approval())["payload"]["rows"]
    candidates = _projection_candidates(items, {"source_id":"real-xlsx","source_kind":"approval_attachment"}, preview)
    gross = [Decimal(str(row["suggested_value"])) for row in candidates if row["fieldname"] == "gross_weight_kg"]
    assert gross == [Decimal(str(row[1])) for row in physical]
    assert sum(gross) == Decimal("875.7")
    boxes = [Decimal(str(row["suggested_value"])) for row in candidates if row["fieldname"] == "package_count"]
    assert sum(boxes) == 47  # T3:T4 is one carton, not two.


def test_transaction_rolls_back_row_reconciliation_when_fee_write_fails(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from overseas_costing.services import material_ai_fill_service as service, fee_service
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    original = existing_items()
    state = {"items": deepcopy(original), "rollbacks": 0, "commits": 0, "writes": 0}
    class DB:
        def sql(self, query, *args, **kwargs):
            return [{"name": "B1", "current_version": "V1"}] if "current_version" in query else []
        def set_value(self, doctype, name, *args, **kwargs):
            state["writes"] += 1
            if doctype == "Overseas Cost Item":
                next(row for row in state["items"] if row["name"] == name).update(args[0])
        def rollback(self):
            state["items"] = deepcopy(original)
            state["rollbacks"] += 1
        def commit(self):
            state["commits"] += 1
    class Doc:
        def __init__(self, values):
            self.values = values
            self.name = "NEW-" + str(len(state["items"]))
        def insert(self, **kwargs):
            state["items"].append({**self.values, "name": self.name})
            return self
    monkeypatch.setattr(service, "frappe", SimpleNamespace(db=DB(), get_all=lambda *a, **kw: deepcopy(state["items"]), get_doc=Doc))
    monkeypatch.setattr(fee_service, "_query_rules", lambda *args: [])
    monkeypatch.setattr(fee_service, "normalize_fee_payload", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("fee failed")))
    proposals = [build_logistics_reconciliation(original, approval()), {"proposal_type": "fee_update", "payload": {"amount": "7756.20"}}]
    with pytest.raises(ValueError, match="重新分析"):
        service.FrappeMaterialAIFillRepository().apply_source_review(
            SimpleNamespace(name="R1", status="READY", source_completeness="COMPLETE"),
            proposals,
            [],
            {"batch": "B1", "version": "V1"},
        )
    assert state["items"] == original
    assert state["writes"] == 0 and state["rollbacks"] == 0 and state["commits"] == 0


def test_main_approval_archived_labelled_text_groups_matching_specs_and_preserves_trace():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    source = approval()
    source["form_fields"]["货物信息Bienes"] = "\n".join(
        f"物料编码 Código de material：{code}；物料名称（中文）Nombre del material (chino)：指环扣；规格型号Especificación / Modelo：超队；数量Cantidad：{qty}；单位Unidad：个"
        for code, qty in GOODS)
    rows = build_logistics_reconciliation(existing_items(), source)["payload"]["rows"]
    assert [(row["material_code"], int(row["actual_shipped_qty"])) for row in rows] == [
        ('FL000429', 100000), ('FL000427', 100000), ('FL000428', 100000),
        ('FL000430', 100000), ('FL003377', 22000), ('CW000191', 2400),
    ]
    assert sum(len(json.loads(row['extra_json'])['logistics_cargo_group']['source_rows']) for row in rows) == 8


def test_duplicate_sku_quantity_mismatch_is_not_forced_by_position():
    from overseas_costing.services.material_ai_fill_service import _projection_candidates
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation
    items = build_logistics_reconciliation(existing_items(), approval())["payload"]["rows"]
    preview = {"material_rows": [{"source_row":2,"material_code":"FL000429","quantity":60000,"gross_weight_kg":60},
                                 {"source_row":3,"material_code":"FL000429","quantity":40000,"gross_weight_kg":40}]}
    assert _projection_candidates(items, {"source_id":"X"}, preview) == []
    assert len(preview["autofill_warnings"]) == 2


def test_stalled_status_exposes_retry_even_when_revision_is_unchanged(monkeypatch):
    from overseas_costing.services import material_ai_fill_service as service
    from overseas_costing.tests.test_material_ai_fill_service import _LifecycleRepository
    repo = _LifecycleRepository(status="RUNNING")
    repo.run["modified"] = "2026-09-09 12:00:00"
    monkeypatch.setattr(service, "_now", lambda: "2026-09-09 12:04:00")
    result = service.get_material_ai_fill_status("B1", "RUN-1", after_revision=3, repository=repo)
    assert result["stalled"] is True
    assert result["unchanged"] is False
    assert result["status"] == "RUNNING"  # Status reads don't mutate runs.
