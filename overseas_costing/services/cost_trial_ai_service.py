"""AI 选择试算口径，服务器保有金额计算权。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from typing import Any

from overseas_costing.services import allocation_service, cost_preview_service, fee_allocation_service

try:
    import frappe
except Exception:  # pragma: no cover - pure tests do not need Frappe
    frappe = None


BASIS_ORDER = ("goods_value", "gross_weight", "volume", "chargeable_weight")
BASIS_LABELS = {
    "goods_value": "货值",
    "gross_weight": "毛重",
    "volume": "体积",
    "chargeable_weight": "计费重",
}
BASIS_FIELDS = {
    "goods_value": "goods_value",
    "gross_weight": "gross_weight_kg",
    "volume": "volume_m3",
    "chargeable_weight": "chargeable_weight_kg",
}
EVIDENCE_PRIORITY_FEES = frozenset({"import_tax", "customs_clearance_fee"})
INPUT_SCHEMA_VERSION = 3
PROMPT_VERSION = "cost-trial-v3"


def cost_trial_ai_enabled() -> bool:
    """Allow an emergency rollback without removing the compatibility calculator."""

    value = os.getenv("OVERSEAS_COST_AI_TRIAL_ENABLED")
    if frappe is not None:
        conf = getattr(frappe, "conf", None)
        try:
            configured = conf.get("overseas_cost_ai_trial_enabled") if conf and hasattr(conf, "get") else None
        except Exception:
            configured = None
        if configured not in (None, ""):
            value = configured
    if value in (None, ""):
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _preview_token_secret() -> bytes:
    """Return a server-only signing key; browsers must not be able to mint previews."""

    value = os.getenv("OVERSEAS_COST_PREVIEW_TOKEN_SECRET")
    if frappe is not None:
        conf = getattr(frappe, "conf", None)
        try:
            value = value or (conf.get("encryption_key") if conf and hasattr(conf, "get") else None)
        except Exception:
            value = value or None
        if not value:
            raise RuntimeError("站点缺少 encryption_key，无法签发试算预览令牌。")
    # Pure unit tests run without a Frappe site. Production always takes the branch above.
    return str(value or "overseas-cost-trial-unit-test-only").encode("utf-8")


def _sign_preview(payload: dict) -> str:
    return hmac.new(_preview_token_secret(), _json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _fee_key(fee: dict) -> str:
    return str(fee.get("logical_fee_key") or fee.get("rule_code") or fee.get("name") or "")


def requires_ai_allocation(fee: dict) -> bool:
    """Return whether a fee needs an AI-selected allocation basis."""

    amount = _decimal(fee.get("amount"))
    return fee_allocation_service.is_counted_fee(fee) and amount is not None and amount != 0


def _item_key(item: dict) -> str:
    return str(item.get("stable_line_key") or item.get("name") or item.get("row_no") or "")


def _basis_value(item: dict, basis: str) -> Decimal | None:
    return fee_allocation_service.basis_decimal(item, basis)


def _basis_profile(fee: dict, items: list[dict], basis: str) -> dict:
    eligible = fee_allocation_service.resolve_eligible_items(fee, items)
    values = [_basis_value(row, basis) for row in eligible]
    missing = [row for row, value in zip(eligible, values) if value is None or value <= 0]
    denominator = sum((value or Decimal("0") for value in values), Decimal("0"))
    return {
        "basis": basis,
        "label": BASIS_LABELS[basis],
        "field": BASIS_FIELDS[basis],
        "available": bool(eligible) and not missing and denominator > 0,
        "missing_count": len(missing),
        "missing_item_keys": [_item_key(row) for row in missing],
        "denominator": format(denominator, "f"),
    }


def _alternative_profile(fee: dict, items: list[dict], fx_context: dict, profile: dict) -> dict:
    result = dict(profile)
    allocation = cost_preview_service.allocate_fee_in_rmb(
        {**fee, "trial_allocation_basis": profile["basis"]}, items, fx_context or {}
    )
    allocations = allocation.get("allocations") or {}
    result["allocation_preview"] = [
        {"item_key": _item_key(item), "amount_rmb": str(allocations.get(_item_key(item)) or "0.00")}
        for item in fee_allocation_service.resolve_eligible_items(fee, items)
    ] if allocation.get("status") == "ALLOCATED" else []
    return result


def _component_rows(fee: dict, components: list[dict]) -> list[dict]:
    key = _fee_key(fee)
    name = str(fee.get("name") or "")
    return [
        dict(row)
        for row in components or []
        if bool(row.get("is_active", 1))
        and str(row.get("status") or "").upper() == "CONFIRMED"
        and str(row.get("cost_effect") or "COST").upper() == "COST"
        and (str(row.get("fee_rule") or "") == name or str(row.get("logical_fee_key") or "") == key)
    ]


def _evidence_profile(fee: dict, components: list[dict], fx_context: dict, items: list[dict]) -> dict:
    if _fee_key(fee) not in EVIDENCE_PRIORITY_FEES:
        return {"locked": False, "rows": [], "amount_rmb": Decimal("0"), "issue": ""}
    rows = _component_rows(fee, components)
    amounts = [_decimal(row.get("amount_rmb")) for row in rows]
    if not rows or any(value is None or value < 0 for value in amounts):
        return {"locked": False, "rows": rows, "amount_rmb": Decimal("0"), "issue": "AMOUNT_INVALID" if rows else ""}
    valid_keys = {_item_key(row) for row in items or []}
    names = {str(row.get("name") or ""): _item_key(row) for row in items or []}
    matched_keys = [
        str(row.get("stable_line_key") or "")
        if str(row.get("stable_line_key") or "") in valid_keys
        else names.get(str(row.get("item") or ""), "")
        for row in rows
    ]
    if any(not key or key not in valid_keys for key in matched_keys):
        return {"locked": False, "rows": rows, "amount_rmb": sum(amounts, Decimal("0")), "issue": "SKU_MATCH_INVALID"}
    converted = cost_preview_service.convert_fee_amount_to_rmb(fee, fx_context or {})
    fee_total = _decimal(converted.get("amount_rmb")) if converted.get("ok") else None
    component_total = sum((value or Decimal("0") for value in amounts), Decimal("0"))
    locked = fee_total is not None and abs(fee_total - component_total) <= Decimal("0.005")
    return {"locked": locked, "rows": rows, "amount_rmb": component_total,
            "issue": "" if locked else "TOTAL_MISMATCH"}


def _suggestion_id(fingerprint: str, fee_key: str) -> str:
    return "trial:" + hashlib.sha256(f"{fingerprint}:{fee_key}".encode("utf-8")).hexdigest()[:24]


def _stable_item_input(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "stable_line_key": item.get("stable_line_key"),
        "row_no": item.get("row_no"),
        "material_code": item.get("material_code"),
        "product_name": item.get("product_name"),
        "category": item.get("category"),
        "transport_mode": item.get("transport_mode"),
        "quantity": item.get("quantity"),
        "goods_value_rmb": _basis_value(item, "goods_value"),
        "gross_weight_kg": item.get("gross_weight_kg"),
        "volume_m3": item.get("volume_m3"),
        "volume_weight_kg": item.get("volume_weight_kg"),
        "chargeable_weight_kg": item.get("chargeable_weight_kg"),
        "project_collection": item.get("project_collection"),
        "supplier": item.get("supplier"),
    }


def _stable_fee_input(fee: dict) -> dict:
    return {
        key: fee.get(key)
        for key in (
            "name", "logical_fee_key", "rule_code", "expense_category", "amount_status",
            "currency", "amount", "scope_type", "scope_value_json", "required_evidence_role",
            "scope_item_keys",
            "included_in_fee_key", "is_enabled", "is_active", "is_final", "source_binding_id",
            "covered_scopes",
        )
    }


def _ai_candidate_fee(fee: dict, available_bases: list[str]) -> dict:
    """Build a prompt-safe fee row without prior decision outputs."""

    return {
        **_stable_fee_input(fee),
        "fee_key": _fee_key(fee),
        "available_bases": list(available_bases),
        "_decision_request": True,
    }


def _stable_component_input(component: dict) -> dict:
    return {
        key: component.get(key)
        for key in (
            "name", "fee_rule", "evidence", "attachment", "item", "stable_line_key",
            "logical_fee_key", "component_type", "tax_code", "hs_code", "currency",
            "original_amount", "amount_rmb", "exchange_rate", "allocation_basis",
            "source_evidence_json", "accounting_role", "cost_effect", "reverses_component",
            "status", "is_active",
        )
    }


def build_input_fingerprint(*, context: dict, items: list[dict], fees: list[dict], fx_context: dict,
                            fee_components: list[dict] | None = None) -> str:
    """Fingerprint decision inputs without calculation outputs or mutable timestamps."""

    payload = {
        "schema": INPUT_SCHEMA_VERSION,
        "context": {
            "batch_name": context.get("batch_name") or context.get("batch") or "",
            "version_name": context.get("version_name") or context.get("version") or "",
            "transport_mode": context.get("transport_mode") or "",
            "project": context.get("project") or context.get("project_collection") or "",
            "supplier": context.get("supplier") or context.get("vendor") or "",
        },
        "items": sorted(
            (_stable_item_input(row) for row in items or []),
            key=lambda row: str(row.get("stable_line_key") or row.get("name") or row.get("row_no") or ""),
        ),
        "fees": sorted(
            (_stable_fee_input(row) for row in fees or []),
            key=lambda row: (str(row.get("logical_fee_key") or row.get("rule_code") or ""), str(row.get("name") or "")),
        ),
        "fx_context": fx_context,
        "fee_components": sorted(
            (_stable_component_input(row) for row in fee_components or []),
            key=lambda row: str(row.get("name") or ""),
        ),
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _previous_choice_map(review: dict | None) -> dict[str, dict]:
    return {
        str(row.get("fee_key") or ""): dict(row)
        for row in (review or {}).get("fee_choices") or []
        if str(row.get("fee_key") or "")
    }


def _legacy_confirmed_basis(fee: dict) -> str:
    basis = str(fee.get("allocation_basis") or fee.get("basis_field") or "")
    if basis not in BASIS_ORDER:
        return ""
    remark = str(fee.get("remark") or "").strip()
    scope_revision = str(fee.get("scope_revision") or "").strip().lower()
    if remark.startswith(("AI 试算确认", "AI试算自动", "试算人工调整")) or scope_revision.startswith("manual:"):
        return basis
    return ""


def _system_fallback_basis(fee: dict, available: list[str]) -> str:
    preferred = str(fee_allocation_service.preferred_allocation_basis(fee) or "")
    return preferred if preferred in available else (available[0] if available else "")


def _decision_plan(inputs: dict, previous_review: dict | None = None, *, force: bool = False) -> dict:
    previous = {} if force else _previous_choice_map(previous_review)
    resolved: dict[str, dict] = {}
    unresolved: list[dict] = []
    blocked: list[str] = []
    for fee in inputs.get("fees") or []:
        if not requires_ai_allocation(fee):
            continue
        key = _fee_key(fee)
        profiles = {basis: _basis_profile(fee, inputs.get("items") or [], basis) for basis in BASIS_ORDER}
        available = [basis for basis in BASIS_ORDER if profiles[basis]["available"]]
        evidence = _evidence_profile(
            fee, inputs.get("fee_components") or [], inputs.get("fx_context") or {}, inputs.get("items") or []
        )
        if evidence.get("locked"):
            resolved[key] = {"basis": available[0] if available else "goods_value", "source": "EVIDENCE"}
            continue
        if evidence.get("issue") in {"SKU_MATCH_INVALID", "AMOUNT_INVALID"} or not available:
            blocked.append(key)
            continue
        previous_basis = str((previous.get(key) or {}).get("basis") or "")
        if not previous_basis and not force:
            previous_basis = _legacy_confirmed_basis(fee)
        if previous_basis in available:
            resolved[key] = {"basis": previous_basis, "source": "REUSED"}
            continue
        unresolved.append(_ai_candidate_fee(fee, available))
    unresolved.sort(key=lambda row: (str(row.get("fee_key") or ""), str(row.get("name") or "")))
    return {"resolved": resolved, "unresolved": unresolved, "blocked": blocked}


def build_cost_trial_review_draft(
    *,
    items: list[dict],
    fees: list[dict],
    fx_context: dict,
    context: dict,
    fee_components: list[dict] | None = None,
    ai_result: dict | None = None,
    decision_overrides: dict[str, dict] | None = None,
) -> dict:
    """Build browser-safe defaults; only complete server-validated bases can be selected."""

    components = list(fee_components or [])
    fingerprint = build_input_fingerprint(
        context=context,
        items=items,
        fees=fees,
        fx_context=fx_context,
        fee_components=components,
    )
    counted = [fee for fee in fees or [] if requires_ai_allocation(fee)]
    ai = ai_result if ai_result is not None else allocation_service.suggest_allocation_rules_with_ai(
        items=items,
        candidate_rules=counted,
        context={
            **context,
            "retrieval_context": [],
            "retrieval_version": "",
        },
    )
    ai_by_key = {_fee_key(row): row for row in (ai.get("rules") or [])}
    overrides = {str(key): dict(value or {}) for key, value in (decision_overrides or {}).items()}
    suggestions = []
    for fee in counted:
        key = _fee_key(fee)
        profiles = {basis: _basis_profile(fee, items, basis) for basis in BASIS_ORDER}
        evidence = _evidence_profile(fee, components, fx_context, items)
        evidence_blocks = evidence.get("issue") in {"SKU_MATCH_INVALID", "AMOUNT_INVALID"}
        available = [
            _alternative_profile(fee, items, fx_context, profiles[basis])
            for basis in BASIS_ORDER
            if profiles[basis]["available"]
        ]
        ai_row = ai_by_key.get(key) or {}
        system_placeholder = bool(ai_row.get("is_system_suggestion")) or ai_row.get("is_ai_suggestion") in {
            0, False, "0", "false", "False",
        }
        ai_recommended = "" if system_placeholder else str(ai_row.get("allocation_basis") or "")
        if ai_recommended not in BASIS_ORDER:
            ai_recommended = ""
        available_bases = [row["basis"] for row in available]
        override = overrides.get(key) or {}
        override_basis = str(override.get("basis") or "")
        if evidence.get("locked"):
            default_basis = available_bases[0] if available_bases else "goods_value"
            decision_source = "EVIDENCE"
        elif override_basis in available_bases:
            default_basis = override_basis
            decision_source = str(override.get("source") or "REUSED")
        elif ai.get("ok") and ai_recommended in available_bases:
            default_basis = ai_recommended
            decision_source = "AI"
        elif available_bases and not evidence_blocks:
            default_basis = _system_fallback_basis(fee, available_bases)
            decision_source = "SYSTEM_FALLBACK"
        else:
            default_basis = ""
            decision_source = ""
        blocked = evidence_blocks or (not evidence.get("locked") and not available_bases)
        effective_basis = default_basis or (ai_recommended if ai_recommended in BASIS_ORDER else "goods_value")
        evidence_summary = [
            {
                "component_id": str(row.get("name") or ""),
                "evidence": str(row.get("evidence") or ""),
                "attachment": str(row.get("attachment") or ""),
                "stable_line_key": str(row.get("stable_line_key") or ""),
                "tax_code": str(row.get("tax_code") or ""),
                "hs_code": str(row.get("hs_code") or ""),
                "amount_rmb": format(_decimal(row.get("amount_rmb")) or Decimal("0"), ".2f"),
                "source_evidence": _json_dict(row.get("source_evidence_json")),
            }
            for row in evidence["rows"]
        ]
        suggestions.append(
            {
                "suggestion_id": _suggestion_id(fingerprint, key),
                "fee_key": key,
                "rule_name": str(fee.get("name") or ""),
                "expense_category": str(fee.get("expense_category") or key),
                "currency": str(fee.get("currency") or "RMB"),
                "amount": str(fee.get("amount") or "0"),
                "scope_type": str(fee.get("scope_type") or "ALL_ITEMS").upper(),
                "scope_item_keys": [_item_key(row) for row in fee_allocation_service.resolve_eligible_items(fee, items)],
                "recommended_basis": effective_basis,
                "recommended_basis_label": BASIS_LABELS[effective_basis],
                "recommended_basis_available": bool(default_basis),
                "ai_recommended_basis": ai_recommended,
                "default_basis": default_basis,
                "decision_source": decision_source,
                "available_bases": available_bases,
                "confidence": str(ai_row.get("ai_confidence") or ai_row.get("confidence") or "0"),
                "reason": str(
                    ai_row.get("remark") or ai_row.get("reason")
                    or ("沿用当前版本上次已确认口径。" if decision_source == "REUSED" else "")
                    or ("AI 未返回可用口径，已采用完整可计算的系统口径。" if decision_source == "SYSTEM_FALLBACK" else "")
                    or ai.get("reason") or ""
                ),
                "basis_profiles": list(profiles.values()),
                "available_alternatives": available,
                "missing_fields": [] if default_basis else [
                    profiles[basis]["field"] for basis in BASIS_ORDER if not profiles[basis]["available"]
                ],
                "requires_temporary_basis": False,
                "requires_user_choice": not evidence["locked"] and not evidence_blocks,
                "blocked": blocked,
                "evidence_locked": evidence["locked"],
                "evidence_component_count": len(evidence["rows"]),
                "evidence_amount_rmb": format(evidence["amount_rmb"], ".2f"),
                "evidence_issue": evidence.get("issue") or "",
                "evidence_summary": evidence_summary,
            }
        )
    decision_summary = {
        "reused": sum(row.get("decision_source") == "REUSED" for row in suggestions),
        "ai": sum(row.get("decision_source") == "AI" for row in suggestions),
        "system_fallback": sum(row.get("decision_source") == "SYSTEM_FALLBACK" for row in suggestions),
        "evidence": sum(row.get("decision_source") == "EVIDENCE" for row in suggestions),
    }
    return {
        "schema_version": 2,
        "input_fingerprint": fingerprint,
        "fx_resolution": dict(context.get("fx_resolution") or fx_context.get("fx_resolution") or {}),
        "model": str(ai.get("model") or ""),
        "ai_status": str(ai.get("action") or ("suggested" if ai.get("ok") else "failed")),
        "ai_ok": bool(ai.get("ok")),
        "ai_warning": "" if ai.get("ok") else str(ai.get("reason") or "AI 未完成建议。"),
        "summary": str(ai.get("summary") or ""),
        "fee_suggestions": suggestions,
        "default_selections": [
            {"suggestion_id": row["suggestion_id"], "basis": row["default_basis"], "reason": ""}
            for row in suggestions
            if row.get("default_basis") and not row.get("evidence_locked") and not row.get("blocked")
        ],
        "decision_summary": decision_summary,
        "ai_invoked": bool(ai.get("_invoked")),
        "trial_context": {
            "transport_mode": str(context.get("transport_mode") or ""),
            "project": str(context.get("project") or context.get("project_collection")
                           or next((row.get("project_collection") for row in items if row.get("project_collection")), "")),
            "supplier": str(context.get("supplier") or context.get("vendor")
                            or next((row.get("supplier") for row in items if row.get("supplier")), "")),
        },
        "retrieval_context": [],
        "retrieval_version": "",
    }


def _selection_map(draft: dict, selections: Any) -> dict[str, dict]:
    if isinstance(selections, str):
        try:
            selections = json.loads(selections or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(_("试算选择格式无效。")) from exc
    if not isinstance(selections, list):
        raise ValueError(_("试算选择格式无效。"))
    proposals = {row["suggestion_id"]: row for row in draft.get("fee_suggestions") or []}
    invalid_evidence = next(
        (row for row in proposals.values() if row.get("evidence_issue") == "SKU_MATCH_INVALID"),
        None,
    )
    if invalid_evidence:
        raise ValueError(_("费用“{0}”的凭证 SKU 匹配无效，请先完成人工匹配。").format(invalid_evidence['expense_category']))
    invalid_amount = next(
        (row for row in proposals.values() if row.get("evidence_issue") == "AMOUNT_INVALID"),
        None,
    )
    if invalid_amount:
        raise ValueError(_("费用“{0}”的凭证金额无效，请先处理凭证。").format(invalid_amount['expense_category']))
    selected: dict[str, dict] = {}

    def select(proposal: dict, raw: dict) -> None:
        basis = str(raw.get("basis") or "")
        available = {value["basis"] for value in proposal.get("available_alternatives") or []}
        if basis not in available:
            raise ValueError(_("分摊口径 {0} 当前不可用。").format(basis or '-'))
        reason = str(raw.get("reason") or "").strip()[:500]
        default_basis = str(proposal.get("default_basis") or proposal.get("recommended_basis") or "")
        modified = basis != default_basis
        selected[proposal["fee_key"]] = {
            "suggestion_id": proposal["suggestion_id"],
            "fee_key": proposal["fee_key"],
            "expense_category": proposal["expense_category"],
            "basis": basis,
            "reason": reason,
            "temporary": False,
            "modified_ai_suggestion": modified,
            "decision_source": "USER_OVERRIDE" if modified else str(proposal.get("decision_source") or "SYSTEM_FALLBACK"),
            "previous_basis": default_basis if modified else "",
            "ai_recommended_basis": proposal.get("ai_recommended_basis") or proposal.get("recommended_basis") or "",
            "ai_confidence": proposal.get("confidence") or "0",
        }

    for raw in selections:
        row = dict(raw or {})
        proposal = proposals.get(str(row.get("suggestion_id") or ""))
        if not proposal:
            raise ValueError(_("试算建议已失效，请重新运行 AI。"))
        select(proposal, row)
    for proposal in proposals.values():
        if proposal.get("evidence_locked"):
            selected[proposal["fee_key"]] = {
                "suggestion_id": proposal["suggestion_id"],
                "fee_key": proposal["fee_key"],
                "expense_category": proposal["expense_category"],
                "basis": next((row["basis"] for row in proposal.get("available_alternatives") or []), "goods_value"),
                "reason": "税费／清关凭证已提供逐 SKU 分项，凭证优先。",
                "temporary": False,
                "evidence_locked": True,
                "decision_source": "EVIDENCE",
                "ai_recommended_basis": proposal.get("ai_recommended_basis") or proposal["recommended_basis"],
                "ai_confidence": proposal.get("confidence") or "0",
            }
        elif proposal.get("requires_user_choice") and proposal["fee_key"] not in selected:
            if proposal.get("blocked") or not proposal.get("default_basis"):
                missing = "、".join(proposal.get("missing_fields") or [])
                raise ValueError(_("费用“{0}”没有完整可用的分摊口径，请补充：{1}。").format(proposal['expense_category'], missing or '分摊数据'))
            select(proposal, {"basis": proposal["default_basis"], "reason": ""})
    return selected


def project_fees_for_trial(fees: list[dict], choices: dict[str, dict], *, for_save: bool = False) -> list[dict]:
    projected = []
    for source in fees or []:
        fee = dict(source or {})
        choice = choices.get(_fee_key(fee))
        if not choice or choice.get("evidence_locked"):
            projected.append(fee)
        elif for_save and not choice.get("temporary"):
            projected.append({**fee, "allocation_basis": choice["basis"], "basis_field": choice["basis"]})
        else:
            projected.append({**fee, "trial_allocation_basis": choice["basis"]})
    return projected


def _choice_audit_remark(choice: dict) -> str:
    basis = str(choice.get("basis") or "")
    source = str(choice.get("decision_source") or "")
    if source == "USER_OVERRIDE":
        previous = str(choice.get("previous_basis") or "-")
        return f"试算人工调整：{previous}→{basis}"
    prefix = {
        "REUSED": "试算沿用上次",
        "AI": "AI试算自动",
        "SYSTEM_FALLBACK": "试算系统兜底",
    }.get(source, "试算确认")
    return f"{prefix}：{basis}"


def annotate_saved_trial_result(result: dict, trial_review: dict) -> dict:
    """Attach the auditable AI decision and preserve temporary-result restrictions."""

    result["trial_review"] = trial_review
    temporary = bool(trial_review.get("is_temporary"))
    result.setdefault("summary", {})["is_temporary"] = temporary
    snapshot = result.setdefault("summary_snapshot", {})
    snapshot["ai_cost_trial"] = trial_review
    snapshot["is_temporary"] = temporary
    comprehensive = snapshot.setdefault("comprehensive_cost", {})
    comprehensive["trial_review"] = trial_review
    if temporary:
        reason = {
            "reason_code": "TEMPORARY_ALLOCATION_BASIS",
            "message": "本次使用暂行分摊口径，补齐资料后需按 AI 建议重新试算。",
        }
        incomplete = result.setdefault("incomplete_reasons", [])
        if not any(row.get("reason_code") == reason["reason_code"] for row in incomplete):
            incomplete.append(reason)
        result["summary"]["is_complete"] = False
        snapshot["formal_confirmation_blocked"] = True
        snapshot["formal_confirmation_block_reason"] = reason["reason_code"]
    return result


def preview_selected_cost_trial(
    *,
    items: list[dict],
    fees: list[dict],
    fx_context: dict,
    fee_components: list[dict],
    draft: dict,
    selections: Any,
) -> dict:
    choices = _selection_map(draft, selections)
    projected_fees = project_fees_for_trial(fees, choices)
    result = cost_preview_service.preview_comprehensive_cost_data(
        items,
        projected_fees,
        fx_context,
        fee_components=fee_components,
    )
    selected_rows = [choices[key] for key in sorted(choices)]
    temporary = any(row.get("temporary") for row in selected_rows)
    if temporary:
        result["incomplete_reasons"].append(
            {
                "reason_code": "TEMPORARY_ALLOCATION_BASIS",
                "message": "本次使用暂行分摊口径，补齐资料后需按 AI 建议重新试算。",
            }
        )
        result["summary"]["is_complete"] = False
    result["trial_review"] = {
        "schema_version": 1,
        "input_fingerprint": draft.get("input_fingerprint") or "",
        "model": draft.get("model") or "",
        "fee_choices": selected_rows,
        "evidence_locks": [
            {
                "suggestion_id": proposal.get("suggestion_id") or "",
                "fee_key": proposal.get("fee_key") or "",
                "amount_rmb": proposal.get("evidence_amount_rmb") or "0.00",
                "component_count": int(proposal.get("evidence_component_count") or 0),
                "components": proposal.get("evidence_summary") or [],
            }
            for proposal in (draft.get("fee_suggestions") or [])
            if proposal.get("evidence_locked")
        ],
        "is_temporary": temporary,
        "retrieval_context": draft.get("retrieval_context") or [],
        "retrieval_version": draft.get("retrieval_version") or "",
    }
    result["preview_token"] = _sign_preview(
        {"fingerprint": draft.get("input_fingerprint"), "choices": selected_rows}
    )
    return result


def _repo(repository: Any | None):
    return repository or FrappeCostTrialAIRepository()


def _default_enqueue(run_id: str) -> None:
    if frappe is None:
        raise RuntimeError("当前未连接 Frappe 长任务队列。")
    frappe.enqueue(
        "overseas_costing.services.cost_trial_ai_service.execute_cost_trial_ai_review",
        queue="long",
        enqueue_after_commit=True,
        job_name=f"cost-trial-ai:{run_id}",
        run_id=run_id,
    )


def _input_fingerprint(inputs: dict) -> str:
    return build_input_fingerprint(
        context=inputs["context"],
        items=inputs["items"],
        fees=inputs["fees"],
        fx_context=inputs["fx_context"],
        fee_components=inputs.get("fee_components") or [],
    )


def _apply_fx_resolution(inputs: dict, resolution: dict | None) -> dict:
    if not isinstance(resolution, dict) or not resolution:
        return inputs
    effective = dict(inputs)
    fx_context = dict(effective.get("fx_context") or {})
    for fieldname in ("fx_usd_to_rmb", "fx_rmb_to_mxn"):
        if resolution.get(fieldname) not in (None, ""):
            fx_context[fieldname] = resolution.get(fieldname)
    fx_context["fx_resolution"] = dict(resolution)
    effective["fx_context"] = fx_context
    context = dict(effective.get("context") or {})
    context["fx_resolution"] = dict(resolution)
    effective["context"] = context
    return effective


def _prepare_fx_resolution(repo: Any, batch_name: str, version_name: str | None) -> dict:
    preparer = getattr(repo, "prepare_fx_resolution", None)
    if not callable(preparer):
        return {}
    prepared = preparer(str(batch_name or ""), str(version_name or ""))
    return dict(prepared or {})


def _run_preview_token(run_id: str, draft: dict, choices: list[dict]) -> str:
    return _sign_preview(
        {
            "run_id": str(run_id or ""),
            "fingerprint": draft.get("input_fingerprint") or "",
            "draft_digest": hashlib.sha256(_json(draft).encode("utf-8")).hexdigest(),
            "choices": choices,
        }
    )


def _load_previous_trial_review(repo: Any, batch: str, version: str) -> dict:
    loader = getattr(repo, "load_previous_trial_review", None)
    if not callable(loader):
        return {}
    loaded = loader(batch, version)
    return dict(loaded) if isinstance(loaded, dict) else {}


def _assert_trial_writable_context(context: dict) -> None:
    version = str(context.get("version") or context.get("version_name") or "")
    current_version = str(context.get("current_version") or "")
    if current_version != version:
        raise ValueError(_("只能试算当前版本，请刷新批次。"))
    if (
        str(context.get("version_status") or "") in {"Confirmed", "Archived"}
        or str(context.get("confirm_status") or "") == "Confirmed"
        or bool(context.get("is_locked"))
    ):
        raise PermissionError(_("已确认或归档版本不能覆盖，请先创建调整版本。"))


def _ready_matches_previous_review(run: dict, previous_review: dict) -> bool:
    previous = _previous_choice_map(previous_review)
    if not previous:
        return True
    draft = _json_dict(run.get("draft_json"))
    defaults = {
        str(row.get("fee_key") or ""): {
            "basis": str(row.get("default_basis") or ""),
            "source": str(row.get("decision_source") or ""),
        }
        for row in draft.get("fee_suggestions") or []
        if str(row.get("fee_key") or "")
    }
    return all(
        defaults.get(key, {}).get("basis") == str(choice.get("basis") or "")
        and defaults.get(key, {}).get("source") in {"REUSED", "EVIDENCE"}
        for key, choice in previous.items()
    )


def _save_run_if_status(repo: Any, run_id: str, expected_status: str, **values) -> bool:
    saver = getattr(repo, "save_run_if_status", None)
    if callable(saver):
        return bool(saver(run_id, expected_status, **values))
    current = repo.get_run(run_id)
    if str(current.get("status") or "") != expected_status:
        return False
    repo.save_run(run_id, **values)
    return True


def start_cost_trial_ai_review(
    batch_name: str,
    version_name: str | None = None,
    *,
    edit_token: str = "",
    expected_modified: str = "",
    force: bool = False,
    reuse_only: bool = False,
    repository: Any | None = None,
    enqueue: Any | None = None,
) -> dict:
    if not cost_trial_ai_enabled():
        raise RuntimeError("AI 成本试算功能开关未启用，请暂用兼容计算接口。")
    if force and reuse_only:
        raise ValueError(_("重新让 AI 判断与仅复用已有口径不能同时执行。"))
    repo = _repo(repository)
    try:
        fx_resolution = _prepare_fx_resolution(repo, str(batch_name), str(version_name or "") or None)
        inputs = repo.load_trial_inputs(
            str(batch_name), str(version_name or "") or None,
            edit_token=edit_token, expected_modified=expected_modified, write=True,
        )
        inputs = _apply_fx_resolution(inputs, fx_resolution)
        _assert_trial_writable_context(inputs["context"])
        from overseas_costing.services.material_input_service import assert_complete_purchase_values
        assert_complete_purchase_values(inputs["items"])
        fingerprint = _input_fingerprint(inputs)
        context = inputs["context"]
        batch = str(context.get("batch_name") or context.get("batch") or batch_name)
        version = str(context.get("version_name") or context.get("version") or version_name or "")
        previous_review = _load_previous_trial_review(repo, batch, version)
        active = None if (force or reuse_only) else repo.find_active(batch, version, fingerprint)
        if active and bool(_json_dict(active.get("draft_json")).get("force_ai")):
            active = None
        if active:
            repo.commit()
            return {
                "ok": True,
                "run_id": str(active.get("name") or ""),
                "status": str(active.get("status") or ""),
                "reused": True,
                "reuse_reason": "RUNNING",
                "progress_revision": int(active.get("progress_revision") or 0),
            }
        reusable = None if force else repo.find_reusable(batch, version, fingerprint)
        if reusable and not _ready_matches_previous_review(reusable, previous_review):
            reusable = None
        if reusable:
            repo.commit()
            return {
                "ok": True,
                "run_id": str(reusable.get("name") or ""),
                "status": str(reusable.get("status") or "READY"),
                "reused": True,
                "reuse_reason": "SAME_INPUT",
                "progress_revision": int(reusable.get("progress_revision") or 0),
            }
        plan = _decision_plan(inputs, previous_review, force=force)
        if reuse_only and plan["unresolved"]:
            for fee in plan["unresolved"]:
                available = list(fee.get("available_bases") or [])
                if available:
                    plan["resolved"][_fee_key(fee)] = {
                        "basis": _system_fallback_basis(fee, available),
                        "source": "SYSTEM_FALLBACK",
                    }
            plan["unresolved"] = []
        if not plan["unresolved"]:
            draft = build_cost_trial_review_draft(
                items=inputs["items"], fees=inputs["fees"], fx_context=inputs["fx_context"],
                context=inputs["context"], fee_components=inputs.get("fee_components") or [],
                ai_result={
                    "ok": True,
                    "action": "not_needed",
                    "model": "",
                    "rules": [],
                    "summary": "已沿用当前版本口径，无需调用 AI。",
                    "_invoked": False,
                },
                decision_overrides=plan["resolved"],
            )
            created = repo.create_run(
                {
                    "batch": batch,
                    "version": version,
                    "operator_name": _session_user(),
                    "status": "READY",
                    "input_fingerprint": fingerprint,
                    "progress_revision": 0,
                    "progress_step": "已有口径已就绪",
                    "progress_percent": 100,
                    "model": "",
                    "prompt_version": PROMPT_VERSION,
                    "draft_json": draft,
                    "error_message": "",
                    "completed_at": _now(),
                }
            )
            repo.commit()
            return {
                "ok": True,
                "run_id": str(created.get("name") or ""),
                "status": "READY",
                "reused": bool(plan["resolved"]),
                "reuse_reason": (
                    "ADJUSTMENT_DEFAULTS" if reuse_only
                    else ("PREVIOUS_CHOICES" if plan["resolved"] else "NO_AI_NEEDED")
                ),
                "progress_revision": int(created.get("progress_revision") or 0),
                "ai_invoked": False,
                "ai_required": False,
            }
        created = repo.create_run(
            {
                "batch": batch,
                "version": version,
                "operator_name": _session_user(),
                "status": "QUEUED",
                "input_fingerprint": fingerprint,
                "progress_revision": 0,
                "progress_step": "等待 AI 分析",
                "progress_percent": 0,
                "model": "",
                "prompt_version": PROMPT_VERSION,
                "draft_json": {"force_ai": bool(force), "fx_resolution": fx_resolution},
                "error_message": "",
            }
        )
        run_id = str(created.get("name") or "")
        (enqueue or _default_enqueue)(run_id)
        repo.commit()
        return {
            "ok": True,
            "run_id": run_id,
            "status": "QUEUED",
            "reused": False,
            "progress_revision": 0,
            "ai_invoked": False,
            "ai_required": True,
            "ai_candidate_count": len(plan["unresolved"]),
        }
    except Exception:
        repo.rollback()
        raise


def execute_cost_trial_ai_review(
    run_id: str,
    *,
    repository: Any | None = None,
    ai_suggester: Any | None = None,
) -> dict:
    repo = _repo(repository)
    run = repo.get_run(str(run_id or ""))
    if str(run.get("status") or "") != "QUEUED":
        return {"ok": True, "run_id": str(run_id), "status": str(run.get("status") or "")}
    try:
        claimed = _save_run_if_status(
            repo,
            str(run_id),
            "QUEUED",
            status="RUNNING",
            progress_step="DeepSeek 正在分析费用口径",
            progress_percent=30,
        )
        if not claimed:
            current = repo.get_run(str(run_id))
            repo.commit()
            return {"ok": True, "run_id": str(run_id), "status": str(current.get("status") or "")}
        # Publish the claim before loading inputs or calling the external model so a
        # second worker cannot enter the same run, and later failures can transition
        # the durable RUNNING state to FAILED.
        repo.commit()
        run = repo.get_run(str(run_id))
        run_meta = _json_dict(run.get("draft_json"))
        inputs = repo.load_trial_inputs(
            str(run.get("batch") or ""), str(run.get("version") or "") or None,
            write=False, trusted=True,
        )
        inputs = _apply_fx_resolution(inputs, run_meta.get("fx_resolution"))
        _assert_trial_writable_context(inputs["context"])
        from overseas_costing.services.material_input_service import assert_complete_purchase_values
        assert_complete_purchase_values(inputs["items"])
        if _input_fingerprint(inputs) != str(run.get("input_fingerprint") or ""):
            stale = _save_run_if_status(
                repo, str(run_id), "RUNNING",
                status="STALE", progress_step="试算输入已变化", progress_percent=100,
            )
            repo.commit()
            if not stale:
                current = repo.get_run(str(run_id))
                return {"ok": True, "run_id": str(run_id), "status": str(current.get("status") or "")}
            return {"ok": False, "run_id": str(run_id), "status": "STALE"}
        previous_review = _load_previous_trial_review(
            repo, str(run.get("batch") or ""), str(run.get("version") or "")
        )
        plan = _decision_plan(inputs, previous_review, force=bool(run_meta.get("force_ai")))
        # Do not keep cost rows locked while waiting for the external model.
        repo.commit()
        candidate_fees = plan["unresolved"]
        suggester = ai_suggester or allocation_service.suggest_allocation_rules_with_ai
        try:
            ai = suggester(
                items=inputs["items"],
                candidate_rules=candidate_fees,
                context={
                    **inputs["context"],
                    "retrieval_context": [],
                    "retrieval_version": "",
                },
            )
        except Exception as exc:
            ai = {
                "ok": False,
                "action": "failed",
                "model": "",
                "rules": [],
                "reason": f"AI 分摊口径调用失败，已采用系统可用口径：{exc}",
            }
        ai = {**dict(ai or {}), "_invoked": bool(candidate_fees)}
        draft = build_cost_trial_review_draft(
            items=inputs["items"], fees=inputs["fees"], fx_context=inputs["fx_context"],
            context=inputs["context"], fee_components=inputs.get("fee_components") or [], ai_result=ai,
            decision_overrides=plan["resolved"],
        )
        refreshed = repo.load_trial_inputs(
            str(run.get("batch") or ""), str(run.get("version") or "") or None,
            write=False, trusted=True,
        )
        refreshed = _apply_fx_resolution(refreshed, run_meta.get("fx_resolution"))
        _assert_trial_writable_context(refreshed["context"])
        if _input_fingerprint(refreshed) != str(run.get("input_fingerprint") or ""):
            stale = _save_run_if_status(
                repo, str(run_id), "RUNNING",
                status="STALE", progress_step="试算输入已变化", progress_percent=100,
            )
            repo.commit()
            if not stale:
                current = repo.get_run(str(run_id))
                return {"ok": True, "run_id": str(run_id), "status": str(current.get("status") or "")}
            return {"ok": False, "run_id": str(run_id), "status": "STALE"}
        saved = _save_run_if_status(
            repo, str(run_id), "RUNNING",
            status="READY", progress_step="试算口径已准备", progress_percent=100,
            model=draft.get("model") or "", draft_json=draft, error_message="", completed_at=_now(),
        )
        if not saved:
            current = repo.get_run(str(run_id))
            repo.commit()
            return {
                "ok": True,
                "run_id": str(run_id),
                "status": str(current.get("status") or ""),
            }
        repo.commit()
        return {
            "ok": True,
            "run_id": str(run_id),
            "status": "READY",
            "ai_invoked": bool(draft.get("ai_invoked")),
            "decision_summary": draft.get("decision_summary") or {},
        }
    except Exception as exc:
        repo.rollback()
        try:
            failed = _save_run_if_status(
                repo, str(run_id), "RUNNING",
                status="FAILED", progress_step="AI 试算失败", progress_percent=100,
                error_message=str(exc)[:2000],
            )
            if failed:
                repo.commit()
            else:
                current = repo.get_run(str(run_id))
                repo.commit()
                return {
                    "ok": True,
                    "run_id": str(run_id),
                    "status": str(current.get("status") or ""),
                }
        except Exception:
            repo.rollback()
        return {"ok": False, "run_id": str(run_id), "status": "FAILED", "message": str(exc)}


def get_cost_trial_ai_review_status(
    batch_name: str,
    run_id: str,
    *,
    after_revision: int | None = None,
    repository: Any | None = None,
) -> dict:
    run = _repo(repository).get_run(str(run_id or ""))
    if str(run.get("batch") or "") != str(batch_name or ""):
        raise ValueError(_("AI 试算任务不属于当前批次。"))
    revision = int(run.get("progress_revision") or 0)
    status = str(run.get("status") or "")
    if after_revision is not None and int(after_revision) == revision and status in {"QUEUED", "RUNNING", "CONFIRMING"}:
        return {"ok": True, "run_id": run_id, "status": status, "progress_revision": revision, "unchanged": True}
    draft = _json_dict(run.get("draft_json"))
    return {
        "ok": True,
        "run_id": str(run.get("name") or run_id),
        "batch_name": str(run.get("batch") or ""),
        "version_name": str(run.get("version") or ""),
        "status": status,
        "progress_revision": revision,
        "progress_step": str(run.get("progress_step") or ""),
        "progress_percent": int(run.get("progress_percent") or 0),
        "model": str(run.get("model") or ""),
        "prompt_version": str(run.get("prompt_version") or ""),
        "error_message": str(run.get("error_message") or ""),
        "draft": draft,
        "ai_invoked": bool(draft.get("ai_invoked")),
        "decision_summary": draft.get("decision_summary") or {},
        "unchanged": False,
    }


def _load_current_run_inputs(repo: Any, batch_name: str, run: dict, *, write: bool = False,
                             edit_token: str = "", expected_modified: str = "") -> dict:
    inputs = repo.load_trial_inputs(
        str(batch_name), str(run.get("version") or "") or None,
        edit_token=edit_token, expected_modified=expected_modified, write=write, trusted=not write,
    )
    inputs = _apply_fx_resolution(inputs, _json_dict(run.get("draft_json")).get("fx_resolution"))
    if _input_fingerprint(inputs) != str(run.get("input_fingerprint") or ""):
        repo.save_run(str(run.get("name") or ""), status="STALE", progress_step="试算输入已变化", progress_percent=100)
        repo.commit()
        raise ValueError(_("试算输入已变化，请重新运行 AI。"))
    return inputs


def preview_cost_trial(
    batch_name: str,
    run_id: str,
    selections: Any,
    *,
    repository: Any | None = None,
) -> dict:
    repo = _repo(repository)
    run = repo.get_run(str(run_id or ""))
    if str(run.get("batch") or "") != str(batch_name or ""):
        raise ValueError(_("AI 试算任务不属于当前批次。"))
    if str(run.get("status") or "") != "READY":
        raise ValueError(_("AI 试算建议尚未就绪。"))
    inputs = _load_current_run_inputs(repo, batch_name, run)
    draft = _json_dict(run.get("draft_json"))
    preview = preview_selected_cost_trial(
        items=inputs["items"], fees=inputs["fees"], fx_context=inputs["fx_context"],
        fee_components=inputs.get("fee_components") or [], draft=draft, selections=selections,
    )
    preview["fx_resolution"] = dict(draft.get("fx_resolution") or {})
    preview["run_id"] = str(run_id)
    preview["preview_token"] = _run_preview_token(
        str(run_id), draft, preview["trial_review"]["fee_choices"],
    )
    return preview


def confirm_cost_trial(
    batch_name: str,
    run_id: str,
    preview_token: str,
    selections: Any,
    *,
    edit_token: str,
    expected_modified: str,
    repository: Any | None = None,
) -> dict:
    repo = _repo(repository)
    run = repo.get_run(str(run_id or ""))
    if str(run.get("batch") or "") != str(batch_name or ""):
        raise ValueError(_("AI 试算任务不属于当前批次。"))
    if str(run.get("status") or "") != "READY":
        raise ValueError(_("AI 试算建议已不可确认。"))
    claimed = _save_run_if_status(
        repo,
        str(run_id),
        "READY",
        status="CONFIRMING",
        progress_step="正在保存试算",
        progress_percent=95,
    )
    if not claimed:
        repo.commit()
        raise ValueError(_("AI 试算建议已不可确认。"))
    try:
        run = repo.get_run(str(run_id))
        inputs = _load_current_run_inputs(
            repo, batch_name, run, write=True,
            edit_token=edit_token, expected_modified=expected_modified,
        )
        _assert_trial_writable_context(inputs["context"])
        from overseas_costing.services.material_input_service import assert_complete_purchase_values
        assert_complete_purchase_values(inputs["items"])
        draft = _json_dict(run.get("draft_json"))
        preview = preview_selected_cost_trial(
            items=inputs["items"], fees=inputs["fees"], fx_context=inputs["fx_context"],
            fee_components=inputs.get("fee_components") or [], draft=draft, selections=selections,
        )
        expected_token = _run_preview_token(
            str(run_id), draft, preview["trial_review"]["fee_choices"],
        )
        if not hmac.compare_digest(expected_token, str(preview_token or "")):
            raise ValueError(_("试算预览已失效，请重新预览。"))
        fx_resolution = dict(draft.get("fx_resolution") or {})
        persister = getattr(repo, "persist_fx_resolution", None)
        if callable(persister) and fx_resolution:
            persisted_fx = persister(inputs, fx_resolution) or {}
            inputs["fx_context"] = {
                **dict(inputs.get("fx_context") or {}),
                **dict(persisted_fx.get("fx_context") or {}),
            }
            fx_resolution = dict(persisted_fx.get("fx_resolution") or fx_resolution)
        preview["fx_resolution"] = fx_resolution
        trial_review = {**preview["trial_review"], "run_id": str(run_id), "confirmed_by": _session_user()}
        saved = repo.save_calculation(inputs, preview, trial_review)
        saved["trial_review"] = trial_review
        confirmed = _save_run_if_status(
            repo,
            str(run_id),
            "CONFIRMING",
            status="CONFIRMED",
            progress_step="试算已保存",
            progress_percent=100,
            selection_json=trial_review,
            error_message="",
            confirmed_at=_now(),
        )
        if not confirmed:
            raise RuntimeError("试算任务状态已变化，未保存本次结果。")
        repo.commit()
        return saved
    except Exception as exc:
        repo.rollback()
        try:
            released = _save_run_if_status(
                repo,
                str(run_id),
                "CONFIRMING",
                status="READY",
                progress_step="试算口径已准备",
                progress_percent=100,
                error_message=str(exc)[:2000],
            )
            if released:
                repo.commit()
            else:
                repo.rollback()
        except Exception:
            repo.rollback()
        raise


def discard_cost_trial_ai_review(
    batch_name: str,
    run_id: str,
    *,
    repository: Any | None = None,
) -> dict:
    repo = _repo(repository)
    for _attempt in range(2):
        run = repo.get_run(str(run_id or ""))
        if str(run.get("batch") or "") != str(batch_name or ""):
            raise ValueError(_("AI 试算任务不属于当前批次。"))
        status = str(run.get("status") or "")
        if status in {"CONFIRMED", "DISCARDED"}:
            return {"ok": True, "run_id": str(run_id), "status": status}
        if status == "CONFIRMING":
            raise ValueError(_("试算结果正在保存，暂时不能放弃。"))
        discarded = _save_run_if_status(
            repo,
            str(run_id),
            status,
            status="DISCARDED",
            progress_step="已放弃试算",
            progress_percent=100,
        )
        if discarded:
            repo.commit()
            return {"ok": True, "run_id": str(run_id), "status": "DISCARDED"}
        repo.commit()
    current = repo.get_run(str(run_id or ""))
    current_status = str(current.get("status") or "")
    if current_status == "CONFIRMING":
        raise ValueError(_("试算结果正在保存，暂时不能放弃。"))
    return {"ok": True, "run_id": str(run_id), "status": current_status}


def _json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _session_user() -> str:
    return str(getattr(getattr(frappe, "session", None), "user", "") or "") if frappe is not None else ""


def _now() -> str:
    if frappe is not None:
        return str(frappe.utils.now())
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class FrappeCostTrialAIRepository:  # pragma: no cover - exercised in Frappe integration
    RUN_DOCTYPE = "Overseas Cost Trial AI Run"
    RUN_FIELDS = [
        "name", "batch", "version", "operator_name", "status", "input_fingerprint",
        "prompt_version", "model", "progress_revision", "progress_step", "progress_percent",
        "draft_json", "selection_json", "error_message", "started_at", "completed_at", "confirmed_at",
    ]

    def __init__(self):
        if frappe is None:
            raise RuntimeError("当前未连接 Frappe 数据库。")

    def prepare_fx_resolution(self, batch_name, version_name):
        return cost_preview_service.FrappeCostRepository().prepare_fx_resolution(batch_name, version_name)

    def persist_fx_resolution(self, inputs, resolution):
        return cost_preview_service.FrappeCostRepository().persist_fx_resolution(inputs["context"], resolution)

    def load_trial_inputs(self, batch_name, version_name, *, edit_token="", expected_modified="",
                          write=False, trusted=False):
        cost_repo = cost_preview_service.FrappeCostRepository()
        context, items, fees, fx_context = cost_repo.lock_and_load(
            batch_name, version_name,
            edit_token=edit_token, expected_modified=expected_modified,
            trusted=bool(trusted or not write),
        )
        decision_rows = frappe.get_all(
            "Overseas Cost Item",
            filters={
                "batch": context["batch"],
                "version": context["version"],
                "is_excluded": 0,
            },
            fields=["name", "category", "transport_mode"],
            limit_page_length=10000,
        )
        decision_by_name = {str(row.get("name") or ""): row for row in decision_rows}
        items = [
            {**row, **decision_by_name.get(str(row.get("name") or ""), {})}
            for row in items
        ]
        public_context = {
            "batch_name": context["batch"],
            "version_name": context["version"],
            "transport_mode": context.get("transport_mode") or "",
            "batch_modified": context.get("batch_modified") or "",
            "version_modified": context.get("version_modified") or "",
            "fx_usd_to_rmb": fx_context.get("fx_usd_to_rmb"),
            "fx_rmb_to_mxn": fx_context.get("fx_rmb_to_mxn"),
        }
        return {
            "context": {**context, **public_context},
            "items": items,
            "fees": fees,
            "fx_context": fx_context,
            "fee_components": cost_repo.load_fee_components(context),
        }

    def load_previous_trial_review(self, batch_name, version_name):
        raw = frappe.db.get_value("Overseas Cost Version", version_name, "summary_snapshot_json")
        snapshot = _json_dict(raw)
        review = snapshot.get("ai_cost_trial")
        if not isinstance(review, dict):
            comprehensive = snapshot.get("comprehensive_cost")
            review = comprehensive.get("trial_review") if isinstance(comprehensive, dict) else {}
        return dict(review) if isinstance(review, dict) else {}

    def find_active(self, batch_name, version_name, fingerprint):
        rows = frappe.get_all(
            self.RUN_DOCTYPE,
            filters={"batch": batch_name, "version": version_name, "input_fingerprint": fingerprint,
                     "status": ["in", ["QUEUED", "RUNNING"]]},
            fields=self.RUN_FIELDS, order_by="creation desc", limit_page_length=1,
        )
        return self._present(rows[0]) if rows else None

    def find_reusable(self, batch_name, version_name, fingerprint):
        rows = frappe.get_all(
            self.RUN_DOCTYPE,
            filters={"batch": batch_name, "version": version_name, "input_fingerprint": fingerprint, "status": "READY"},
            fields=self.RUN_FIELDS, order_by="creation desc", limit_page_length=1,
        )
        return self._present(rows[0]) if rows else None

    def create_run(self, values):
        payload = {key: _json(value) if key.endswith("_json") and not isinstance(value, str) else value
                   for key, value in values.items()}
        payload.setdefault("started_at", _now())
        doc = frappe.get_doc({"doctype": self.RUN_DOCTYPE, **payload})
        doc.insert(ignore_permissions=True)
        return self.get_run(doc.name)

    def get_run(self, run_id):
        row = frappe.db.get_value(self.RUN_DOCTYPE, run_id, self.RUN_FIELDS, as_dict=True)
        if not row:
            raise ValueError(_("未找到 AI 试算任务。"))
        return self._present(row)

    def save_run(self, run_id, **values):
        locked = frappe.db.sql(
            f"SELECT name,progress_revision FROM `tab{self.RUN_DOCTYPE}` WHERE name=%s FOR UPDATE",
            (run_id,), as_dict=True,
        )
        if not locked:
            raise ValueError(_("未找到 AI 试算任务。"))
        payload = {key: _json(value) if key.endswith("_json") and not isinstance(value, str) else value
                   for key, value in values.items()}
        payload["progress_revision"] = int(locked[0].get("progress_revision") or 0) + 1
        frappe.db.set_value(self.RUN_DOCTYPE, run_id, payload, update_modified=True)
        return self.get_run(run_id)

    def save_run_if_status(self, run_id, expected_status, **values):
        locked = frappe.db.sql(
            f"SELECT name,status,progress_revision FROM `tab{self.RUN_DOCTYPE}` WHERE name=%s FOR UPDATE",
            (run_id,), as_dict=True,
        )
        if not locked:
            raise ValueError(_("未找到 AI 试算任务。"))
        if str(locked[0].get("status") or "") != str(expected_status or ""):
            return False
        payload = {
            key: _json(value) if key.endswith("_json") and not isinstance(value, str) else value
            for key, value in values.items()
        }
        payload["progress_revision"] = int(locked[0].get("progress_revision") or 0) + 1
        frappe.db.set_value(self.RUN_DOCTYPE, run_id, payload, update_modified=True)
        return True

    def save_calculation(self, inputs, preview, trial_review):
        from overseas_costing.services.material_input_service import assert_complete_purchase_values
        assert_complete_purchase_values(inputs["items"])
        choices = {row["suggestion_id"]: row for row in trial_review.get("fee_choices") or []}
        draft = _json_dict(self.get_run(trial_review["run_id"]).get("draft_json"))
        proposal_by_id = {row["suggestion_id"]: row for row in draft.get("fee_suggestions") or []}
        fee_choice_by_key = {}
        for suggestion_id, choice in choices.items():
            proposal = proposal_by_id.get(suggestion_id) or {}
            if proposal.get("fee_key"):
                fee_choice_by_key[proposal["fee_key"]] = choice
        projected_fees = project_fees_for_trial(inputs["fees"], fee_choice_by_key, for_save=True)
        result = cost_preview_service.build_saved_cost_data(
            inputs["items"], projected_fees, inputs["fx_context"], inputs["context"]["transport_mode"],
            fee_components=inputs.get("fee_components") or [],
        )
        annotate_saved_trial_result(result, trial_review)
        cost_preview_service.assert_persistable_item_updates(result)
        cost_repo = cost_preview_service.FrappeCostRepository()
        cost_repo.assert_unchanged(inputs["context"])
        modified = cost_repo.save(inputs["context"], result)
        for fee_key, choice in fee_choice_by_key.items():
            if choice.get("temporary") or choice.get("evidence_locked"):
                continue
            fee = next((row for row in inputs["fees"] if _fee_key(row) == fee_key), None)
            if fee and fee.get("name"):
                frappe.db.set_value(
                    "Overseas Cost Allocation Rule", fee["name"],
                    {"allocation_basis": choice["basis"], "remark": _choice_audit_remark(choice)[:500]},
                    update_modified=False,
                )
        result.pop("item_updates", None)
        return {
            **result, "ok": True, "saved": True, "read_only": False,
            "batch_name": inputs["context"]["batch"], "version_name": inputs["context"]["version"],
            "batch_modified": modified, "transport_mode": inputs["context"]["transport_mode"],
            "message": "分摊口径已保存，试算结果已更新。",
        }

    def commit(self):
        frappe.db.commit()

    def rollback(self):
        frappe.db.rollback()

    @staticmethod
    def _present(row):
        result = dict(row or {})
        result["draft_json"] = _json_dict(result.get("draft_json"))
        result["selection_json"] = _json_dict(result.get("selection_json"))
        return result
