"""Review readiness checks real saved comprehensive-cost results without database writes."""

from copy import deepcopy
import importlib
import json

import pytest

from overseas_costing.services import cost_preview_service, cost_trial_ai_service, fee_service


def saved_context():
    batch = {"name": "B-1", "current_version": "V-1", "status": "Calculated",
             "confirm_status": "Pending", "subsidiary_code": "MX", "transport_mode": "SEA",
             "estimated_total_cost_rmb": "130.00", "source_status": {"purchase_approval_sync_state": "missing"}}
    values = {"name": "I-1", "row_no": 1, "stable_line_key": "line-1", "material_code": "SKU-1",
              "product_name": "Product", "unit": "件", "purchase_uom": "件", "unit_price_uom": "件",
              "quantity": 10.0, "actual_shipped_qty_mode": "DEFAULT_PURCHASE", "goods_value": 100.0,
              "gross_weight_kg": 1.0, "volume_m3": 1.0, "chargeable_weight_kg": 1.0}
    items = [{field: values.get(field) for field in cost_preview_service.COST_INPUT_FIELDS}]
    rules = []
    for index, template in enumerate(fee_service.build_default_fee_templates("SEA")):
        values = {**template, "name": f"F-{index}", "batch": "B-1", "version": "V-1",
                  "amount_status": "ACTUAL" if index == 0 else "NOT_INCURRED", "amount": 30.0 if index == 0 else 0.0,
                  "currency": "RMB", "amount_revision": "manual:1", "scope_revision": ""}
        rules.append({field: values.get(field) for field in fee_service._rule_fields()})
    version = {"name": "V-1", "batch": "B-1", "version_code": "EST-001", "status": "Active",
               "fx_usd_to_rmb": 7.0, "fx_rmb_to_mxn": 2.5}
    evidence = []
    context = {"batch": batch, "version": version, "items": items, "fees": rules, "evidence": evidence}
    save_result(context)
    return context


def save_result(context):
    version = context["version"]
    inputs = [{field: row.get(field) for field in cost_preview_service.COST_INPUT_FIELDS} for row in context["items"]]
    from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
    inputs = project_packing_groups(inputs, groups_from_version(version))["items"]
    fx = {key: version.get(key) for key in ("fx_usd_to_rmb", "fx_rmb_to_mxn")}
    fees = fee_service.compose_fee_worklist_rows(context["fees"], context["batch"]["transport_mode"])
    saved = cost_preview_service.build_saved_cost_data(
        inputs,
        fees,
        fx,
        context["batch"]["transport_mode"],
        fee_components=context.get("fee_components") or [],
    )
    version["calculated_at"] = "2026-09-08 10:00:00"
    saved["summary_snapshot"]["calculated_at"] = version["calculated_at"]
    version["summary_snapshot_json"] = json.dumps(saved["summary_snapshot"])
    updates = {row["name"]: row for row in saved["item_updates"]}
    for item in context["items"]:
        item.update(updates[item["name"]])
    context["batch"]["estimated_total_cost_rmb"] = saved["summary"]["total_cost_rmb"]


def evaluate(context):
    return importlib.import_module("overseas_costing.services.cost_review_service").evaluate_review_readiness(**context)


