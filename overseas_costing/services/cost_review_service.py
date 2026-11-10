"""Pure review readiness for trusted current inputs and saved cost snapshots.

Review permits estimates and missing evidence. Final confirmation and ERP keep
their existing, stricter guards. This module never reads or writes a database.
"""

from __future__ import annotations

import json
from decimal import Decimal

from overseas_costing.services import cost_preview_service, fee_service, fee_status_service
from overseas_costing.services.material_input_service import present_material_row, purchase_value_coverage
from overseas_costing.services.shipment_cost_service import is_explicit_shipment_zero
from overseas_costing.services.transport_fee_service import fee_is_active


ISSUE_ORDER = ("purchase", "logistics", "calculation", "erp_failed")
PURCHASE_CODES = {"MATERIAL_ITEMS_REQUIRED", "GOODS_VALUE_MISSING", "PURCHASE_SOURCE_INVALID"}
SAVED_ITEM_OUTPUT_FIELDS = (
    "goods_value_ratio", "weight_ratio", "freight_alloc_rmb", "freight_alloc_mxn",
    "total_logistics_mxn", "alloc_price_mxn", "total_cost_rmb", "total_unit_rmb", "derived_json",
)
ALLOCATION_CODES = {"SHIPPING_UNIT_REQUIRED", "ALLOCATION_BASIS_INCOMPLETE", "ALLOCATION_DENOMINATOR_ZERO",
                    "FEE_SCOPE_EMPTY", "FEE_SCOPE_INVALID", "DIRECT_ITEM_SCOPE_INVALID",
                    "STABLE_ITEM_KEY_REQUIRED", "STABLE_ITEM_KEY_DUPLICATED"}
FEE_CODES = {"AMOUNT_MISSING", "AMOUNT_STATUS_INVALID", "FEE_AMOUNT_INVALID", "DUPLICATE_LOGICAL_FEE",
             "FX_RATE_MISSING", "CURRENCY_UNSUPPORTED"}
MESSAGES = {
    "PURCHASE_SOURCE_INVALID": "采购来源已失效或无法读取，请先核对采购资料。",
    "TRANSPORT_MODE_REQUIRED": "请先确认批次运输方式。",
    "RESULT_NOT_SAVED": "尚未保存综合成本试算，请先重新计算。",
    "RESULT_LEGACY": "历史结果缺少可验证的计算快照，请重新计算。",
    "RESULT_STALE": "计算输入或版本已变化，请重新计算。",
    "SAVED_RESULT_INVALID": "保存结果的物料、费用分摊或汇总不一致，请重新计算。",
    "AMOUNT_MISSING": "存在未填金额的费用，请补充费用。",
    "AMOUNT_STATUS_INVALID": "费用金额状态无效，请核对费用。",
    "FEE_AMOUNT_INVALID": "费用金额无效，请核对费用。",
    "DUPLICATE_LOGICAL_FEE": "存在重复费用，请核对并处理重复记录。",
    "FX_RATE_MISSING": "费用换算所需汇率缺失，请补充汇率。",
    "CURRENCY_UNSUPPORTED": "费用币种暂不支持，请核对费用。",
    "ESTIMATED_AMOUNT": "含暂估费用，可先审核，最终确认前仍需补充实际金额。",
    "EVIDENCE_MISSING": "费用凭证缺失或尚未通过校验，可先审核。",
    "TEMPORARY_ALLOCATION_BASIS": "当前结果采用暂行分摊口径，补齐资料后需按 AI 建议重新试算。",
}


