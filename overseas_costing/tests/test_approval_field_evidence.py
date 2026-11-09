"""Scope, row identity, and field evidence for deterministic OA review."""
from copy import deepcopy

import pytest

from overseas_costing.services import material_ai_fill_service as ai
from overseas_costing.services import supplier_resolution_service as suppliers
from overseas_costing.services.purchase_value_evidence import extract_row_fact
from overseas_costing.services.source_review_extract_service import build_system_approval_proposals


TABLE = "货物信息Bienes"
PROJECT = "项目归属Pertenencia del proyecto"


def row(**changes):
    return {
        "物料编码 Código de material": "A-1",
        "数量Cantidad": 1,
        "单位Unidad": "套",
        "货值Valor de mercancía": 7365,
        "币种Moneda": "人民币RMB",
        **changes,
    }


def approval(*rows, role="international_logistics", **fields):
    return {
        "source_id": "LOG",
        "document_id": "D",
        "approval_role": role,
        "source_kind": "approval_form",
        "form_fields": {TABLE: list(rows), **fields},
    }


def normalized(items, source, proposals=None):
    if proposals is None:
        proposals = build_system_approval_proposals(items, source)
        trusted = {proposal["proposal_id"] for proposal in proposals}
    else:
        trusted = set()
    for proposal in proposals:
        for ref in proposal["source_refs"]:
            ref["document_id"] = "D"
    return ai.normalize_source_review_proposals(
        proposals, items, [{**source, "source_ref": {"source_id": "LOG"}}],
        trusted_system_proposal_ids=trusted,
    )


def test_verified_oa_cells_and_unique_header_keep_individual_provenance(monkeypatch):
    item = {"name": "I", "material_code": "A-1"}
    source = approval(row(**{
        "物料名称": "模具", "规格型号": "TPU", "收件人Destinatario": "RECEIVER",
        "供应商Proveedor": "Mold Vendor", "净重Peso Neto": "20 kg",
        "毛重Peso Bruto": "21.37 kg", "箱数": "1",
        "体积 m³": "0.012 m³",
    }), **{PROJECT: [{"name": "LOCAL-TOOLING 模具项目", "id": "P1"}]})
    before = deepcopy(source)
    monkeypatch.setattr(suppliers, "load_active_suppliers", lambda: [
        {"name": "SUP-001", "supplier_name": "Mold Vendor", "disabled": 0},
    ])

    proposal, = normalized([item], source)

    assert proposal["payload"]["fields"] == {
        "actual_shipped_qty": "1", "shipped_uom": "套", "goods_value": "7365",
        "product_name": "模具", "spec_model": "TPU", "recipient": "RECEIVER",
        "supplier": "SUP-001", "project_collection": "LOCAL-TOOLING 模具项目",
        "net_weight_kg": "20", "gross_weight_kg": "21.37",
        "package_count": "1", "volume_m3": "0.012",
    }
    proof = proposal["_review_verified_fields"]
    assert proof["supplier"]["raw_value"] == "Mold Vendor"
    assert proof["supplier"]["canonical_supplier"] == "SUP-001"
    assert proof["supplier"]["source_refs"][0]["row"] == 1
    assert proof["supplier"]["source_refs"][0]["column"] == "供应商Proveedor"
    assert proof["project_collection"]["scope"] == "header"
    assert proof["project_collection"]["source_refs"][0]["field"] == PROJECT
    assert "_review_verified_fields" not in ai._public_ai_payload(proposal)
    assert source == before