def codes(result):
    return {row["code"] for row in result["review_blockers"]}


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("missing_value", [False, True])
def test_unavailable_adopted_freight_blocks_only_readiness_without_restoring_estimate(confirmed, missing_value):
    from overseas_costing.services.logistics_settlement.fee_policy import FeeSelectionError

    context = saved_context()
    source = {"root_kind": "logistics", "separate_adoption": True, "fingerprint": "changed",
              "freight": {"selected": True, "available": False, "claims": []}, "packing": {}}
    context["source_context"] = source
    if missing_value:
        context["items"][0].update(goods_value=None, unit_price=None)
    if confirmed:
        context["batch"]["confirm_status"] = "Confirmed"
        context["version"]["confirmed_at"] = "2026-09-09 10:00:00"
    before = deepcopy(context)
    result = evaluate(context)
    assert result["review_state"] == ("confirmed" if confirmed else "processing")
    assert codes(result) == ({"FEE_SOURCE_INVALID", "GOODS_VALUE_MISSING"} if missing_value else {"FEE_SOURCE_INVALID"})
    assert result["issue_codes"] == (["purchase", "logistics"] if missing_value else ["logistics"])
    assert result["primary_action"] == ("view" if confirmed else "supplement" if missing_value else "supplement_fees")
    assert result["cost_review_started"] is True
    assert result["cost_review_eligible"] is (not missing_value)
    assert result["result_is_current"] is False
    assert result["shipment_goods_value_rmb"] is None
    assert context == before
    service = importlib.import_module("overseas_costing.services.cost_review_service")
    with pytest.raises(FeeSelectionError):
        service.project_review_inputs(**{key: context[key] for key in ("batch", "version", "items", "fees", "source_context")})


def test_review_readiness_does_not_hide_unrelated_projection_errors(monkeypatch):
    service = importlib.import_module("overseas_costing.services.cost_review_service")

    def invalid_projection(**kwargs):
        raise ValueError("unrelated projection failure")

    monkeypatch.setattr(service, "project_review_inputs", invalid_projection)
    with pytest.raises(ValueError, match="unrelated projection failure"):
        evaluate(saved_context())


def test_fee_source_blocker_keeps_current_expense_merchandise_out_of_legacy_purchase_checks():
    context = saved_context()
    source = {"root_kind": "expense", "separate_adoption": True, "fingerprint": "current",
              "available": True, "approved": True, "freight": {"selected": True, "available": False}, "packing": {}}
    context["source_context"] = source
    context["batch"]["source_status"] = {"purchase_approval_sync_state": "invalid"}
    context["items"][0]["extra_json"] = {
        "effective_logistics_source": source, "settlement_cargo": {"merchandise_price": {"present": True}}}
    result = evaluate(context)
    assert codes(result) == {"FEE_SOURCE_INVALID", "GOODS_VALUE_MISSING"}
    assert "PURCHASE_SOURCE_INVALID" not in codes(result)
    assert result["issue_codes"] == ["purchase", "logistics"]
    assert result["primary_action"] == "supplement"


def test_current_saved_result_is_ready_with_complete_physical_basis_and_missing_sync_marker():
    context = saved_context()
    before = deepcopy(context)
    result = evaluate(context)
    assert result["review_state"] == "ready"
    assert result["result_is_current"] is True
    assert result["review_blockers"] == []
    assert result["primary_action"] == "review"
    assert {row["code"] for row in result["review_warnings"]} == {"EVIDENCE_MISSING"}
    assert context == before


def test_saved_result_with_separate_logistics_source_is_immediately_current():
    context = saved_context()
    source_context = {
        "root_kind": "logistics",
        "available": True,
        "approved": False,
        "invalid": False,
        "separate_adoption": True,
        "fingerprint": "source-fingerprint",
        "source_snapshot": "source-snapshot",
        "freight": {"selected": False},
        "packing": {},
    }
    context["source_context"] = source_context
    inputs = [
        {field: row.get(field) for field in cost_preview_service.COST_INPUT_FIELDS}
        for row in context["items"]
    ]
    from overseas_costing.services.effective_source_values import project_source_values
    inputs = [project_source_values(row, source_context) for row in inputs]
    fx = {
        key: context["version"].get(key)
        for key in ("fx_usd_to_rmb", "fx_rmb_to_mxn")
    }
    fees = fee_service.compose_fee_worklist_rows(
        context["fees"],
        context["batch"]["transport_mode"],
        source_context=source_context,
    )
    saved = cost_preview_service.build_saved_cost_data(
        inputs,
        fees,
        fx,
        context["batch"]["transport_mode"],
    )
    context["version"]["calculated_at"] = "2026-09-20 21:14:05"
    saved["summary_snapshot"]["calculated_at"] = context["version"]["calculated_at"]
    context["version"]["summary_snapshot_json"] = json.dumps(saved["summary_snapshot"])
    updates = {row["name"]: row for row in saved["item_updates"]}
    for item in context["items"]:
        item.update(updates[item["name"]])
    context["batch"]["estimated_total_cost_rmb"] = saved["summary"]["total_cost_rmb"]

    result = evaluate(context)

    assert result["result_is_current"] is True
    assert result["review_state"] == "ready"
    assert "RESULT_STALE" not in codes(result)