def _dict(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _same_amount(left, right) -> bool:
    left_value = cost_preview_service._decimal(left)
    right_value = cost_preview_service._decimal(right)
    return left_value is not None and right_value is not None and left_value == right_value


def _source_invalid(source: dict, items: list[dict]) -> bool:
    from overseas_costing.services.batch_service import is_invalid_approval_status

    if source.get("invalid_business") or str(source.get("purchase_approval_sync_state") or "").lower() in {"invalid", "excluded", "unreadable", "error"}:
        return True
    approvals = [row for row in source.get("linked_purchase_approvals") or [] if isinstance(row, dict)]
    invalid_ids = {str(row.get("source_instance_id") or "") for row in approvals
                   if is_invalid_approval_status(row.get("approval_status"))
                   or is_invalid_approval_status(row.get("message"))}
    invalid_ids.discard("")
    valid_ids = {str(row.get("source_instance_id") or "") for row in approvals
                 if row.get("approval_status") and not is_invalid_approval_status(row.get("approval_status"))
                 and not is_invalid_approval_status(row.get("message"))}
    valid_ids.discard("")
    for row in items:
        instance_id = str(row.get("dingtalk_instance_id") or "")
        if instance_id in invalid_ids:
            return True
        if (source.get("purchase_approval_sync_state") == "partial"
                and str(row.get("source_type") or "").upper() == "PURCHASE_EXPENSE_OA"
                and instance_id not in valid_ids):
            return True
    return False


def _saved_result_has_complete_purchase_values(snapshot: dict) -> bool:
    """A formal review can only start from a complete saved calculation."""

    saved = _dict(snapshot.get("comprehensive_cost"))
    saved_items = saved.get("items")
    if not isinstance(saved_items, list) or not saved_items:
        return False
    for row in saved_items:
        amount = cost_preview_service._decimal(row.get("goods_value_rmb"))
        if amount is None or amount < 0:
            return False
        if amount == 0 and not is_explicit_shipment_zero(row.get("valuation_source")):
            return False
    return True


def _saved_result_matches(snapshot: dict, expected: dict, items: list[dict]) -> bool:
    """Recompute only in memory to verify saved coverage, allocations and totals."""
    saved = _dict(snapshot.get("comprehensive_cost"))
    for field in ("items", "included_fees", "excluded_fees", "ignored_fees", "incomplete_reasons"):
        # These are the canonical structures emitted by the same pure calculator.
        # Comparing every allocation also rejects duplicate and omitted item rows.
        if saved.get(field) != expected.get(field):
            return False
    if saved.get("summary") != expected.get("summary"):
        return False
    for field in ("purchase_goods_value_rmb", "direct_fees_rmb", "allocated_fees_rmb", "total_cost_rmb"):
        if not _same_amount(snapshot.get(field), expected["summary"].get(field)):
            return False
    if snapshot.get("item_count") != len(items):
        return False
    expected_by_name = {row["name"]: row for row in expected.get("item_updates") or []}
    if len(expected_by_name) != len(items):
        return False
    for item in items:
        output = expected_by_name.get(item.get("name")) or {}
        for field in SAVED_ITEM_OUTPUT_FIELDS:
            if field == "derived_json":
                continue
            if output.get(field) is None:
                if item.get(field) is not None:
                    return False
            elif not _same_amount(item.get(field), output.get(field)):
                return False
        derived = _dict(item.get("derived_json"))
        expected_derived = _dict(output.get("derived_json"))
        # Validate the calculator's canonical allocation/display projection while
        # permitting unrelated annotations in the same JSON document.
        if {key: derived.get(key) for key in expected_derived} != expected_derived:
            return False
    return True


def project_review_inputs(*, batch: dict, version: dict, items: list[dict], fees: list[dict],
                          fee_components: list[dict] | None = None,
                          source_context: dict | None = None) -> dict:
    """把「读路径 → 现算指纹」这一段收在一处，返回投影好的输入与 ``current_hash``。

    两个调用方：``evaluate_review_readiness`` 用它判 ``RESULT_STALE``；
    ``calculate_service._refresh_saved_input_hash_for_reference_change`` 用它把供应商 / 项目归属
    这类非金额参考字段的变更对齐回已保存结果快照。**现算指纹只能有一个 owner** ——
    两侧各写一份投影，就会像 ``COST_HASH_FX_FIELDS`` 注释里那次一样在同一个事实上各算各的。
    """

    mode = fee_service.resolve_transport_mode(batch.get("transport_mode"))
    inputs = [{field: row.get(field) for field in cost_preview_service.COST_INPUT_FIELDS} for row in items]
    from overseas_costing.services.effective_source_values import project_source_values
    inputs = [project_source_values(row,source_context if source_context else None) for row in inputs]
    from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
    inputs = project_packing_groups(inputs, groups_from_version(version))["items"]
    inputs.sort(key=lambda row: (cost_preview_service._decimal(row.get("row_no")) or Decimal(0), str(row.get("name") or "")))
    raw_fees = [{field: row.get(field) for field in fee_service._rule_fields()} for row in fees]
    from overseas_costing.services.effective_source_values import source_context_from_items
    composed = fee_service.compose_fee_worklist_rows(raw_fees, mode,source_context=source_context_from_items(inputs))
    canonical_fees = fee_service._decorate_historical_rules(composed, mode)
    fx = {key: version.get(key) for key in ("fx_usd_to_rmb", "fx_rmb_to_mxn")}
    components = list(fee_components or [])
    snapshot = _dict(version.get("summary_snapshot_json"))
    trial_review = _dict(snapshot.get("ai_cost_trial"))
    calculation_fees = composed
    if trial_review.get("is_temporary"):
        from overseas_costing.services.cost_trial_ai_service import project_fees_for_trial
        temporary_choices = {
            str(row.get("fee_key") or ""): row
            for row in (trial_review.get("fee_choices") or [])
            if row.get("fee_key") and row.get("temporary")
        }
        calculation_fees = project_fees_for_trial(composed, temporary_choices, for_save=True)
    saved_items, saved_fees = cost_preview_service.normalize_saved_cost_inputs(
        inputs,
        calculation_fees,
        fx,
        mode,
    )
    return {
        "mode": mode,
        "inputs": inputs,
        "canonical_fees": canonical_fees,
        "fx": fx,
        "components": components,
        "snapshot": snapshot,
        "trial_review": trial_review,
        "calculation_fees": calculation_fees,
        "cost_review_eligible": purchase_value_coverage(inputs)["complete"],
        "current_hash": cost_preview_service.cost_input_hash(saved_items, saved_fees, fx, mode, components),
    }


def evaluate_review_readiness(*, batch: dict, version: dict, items: list[dict], fees: list[dict],
                              evidence: list[dict] | None = None,
                              fee_components: list[dict] | None = None,
                              source_context: dict | None = None) -> dict:
    """Evaluate one authorized batch; fee rows must use the saved query's order.

    Project exactly the fields used by FrappeCostRepository, including nulls.
    Output-only item fields and bulk-query grouping fields must not enter the hash.
    Fee composition and the second historical decoration match build_saved_cost_data.
    """
    blockers: dict[str, dict] = {}
    warnings: dict[str, dict] = {}

    def block(code, message=""):
        issue = "purchase" if code in PURCHASE_CODES else "logistics" if code in ALLOCATION_CODES | FEE_CODES else "calculation"
        blockers.setdefault(code, {"code": code, "message": MESSAGES.get(code) or message or "费用分摊资料不完整，请核对适用物料和分摊依据。", "issue": issue})

    def warn(code):
        warnings.setdefault(code, {"code": code, "message": MESSAGES[code]})

    if source_context and source_context.get('root_kind') == 'expense':
        from overseas_costing.services.effective_source_values import item_source_context
        source_ready = (source_context.get('available') and source_context.get('approved')
                        and not source_context.get('invalid') and bool(items)
                        and all(item_source_context(item).get('fingerprint') == source_context.get('fingerprint')
                                and (source_context.get('separate_adoption') or _dict(item.get('extra_json')).get('settlement_cargo')) for item in items))
        if not source_ready:
            block('SOURCE_ADOPTION_PENDING', '当前采购支出资料尚未有效采用，旧结果仅供追溯。')

    projection = project_review_inputs(
        batch=batch, version=version, items=items, fees=fees,
        fee_components=fee_components, source_context=source_context,
    )
    mode = projection["mode"]
    inputs = projection["inputs"]
    canonical_fees = projection["canonical_fees"]
    fx = projection["fx"]
    components = projection["components"]
    snapshot = projection["snapshot"]
    trial_review = projection["trial_review"]
    calculation_fees = projection["calculation_fees"]
    cost_review_eligible = projection["cost_review_eligible"]
    current_hash = projection["current_hash"]
    cost_review_started = bool(
        snapshot.get("calculation_schema") == 2
        and snapshot.get("input_hash")
        and isinstance(snapshot.get("comprehensive_cost"), dict)
        and _saved_result_has_complete_purchase_values(snapshot)
        and version.get("calculated_at")
        and snapshot.get("calculated_at")
    )
    if any(fee.get("duplicate_rule_names") for fee in canonical_fees):
        # The saver rejects duplicates. The preview retains them as blockers so
        # the read-only workbench can still explain how to repair the batch.
        expected = cost_preview_service.preview_comprehensive_cost_data(
            inputs, canonical_fees, fx, fee_components=components
        )
    else:
        expected = cost_preview_service.build_saved_cost_data(
            inputs, calculation_fees, fx, mode, fee_components=components
        )
        if trial_review:
            from overseas_costing.services.cost_trial_ai_service import annotate_saved_trial_result
            annotate_saved_trial_result(expected, trial_review)

    if not mode:
        block("TRANSPORT_MODE_REQUIRED")
    price_inputs = inputs
    if source_context and source_context.get('root_kind') == 'expense':
        price_inputs = [row for row in inputs if not ((_dict(row.get('extra_json')).get('settlement_cargo') or {}).get('merchandise_price') or {}).get('present')]
    if price_inputs and _source_invalid(_dict(batch.get("source_status")), price_inputs):
        block("PURCHASE_SOURCE_INVALID")
    keys = [present_material_row(row)["stable_line_key"] for row in inputs]
    if len(set(keys)) != len(keys):
        block("STABLE_ITEM_KEY_DUPLICATED")
    for reason in expected["incomplete_reasons"]:
        code = (
            "GOODS_VALUE_MISSING"
            if reason.get("field") == "goods_value"
            else reason["reason_code"]
        )
        if code == "ESTIMATED_AMOUNT":
            warn(code)
        else:
            block(code, reason.get("message"))

    evidence_by_rule: dict[str, list[dict]] = {}
    for row in evidence or []:
        evidence_by_rule.setdefault(str(row.get("fee_rule") or ""), []).append(row)
    for fee in canonical_fees:
        if not fee_is_active(fee):
            continue
        status = fee_status_service.build_fee_status(fee=fee, allocation=None,
            evidence=evidence_by_rule.get(str(fee.get("name") or ""), []))
        if status["amount_state"] == "ESTIMATED":
            warn("ESTIMATED_AMOUNT")
        if status["evidence_state"] in {"MISSING", "INVALID", "PENDING"}:
            warn("EVIDENCE_MISSING")

    result_current = 'SOURCE_ADOPTION_PENDING' not in blockers
    if not snapshot:
        block("RESULT_NOT_SAVED")
        result_current = False
    elif snapshot.get("calculation_schema") != 2 or not snapshot.get("input_hash") or not isinstance(snapshot.get("comprehensive_cost"), dict):
        block("RESULT_LEGACY")
        result_current = False
    elif snapshot.get("input_hash") == current_hash and not _saved_result_matches(snapshot, expected, items):
        block("SAVED_RESULT_INVALID")
        result_current = False
    if (not version.get("name") or version.get("name") != batch.get("current_version")
            or version.get("batch") != batch.get("name")
            or not version.get("calculated_at")
            or str(snapshot.get("calculated_at") or "") != str(version.get("calculated_at") or "")
            or snapshot.get("input_hash") != current_hash
            or str(batch.get("status") or "").lower() in {"draft", "dirty"}):
        block("RESULT_STALE")
        result_current = False

    confirmed = str(batch.get("confirm_status") or "").lower() == "confirmed"
    state = "confirmed" if confirmed else "processing" if blockers else "ready"
    issues = {row["issue"] for row in blockers.values()}
    if "fail" in str(batch.get("writeback_status") or "").lower():
        issues.add("erp_failed")
    primary = next((code for code in ISSUE_ORDER if code in issues), "ready")
    if state == "confirmed":
        action = "view"
    elif state == "ready":
        action = "review"
    elif primary == "purchase":
        action = "supplement"
    elif any(code in FEE_CODES for code in blockers):
        action = "supplement_fees"
    elif any(code in ALLOCATION_CODES for code in blockers):
        action = "supplement_allocation"
    else:
        action = "recalculate"
    shipment_goods_value_rmb = None
    if (
        cost_review_eligible
        and version.get("name")
        and version["name"] == batch.get("current_version")
        and version.get("batch") == batch.get("name")
        and not {"SOURCE_ADOPTION_PENDING", "PURCHASE_SOURCE_INVALID", "STABLE_ITEM_KEY_DUPLICATED"}.intersection(blockers)
    ):
        shipment_goods_value_rmb = expected["summary"]["purchase_goods_value_rmb"]
    return {"review_state": state, "review_blockers": list(blockers.values()),
            "review_warnings": list(warnings.values()), "result_is_current": result_current,
            "cost_review_started": cost_review_started,
            "cost_review_eligible": cost_review_eligible,
            "shipment_goods_value_rmb": shipment_goods_value_rmb,
            "issue_codes": [code for code in ISSUE_ORDER if code in issues], "primary_issue": primary,
            "primary_action": action, "reviewed_at": version.get("reviewed_at") or version.get("confirmed_at") if confirmed else None,
            "reviewed_version": version.get("name") if confirmed else None,
            "reviewed_version_code": version.get("version_code") if confirmed else None}


def apply_remediation_projection(readiness: dict, projection: dict | None) -> dict:
    """Merge review collaboration state without rewriting calculation readiness."""

    result = dict(readiness or {})
    projected = dict(projection or {})
    result.update(projected)
    state = str(projected.get("remediation_state") or "none").strip().lower()
    if state in {"returned", "resubmitted"}:
        result["issue_codes"] = list(dict.fromkeys(["remediation", *(result.get("issue_codes") or [])]))
        result["primary_issue"] = "remediation"
        result["primary_action"] = "review_remediation"
    return result