@pytest.mark.parametrize("source_project", [
    [{"name": "First", "id": "P1"}, {"name": "Second", "id": "P2"}],
    "",
])
def test_no_default_for_ambiguous_project_or_supplier_and_no_inferred_dimensions(monkeypatch, source_project):
    item = {"name": "I", "material_code": "A-1"}
    source = approval(row(**{"供应商Proveedor": "Mold Vendor", "重量Peso（KG）": "21.37",
                             "规格型号": "33*20*23", "体积 m³": "22 L"}),
                      **{PROJECT: source_project, "物流报价Cotización de logística": "尺寸33*20*23，重量21.37kg"})
    monkeypatch.setattr(suppliers, "load_active_suppliers", lambda: [
        {"name": "SUP-001", "supplier_name": "Mold Vendor", "disabled": 0},
        {"name": "SUP-002", "supplier_name": "Mold Vendor", "disabled": 0},
    ])
    proposal, = normalized([item], source)
    fields = proposal["payload"]["fields"]
    assert fields["gross_weight_kg"] == "21.37"
    assert not ({"net_weight_kg", "package_count", "volume_m3", "supplier", "project_collection"} & fields.keys())


def test_row_project_wins_over_single_form_project():
    item = {"name": "I", "material_code": "A-1"}
    proposal, = normalized([item], approval(row(**{PROJECT: "Row Project"}), **{PROJECT: "Header Project"}))
    assert proposal["payload"]["fields"]["project_collection"] == "Row Project"
    assert proposal["_review_verified_fields"]["project_collection"]["scope"] == "row"


def test_model_cannot_add_protected_identity_or_supplier_by_faking_proof():
    item = {"name": "I", "material_code": "A-1"}
    source = approval(row(**{"物料名称": "模具"}))
    proposal = {
        "proposal_id": "AI",
        "proposal_type": "item_update",
        "target_item_name": "I",
        "result_origin": "SYSTEM",
        "confidence": 1,
        "source_refs": [{"field": TABLE, "row": 1}],
        "_review_verified_fields": {"supplier": {"raw_value": "FAKE"}},
        "payload": {"fields": {"supplier": "FAKE", "product_name": "invented"}},
    }

    assert normalized([item], source, [proposal]) == []


@pytest.mark.parametrize("amount", [0, "0", "7365 CNY"])
def test_confirmable_goods_value_requires_consistent_currency_and_positive_denominator(amount):
    goods = row(**{"货值Valor de mercancía": amount, "币种": "CNY"})
    fact = extract_row_fact(goods, role="international_logistics")
    assert fact and fact["currency"] == "RMB"
    assert fact["explicit_zero"] is (amount in (0, "0"))
    assert extract_row_fact({**goods, "币种": "USD"}, role="international_logistics", fx_rates={"USD": 7}) is None
    assert extract_row_fact({**goods, "数量Cantidad": 0}, role="international_logistics") is None


def test_generic_payment_or_freight_total_cannot_be_used_as_goods_value():
    goods = row()
    goods["总金额Monto Total"] = goods.pop("货值Valor de mercancía")
    assert extract_row_fact(goods, role="logistics_expense") is None
    assert extract_row_fact(goods, role="payment") is None
    assert extract_row_fact(goods, role="purchase")["amount_rmb"] == "7365"
    source = {"source_id": "P", "document_id": "D", "approval_role": "payment",
              "form_fields": {"运费明细": [row()]}}
    assert build_system_approval_proposals([{"name": "I", "material_code": "A-1"}], source) == []


def test_repeated_unresolved_sku_keeps_two_readonly_source_rows():
    item = {"name": "I", "material_code": "A-1"}
    proposals = normalized([item], approval(row(), row()))
    assert len(proposals) == 2
    assert {p["source_refs"][0]["row"] for p in proposals} == {1, 2}
    assert all(p.get("_review_unresolved_reason") for p in proposals)
    assert all(not p.get("_purchase_value_fact") for p in proposals)


def test_scope_and_value_keep_actual_ship_quantity_separate_from_purchase_basis():
    from overseas_costing.services.logistics_autofill_service import build_logistics_reconciliation

    item = {"name": "I", "material_code": "A-1"}
    source = approval(row(**{"数量Cantidad": 10, "实际发货数量": 3, "发货单位": "套"}))

    scope = build_logistics_reconciliation([item], source)["payload"]["rows"][0]
    proposal, = normalized([item], source)

    assert scope["actual_shipped_qty"] == "3"
    assert proposal["payload"]["fields"]["actual_shipped_qty"] == "3"
    assert proposal["_purchase_value_fact"]["quantity"] == "10"