@pytest.mark.parametrize("confirm_status", ["Pending", "Partially Confirmed"])
def test_latest_estimates_and_missing_evidence_are_review_warnings(confirm_status):
    context = saved_context()
    context["batch"]["confirm_status"] = confirm_status
    context["fees"][0]["amount_status"] = "ESTIMATED"
    save_result(context)
    result = evaluate(context)
    assert result["review_state"] == "ready"
    assert result["result_is_current"] is True
    assert {row["code"] for row in result["review_warnings"]} == {"ESTIMATED_AMOUNT", "EVIDENCE_MISSING"}


@pytest.mark.parametrize("change", ["dirty", "no_snapshot", "legacy", "no_time", "wrong_version", "input_changed", "fx_changed"])
def test_positive_saved_amount_does_not_make_stale_or_unverifiable_results_ready(change):
    context = saved_context()
    if change == "dirty":
        context["batch"]["status"] = "Dirty"
    elif change == "no_snapshot":
        context["version"]["summary_snapshot_json"] = "{}"
    elif change == "legacy":
        context["version"]["summary_snapshot_json"] = '{"total_cost_rmb": "130.00"}'
    elif change == "no_time":
        context["version"]["calculated_at"] = None
    elif change == "wrong_version":
        context["version"]["name"] = "V-2"
    elif change == "input_changed":
        context["items"][0]["goods_value"] = 110.0
    else:
        context["version"]["fx_usd_to_rmb"] = 7.1
    result = evaluate(context)
    assert result["review_state"] == "processing"
    assert result["result_is_current"] is False
    assert result["primary_action"] == "recalculate"


@pytest.mark.parametrize("change, code", [("missing_fee", "AMOUNT_MISSING"), ("fx", "FX_RATE_MISSING"),
    ("currency", "CURRENCY_UNSUPPORTED"), ("scope", "FEE_SCOPE_EMPTY"),
    ("missing_goods", "GOODS_VALUE_MISSING"), ("missing_shipping", "SHIPPING_UNIT_REQUIRED")])
def test_saved_partial_result_with_missing_required_inputs_remains_processing(change, code):
    context = saved_context()
    if change == "missing_fee":
        context["fees"][0]["amount_status"] = "MISSING"
    elif change == "fx":
        context["fees"][0]["currency"] = "USD"
        context["version"]["fx_usd_to_rmb"] = 0
    elif change == "currency":
        context["fees"][0]["currency"] = "EUR"
    elif change == "scope":
        context["fees"][0].update(scope_type="ITEMS", scope_value_json='["other"]')
    elif change == "missing_goods":
        context["items"][0]["goods_value"] = None
    else:
        context["items"][0]["quantity"] = None
    save_result(context)
    result = evaluate(context)
    assert result["review_state"] == "processing"
    assert code in codes(result)


def test_new_duplicate_fee_blocks_review_of_previously_saved_result():
    context = saved_context()
    context["fees"].append({**context["fees"][0], "name": "DUPLICATE"})
    result = evaluate(context)
    assert "DUPLICATE_LOGICAL_FEE" in codes(result)
    assert result["review_state"] == "processing"
    assert result["primary_action"] == "supplement_fees"


@pytest.mark.parametrize("change", ["item_missing", "item_duplicate", "item_total", "fee_missing", "allocation_missing", "allocation_wrong", "summary_total", "stored_item_total"])
def test_saved_allocations_must_cover_items_and_reconcile_with_current_inputs(change):
    context = saved_context()
    snapshot = json.loads(context["version"]["summary_snapshot_json"])
    saved = snapshot["comprehensive_cost"]
    if change == "item_missing":
        saved["items"] = []
    elif change == "item_duplicate":
        saved["items"].append(deepcopy(saved["items"][0]))
    elif change == "item_total":
        saved["items"][0]["total_cost_rmb"] = "129.00"
    elif change == "fee_missing":
        saved["included_fees"] = []
    elif change == "allocation_missing":
        saved["included_fees"][0]["allocations"] = {}
    elif change == "allocation_wrong":
        saved["included_fees"][0]["allocations"]["line-1"] = "29.00"
    elif change == "summary_total":
        snapshot["total_cost_rmb"] = "999.00"
    else:
        context["items"][0]["total_cost_rmb"] = "999.00"
    context["version"]["summary_snapshot_json"] = json.dumps(snapshot)
    result = evaluate(context)
    assert "SAVED_RESULT_INVALID" in codes(result)
    assert result["result_is_current"] is False
    assert result["review_state"] == "processing"


def test_confirmed_history_is_separate_even_when_legacy_snapshot_is_not_current():
    context = saved_context()
    context["batch"].update(confirm_status="Confirmed", status="Confirmed")
    context["version"].update(summary_snapshot_json="{}", confirmed_at="2026-09-08 11:00:00")
    result = evaluate(context)
    assert result["review_state"] == "confirmed"
    assert result["result_is_current"] is False
    assert result["primary_action"] == "view"
    assert result["reviewed_at"] == "2026-09-08 11:00:00"
    assert result["reviewed_version"] == "V-1"


@pytest.mark.parametrize("source_status", [
    {"purchase_approval_sync_state": "unreadable"},
    {"invalid_business": True, "invalid_business_scope": "linked_purchase_approval"},
    {"purchase_approval_sync_state": "partial", "linked_purchase_approvals": [
        {"source_instance_id": "bad", "approval_status": "拒绝"}]}])
def test_invalid_purchase_source_is_a_purchase_blocker(source_status):
    context = saved_context()
    context["batch"]["source_status"] = source_status
    context["items"][0]["dingtalk_instance_id"] = "bad"
    save_result(context)
    result = evaluate(context)
    assert "PURCHASE_SOURCE_INVALID" in codes(result)
    assert result["primary_issue"] == "purchase"


def test_valid_evidence_removes_only_evidence_warning():
    context = saved_context()
    context["fees"][0]["amount_status"] = "ESTIMATED"
    context["evidence"] = [{"fee_rule": "F-0", "evidence_role": "freight_invoice", "validation_status": "VALID"}]
    save_result(context)
    assert {row["code"] for row in evaluate(context)["review_warnings"]} == {"ESTIMATED_AMOUNT"}


def test_partially_invalid_approval_requires_purchase_oa_items_to_reference_a_valid_source():
    context = saved_context()
    context["batch"]["source_status"] = {"purchase_approval_sync_state": "partial", "linked_purchase_approvals": [
        {"source_instance_id": "bad", "approval_status": "拒绝"},
        {"source_instance_id": "good", "approval_status": "已完成"}]}
    context["items"][0].update(source_type="PURCHASE_EXPENSE_OA", dingtalk_instance_id="unverified")
    save_result(context)
    assert "PURCHASE_SOURCE_INVALID" in codes(evaluate(context))
    context["items"][0]["dingtalk_instance_id"] = "good"
    save_result(context)
    assert evaluate(context)["review_state"] == "ready"


def test_changed_inputs_report_staleness_without_claiming_saved_result_is_corrupt():
    context = saved_context()
    context["items"][0]["goods_value"] = 110.0
    assert codes(evaluate(context)) == {"RESULT_STALE"}


def test_saved_trial_starts_cost_review_even_after_input_change():
    context = saved_context()
    context["items"][0]["goods_value"] = 110.0

    result = evaluate(context)

    assert result["cost_review_started"] is True
    assert result["review_state"] == "processing"
    assert result["result_is_current"] is False


def test_saved_trial_without_any_purchase_goods_value_is_not_cost_review_eligible():
    context = saved_context()
    context["items"][0]["goods_value"] = None
    save_result(context)

    result = evaluate(context)

    assert result["cost_review_started"] is False
    assert result["cost_review_eligible"] is False
    assert "GOODS_VALUE_MISSING" in codes(result)