def test_currency_conflict_retains_readable_total_without_discarding_other_fields():
    item = {"name": "I", "material_code": "A-1"}
    fields, money = normalized([item], approval(row(**{"币种": "USD", "物料名称": "模具"})))
    assert fields["payload"]["fields"]["product_name"] == "模具"
    assert money["payload"]["fields"] == {"goods_value": "7365"}
    assert "币种相互冲突" in money["_review_unresolved_reason"]
    assert not money.get("_purchase_value_fact")
    assert not money["default_selected"]


def test_serialized_rowvalue_and_sparse_locator_keep_evidence_on_the_exact_row():
    import json

    item = {"name": "I", "material_code": "A-1"}
    cells = [{"label": label, "value": value} for label, value in row(**{"收件人": "甲"}).items()]
    source = approval({}, {"rowNumber": "OA-22", "rowValue": json.dumps(cells, ensure_ascii=False)})

    proposal, = normalized([item], source)
    assert proposal["source_refs"][0]["row"] == 2
    assert proposal["_purchase_value_fact"]["source_refs"][0]["source_row_id"] == "OA-22"
    assert proposal["_review_verified_fields"]["recipient"]["source_refs"][0]["row"] == 2


def test_model_header_ref_cannot_bind_same_amount_to_goods_table():
    item = {"name": "I", "material_code": "A-1"}
    source = approval(row(), **{"付款总额": "7365 RMB"})
    proposal = {
        "proposal_id": "AI-HEADER",
        "proposal_type": "item_update",
        "target_item_name": "I",
        "confidence": 1,
        "source_refs": [{"field": "付款总额"}],
        "payload": {"fields": {"goods_value": 7365}},
    }

    matches = normalized([item], source, [proposal])
    assert not any(match.get("_purchase_value_fact") for match in matches)


def test_deterministic_item_evidence_keeps_versioned_source_identity_and_indexes_once(monkeypatch):
    from overseas_costing.services import source_review_extract_service as extractor

    items = [{"name": "I1", "material_code": "A-1"}, {"name": "I2", "material_code": "A-2"}]
    source = approval(row(), row(**{"物料编码 Código de material": "A-2", "收件人": "甲"}))
    source["_review_source_identity"] = {"source_id": "LOG", "source_hash": "h", "source_revision": "r"}
    original = extractor.match_approval_goods_rows
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(extractor, "match_approval_goods_rows", counted)
    proposals = normalized(items, source)

    assert len(proposals) == 2
    assert len(calls) == 2  # One deterministic source read and one server validation index.
    assert all(p["_purchase_value_fact"]["_review_source_identity"]["source_revision"] == "r" for p in proposals)
    assert proposals[1]["_review_verified_fields"]["recipient"]["_review_source_identity"]["source_hash"] == "h"


def test_public_row_projection_hides_internal_source_identity_but_keeps_amount():
    from overseas_costing.services.material_ai_selection_service import public_preview

    item = {"name": "I", "material_code": "A-1"}
    source = approval(row())
    source["_review_source_identity"] = {"source_hash": "INTERNAL-FINGERPRINT", "source_revision": "private-rev"}
    proposal, = normalized([item], source)
    fact = proposal["_purchase_value_fact"]

    public = public_preview({"rows": [{
        "shipment_value_rmb": fact["amount_rmb"],
        "adopted_price": {"evidence": fact},
        "shipment_valuation": {"input_evidence": fact},
    }]})
    [projected] = public["rows"]
    assert projected["shipment_value_rmb"] == "7365"
    assert "_review_source_identity" not in projected["adopted_price"]["evidence"]
    assert "_review_source_identity" not in projected["shipment_valuation"]["input_evidence"]
    assert "INTERNAL-FINGERPRINT" not in str(public)