def test_saved_trial_with_partial_purchase_value_is_not_cost_review_eligible():
    context = saved_context()
    missing = deepcopy(context["items"][0])
    missing.update(name="I-2", row_no=2, stable_line_key="line-2", material_code="SKU-2", goods_value=None)
    context["items"].append(missing)
    save_result(context)

    result = evaluate(context)

    assert result["cost_review_started"] is False
    assert result["cost_review_eligible"] is False
    assert "GOODS_VALUE_MISSING" in codes(result)


def test_stale_purchase_value_source_uses_the_purchase_data_blocker() -> None:
    context = saved_context()
    item = context["items"][0]
    item["goods_value"] = 0
    item["extra_json"] = json.dumps({
        "shipment_valuation": {
            "amount_rmb": "100",
            "currency": "RMB",
            "quantity": "9",
            "uom": "件",
            "method": "SYSTEM_EXCEL",
            "status": "automatic",
            "error": "",
        }
    })
    save_result(context)

    result = evaluate(context)

    assert "GOODS_VALUE_MISSING" in codes(result)
    assert "SHIPMENT_VALUATION_STALE" not in codes(result)
    assert result["primary_issue"] == "purchase"
    assert result["primary_action"] == "supplement"


def test_filling_current_values_does_not_make_an_old_partial_snapshot_reviewable():
    context = saved_context()
    context["items"][0]["goods_value"] = None
    save_result(context)
    context["items"][0]["goods_value"] = 100

    result = evaluate(context)

    assert result["cost_review_eligible"] is True
    assert result["cost_review_started"] is False
    assert result["review_state"] == "processing"
    assert result["primary_action"] == "recalculate"


def test_explicitly_confirmed_zero_purchase_value_is_cost_review_eligible():
    from overseas_costing.services.shipment_cost_service import build_manual_shipment_valuation

    context = saved_context()
    item = context["items"][0]
    item["goods_value"] = 0
    item["extra_json"] = json.dumps({
        "manual_shipment_valuation": build_manual_shipment_valuation(
            item,
            "0",
            actor="tester@example.com",
            reason="确认免费样品",
        )
    })
    save_result(context)

    result = evaluate(context)

    assert result["cost_review_started"] is True
    assert result["cost_review_eligible"] is True


def test_purchase_evidence_with_spec_model_remains_reviewable_in_formal_inputs():
    from overseas_costing.services.purchase_value_evidence import META_KEY

    context = saved_context()
    item = context["items"][0]
    item.update(
        material_code="FL004195",
        spec_model="PIGMENT-RED",
        quantity=4,
        actual_shipped_qty=4,
        actual_shipped_qty_mode="MANUAL_CONFIRMED",
        purchase_uom="kg",
        shipped_uom="kg",
        goods_value=0,
        extra_json=json.dumps({
            META_KEY: {
                "amount_rmb": "800",
                "quantity": "4",
                "uom": "kg",
                "material_code": "FL004195",
                "spec_model": "PIGMENT-RED",
                "source_refs": [{"document_id": "DOC-1", "row": 1}],
            }
        }),
    )
    save_result(context)

    result = evaluate(context)

    assert result["cost_review_started"] is True
    assert result["cost_review_eligible"] is True
    assert "GOODS_VALUE_MISSING" not in codes(result)


def test_confirmed_sku_component_is_part_of_saved_result_fingerprint():
    context = saved_context()
    tax = next(row for row in context["fees"] if row.get("logical_fee_key") == "import_tax")
    tax.update(amount_status="ACTUAL", amount=100, currency="RMB")
    context["fee_components"] = [{
        "name": "COMP-1",
        "fee_rule": tax["name"],
        "logical_fee_key": "import_tax",
        "stable_line_key": "line-1",
        "amount_rmb": "100",
        "status": "CONFIRMED",
        "is_active": 1,
        "cost_effect": "COST",
    }]
    save_result(context)
    # Bulk review loaders include these grouping columns; the saver does not.
    # They must never become part of the business input fingerprint.
    context["fee_components"][0].update(batch="B-1", version="V-1")

    assert evaluate(context)["result_is_current"] is True
    context["fee_components"][0]["amount_rmb"] = "99"
    result = evaluate(context)
    assert result["result_is_current"] is False
    assert codes(result) == {"RESULT_STALE"}


def test_confirmed_material_packing_groups_use_the_same_projection_as_saved_trial():
    context = saved_context()
    first = context["items"][0]
    first.update(actual_shipped_qty=10, shipped_uom="件", gross_weight_kg=4, volume_m3=1)
    second = {
        **first,
        "name": "I-2",
        "row_no": 2,
        "stable_line_key": "line-2",
        "material_code": "SKU-2",
        "goods_value": 300,
        "quantity": 30,
        "actual_shipped_qty": 30,
        "gross_weight_kg": 6,
        "volume_m3": 3,
    }
    context["items"].append(second)
    group = {
        "group_id": "GROUP-1",
        "member_keys": ["line-1", "line-2"],
        "package_count": "1",
        "net_weight_kg": "18",
        "gross_weight_kg": "20",
        "volume_m3": "8",
        "status": "confirmed",
    }
    context["version"]["extra_json"] = json.dumps({"material_packing_groups": [group]})

    save_result(context)

    current = evaluate(context)
    assert current["result_is_current"] is True
    assert current["review_state"] == "ready"

    changed = json.loads(context["version"]["extra_json"])
    changed["material_packing_groups"][0]["gross_weight_kg"] = "21"
    context["version"]["extra_json"] = json.dumps(changed)
    stale = evaluate(context)
    assert stale["result_is_current"] is False
    assert "RESULT_STALE" in codes(stale)


def test_saved_temporary_ai_basis_remains_verifiable_but_not_formally_reviewable():
    context = saved_context()
    first = context["items"][0]
    first.update(goods_value=100, gross_weight_kg=30, volume_m3=1)
    second = {**first, "name": "I-2", "row_no": 2, "stable_line_key": "line-2",
              "material_code": "SKU-2", "goods_value": 300, "gross_weight_kg": 10, "volume_m3": 3}
    context["items"].append(second)
    fee = context["fees"][0]
    fee.update(allocation_basis="goods_value", basis_field="goods_value", amount_revision="manual:1")
    version = context["version"]
    inputs = [{field: row.get(field) for field in cost_preview_service.COST_INPUT_FIELDS} for row in context["items"]]
    fx = {key: version.get(key) for key in ("fx_usd_to_rmb", "fx_rmb_to_mxn")}
    composed = fee_service.compose_fee_worklist_rows(context["fees"], context["batch"]["transport_mode"])
    trial_review = {
        "run_id": "RUN-TEMP",
        "is_temporary": True,
        "fee_choices": [{
            "suggestion_id": "S-1", "fee_key": fee["logical_fee_key"],
            "basis": "gross_weight", "temporary": True,
        }],
    }
    projected = cost_trial_ai_service.project_fees_for_trial(
        composed,
        {fee["logical_fee_key"]: trial_review["fee_choices"][0]},
        for_save=True,
    )
    saved = cost_preview_service.build_saved_cost_data(inputs, projected, fx, "SEA")
    cost_trial_ai_service.annotate_saved_trial_result(saved, trial_review)
    version["calculated_at"] = "2026-09-08 12:00:00"
    saved["summary_snapshot"]["calculated_at"] = version["calculated_at"]
    version["summary_snapshot_json"] = json.dumps(saved["summary_snapshot"])
    updates = {row["name"]: row for row in saved["item_updates"]}
    for item in context["items"]:
        item.update(updates[item["name"]])
    context["batch"]["estimated_total_cost_rmb"] = saved["summary"]["total_cost_rmb"]

    result = evaluate(context)

    assert result["result_is_current"] is True
    assert "SAVED_RESULT_INVALID" not in codes(result)
    assert "TEMPORARY_ALLOCATION_BASIS" in codes(result)
    assert result["review_state"] == "processing"


def test_saved_formal_ai_review_metadata_does_not_make_result_invalid():
    context = saved_context()
    snapshot = json.loads(context["version"]["summary_snapshot_json"])
    trial_review = {"run_id": "RUN-FORMAL", "is_temporary": False, "fee_choices": []}
    snapshot["ai_cost_trial"] = trial_review
    snapshot["is_temporary"] = False
    snapshot["comprehensive_cost"]["trial_review"] = trial_review
    snapshot["comprehensive_cost"]["summary"]["is_temporary"] = False
    context["version"]["summary_snapshot_json"] = json.dumps(snapshot)

    result = evaluate(context)

    assert result["result_is_current"] is True
    assert "SAVED_RESULT_INVALID" not in codes(result)


@pytest.mark.parametrize("missing_context", ["purchase_quantity", "material_code", "subsidiary"])
def test_complete_saved_allocation_does_not_require_unused_purchase_context(missing_context):
    context = saved_context()
    if missing_context == "purchase_quantity":
        context["items"][0].update(quantity=None, actual_shipped_qty=10,
                                   actual_shipped_qty_mode="EXPLICIT_SOURCE", shipped_uom="箱")
    elif missing_context == "material_code":
        context["items"][0]["material_code"] = None
    else:
        context["batch"]["subsidiary_code"] = None
    save_result(context)
    snapshot = json.loads(context["version"]["summary_snapshot_json"])
    assert snapshot["comprehensive_cost"]["summary"]["is_complete"] is True
    result = evaluate(context)
    assert result["review_state"] == "ready"
    assert result["result_is_current"] is True
    assert result["review_blockers"] == []


@pytest.mark.parametrize("tamper", ["missing_rules", "duplicate_rule", "rule_amount", "allocated_fees_rmb",
    "direct_fees_rmb", "tax_allocated_rmb", "mexico_customs_rmb", "shipping_unit_cost", "purchase_pricing_unit_cost"])
def test_persisted_sku_derived_allocation_and_display_must_match_saved_projection(tamper):
    context = saved_context()
    derived = json.loads(context["items"][0]["derived_json"])
    if tamper == "missing_rules":
        derived["allocated_rules"] = []
    elif tamper == "duplicate_rule":
        derived["allocated_rules"].append(deepcopy(derived["allocated_rules"][0]))
    elif tamper == "rule_amount":
        derived["allocated_rules"][0]["allocated_rmb"] = "999.00"
    elif tamper in {"shipping_unit_cost", "purchase_pricing_unit_cost"}:
        derived[tamper]["amount_rmb"] = "999.000000"
    else:
        derived[tamper] = "999.00"
    context["items"][0]["derived_json"] = json.dumps(derived)
    result = evaluate(context)
    assert "SAVED_RESULT_INVALID" in codes(result)
    assert result["review_state"] == "processing"
    assert result["result_is_current"] is False


@pytest.mark.parametrize("field", ["goods_value_ratio", "weight_ratio", "freight_alloc_rmb", "freight_alloc_mxn",
    "total_logistics_mxn", "alloc_price_mxn", "total_unit_rmb"])
def test_persisted_sku_numeric_cost_outputs_must_match_saved_projection(field):
    context = saved_context()
    context["items"][0][field] = "999.00"
    assert "SAVED_RESULT_INVALID" in codes(evaluate(context))


def test_unrelated_sku_derived_metadata_does_not_invalidate_saved_allocations():
    context = saved_context()
    derived = json.loads(context["items"][0]["derived_json"])
    derived["packing_note"] = "Separate source annotation"
    context["items"][0]["derived_json"] = json.dumps(derived)
    assert evaluate(context)["review_state"] == "ready"


def test_remediation_projection_overrides_action_without_hiding_calculation_readiness():
    from overseas_costing.services.cost_review_service import apply_remediation_projection

    base = {
        "review_state": "ready",
        "issue_codes": [],
        "primary_issue": "ready",
        "primary_action": "review",
    }
    returned = apply_remediation_projection(base, {
        "remediation_state": "returned",
        "round_name": "ROUND-1",
        "round_no": 1,
        "issue_count": 2,
        "unresolved_count": 1,
        "addressed_count": 1,
        "erp_blocked": True,
    })

    assert returned["review_state"] == "ready"
    assert returned["remediation_state"] == "returned"
    assert returned["primary_issue"] == "remediation"
    assert returned["primary_action"] == "review_remediation"
    assert returned["issue_codes"] == ["remediation"]
    assert returned["unresolved_count"] == 1

    resolved = apply_remediation_projection(base, {
        "remediation_state": "resolved",
        "erp_blocked": False,
    })
    assert resolved["primary_action"] == "review"
    assert resolved["primary_issue"] == "ready"


def test_reference_field_change_on_a_confirmed_batch_realigns_the_saved_result(monkeypatch):
    """已确认批次补供应商 / 项目归属之后，批次不能停在「待重新计算」。

    这两项按 ``COST_INPUT_FIELDS`` 契约在指纹里（所以补完指纹就变了），分摊计算却从不读它们；
    已确认版本又不允许重算（``calculate_comprehensive_cost`` 直接 PermissionError）。所以服务端
    写入后要把已存结果对齐回去：``calculate_service._refresh_saved_input_hash_for_reference_change``。

    这里跑的是那份真实现，钉住三件事：对齐后 stale 类 blocker 全部消失；快照里带着的参考字段
    透传副本跟着当前值走（只刷指纹不改副本会换成 SAVED_RESULT_INVALID，一样是「待重新计算」）；
    金额与分摊一个字节都没被动过。
    """

    from types import SimpleNamespace

    from overseas_costing.services import batch_service, calculate_service

    context = saved_context()
    context["batch"].update({"confirm_status": "Confirmed", "status": "Confirmed"})
    context["version"]["status"] = "Confirmed"
    confirmed = evaluate(context)
    assert "RESULT_STALE" not in codes(confirmed)
    assert confirmed["review_state"] == "confirmed"

    # 确认之后采购才补上供应商与项目归属。
    context["items"][0]["supplier"] = "SUP-1"
    context["items"][0]["project_collection"] = "项目一"
    assert "RESULT_STALE" in codes(evaluate(context))

    stored = {}

    def set_value(doctype, name, field, value, **kwargs):
        stored[(doctype, name, field)] = value

    monkeypatch.setattr(calculate_service, "_frappe", SimpleNamespace(
        db=SimpleNamespace(set_value=set_value)))
    monkeypatch.setattr(calculate_service, "_insert_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(batch_service, "_load_erp_push_context", lambda batch_name, version_name: {
        "ok": True,
        "batch_doc_name": context["batch"]["name"],
        "version_name": context["version"]["name"],
        "batch": context["batch"],
        "version": context["version"],
        "items": context["items"],
        "rules": context["fees"],
        "evidence": context["evidence"],
        "fee_components": [],
        "source_context": {},
    })

    original = json.loads(context["version"]["summary_snapshot_json"])
    calculate_service._refresh_saved_input_hash_for_reference_change("B-1", "V-1")

    rewritten = json.loads(stored[("Overseas Cost Version", "V-1", "summary_snapshot_json")])
    context["version"]["summary_snapshot_json"] = json.dumps(rewritten)
    result = evaluate(context)

    assert "RESULT_STALE" not in codes(result)
    assert "SAVED_RESULT_INVALID" not in codes(result)
    assert result["review_state"] == "confirmed"
    assert rewritten["comprehensive_cost"]["items"][0]["project_collection"] == "项目一"
    # 参考字段之外的东西不凭空加进快照（supplier 本来就不在 preview_items 里）。
    assert "supplier" not in rewritten["comprehensive_cost"]["items"][0]
    # 金额、分摊与汇总一个字都没动。
    assert rewritten["comprehensive_cost"]["summary"] == original["comprehensive_cost"]["summary"]
    assert (rewritten["comprehensive_cost"]["items"][0]["total_cost_rmb"]
            == original["comprehensive_cost"]["items"][0]["total_cost_rmb"])
