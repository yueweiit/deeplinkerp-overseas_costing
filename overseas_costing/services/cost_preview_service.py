"""综合成本统一计算；只读预览和保存试算共用同一分摊结果。"""

from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

try:
    import frappe
except Exception:  # pragma: no cover - pure tests do not require Frappe
    frappe = None

from overseas_costing.services import fee_allocation_service, fee_service, fx_rate_service
from overseas_costing.services.material_input_service import present_material_row
from overseas_costing.services.effective_source_values import source_context_from_items, project_batch_items, batch_source_context
from overseas_costing.services.transport_fee_service import assert_no_duplicate_fees, fee_is_active, mark_duplicate_fees
from overseas_costing.services.logistics_settlement.item_metadata import persist_calculated_item, prune_version_meta
from overseas_costing.services.shipment_cost_service import is_explicit_shipment_zero, object_json
from overseas_costing.services.logistics_settlement.fee_policy import (
    LEGACY_POOL_CURRENCIES, METADATA_FIELDS, is_final, row_scopes, select_fees, validate_final, supplement_legacy_fees,
)


LEGACY_TAX_COMPONENT_FIELDS = (
    "igi_amount",
    "iva_amount",
    "dta",
    "prv_duty",
    "prv_iva",
)
LEGACY_CUSTOMS_SERVICE_FIELDS = (
    "revalidacion",
    "maniobras",
    "muellaje",
    "entrega_mercancia",
    "previo",
    "service_aa",
    "almacenajes",
    "reconocimiento_aduanero",
    "honorarios",
    "complemento_maniobras",
    "desconsolidacion",
    "maniobra_falso",
    "arrastre",
    "patio_regulador",
    "entrega_vacio",
    "limpieza_contenedor",
)

FEE_COMPONENT_INPUT_FIELDS = (
    "name", "fee_rule", "logical_fee_key", "evidence", "attachment", "item",
    "stable_line_key", "component_type", "tax_code", "hs_code", "currency",
    "original_amount", "amount_rmb", "exchange_rate", "allocation_basis",
    "source_evidence_json", "accounting_role", "cost_effect", "reverses_component",
    "status", "is_active",
)

# The fee projection that decides whether a saved snapshot is still current.
#
# This is the single owner of "which fee fields are calculation inputs".  It
# reuses the contract already declared by ``fee_service.COST_INPUT_FIELDS``
# (the field set ``merge_logical_fee`` compares to decide whether a fee's
# *cost* content changed, and therefore which fields may bump
# ``amount_revision``/``scope_revision``), and adds only the fields that the
# allocation itself reads (identity, final-vs-trial split, freight claim
# binding) plus the two revision markers.
#
# Audit labels and history text must stay OUT of this set: ``remark``,
# ``status_change_reason`` and ``status_changed_*`` are display/audit data, not
# valuation inputs.  They are written by flows that do not necessarily bump a
# revision (for example the AI cost-trial save, which rewrites the rule
# ``remark`` right after it stores the snapshot).  Hashing them made the stored
# ``input_hash`` permanently disagree with the hash recomputed from the live
# rule and pinned every such batch on ``RESULT_STALE``.
COST_HASH_FEE_FIELDS = (
    "name",
    *fee_service.COST_INPUT_FIELDS,
    "is_final",
    "source_binding_id",
    "source_snapshot",
    "covered_scopes",
    "amount_revision",
    "scope_revision",
)


def cost_hash_fee_projection(row) -> dict:
    """Return only the calculation inputs of one fee row, in a fixed key order."""

    fee = row if isinstance(row, dict) else {}
    return {field: fee.get(field) for field in COST_HASH_FEE_FIELDS}


# ``spec_model`` was added to the formal query in schema 2 so purchase-value
# evidence can validate row identity.  Keep legacy snapshots compatible: a
# genuine identity mismatch changes the effective valuation and is caught by
# saved-result verification without staling every legacy snapshot merely because
# this previously omitted query field now exists.
COST_HASH_ITEM_SKIP_FIELDS = ("spec_model",)

# Value-level counterpart of ``COST_HASH_ITEM_SKIP_FIELDS``.  The two item loaders
# differ in *how* they hand over the same fact, not only in *which* keys they
# carry: ``extra_json`` reaches the fingerprint as a JSON string from
# ``batch_service._load_erp_push_context`` (the DB column) but as an already
# parsed mapping from ``cost_trial_ai_service.load_trial_inputs``.  The column is
# canonically a string -- ``logistics_settlement.item_metadata.persist_item_meta``
# returns ``json.dumps(...)`` -- so re-serialising dict/list payloads with the same
# sort-keys/compact settings restores the declared representation and stops the
# save path and the read path disagreeing over a mere type difference.
COST_HASH_JSON_ITEM_FIELDS = ("extra_json",)

# The foreign-exchange inputs the calculation actually reads.  ``cost_input_hash``
# hashed the whole ``fx_context`` mapping verbatim, but the two loaders do not
# hand over the same mapping: ``cost_trial_ai_service._apply_fx_resolution``
# stores the entire rate-resolution audit blob under ``fx_resolution`` before the
# AI-trial save hashes it, while the read side rebuilds only the two rate columns
# from the version row.  ``fx_resolution`` is an audit payload -- the allocation
# never reads it (only ``fx_usd_to_rmb``/``fx_rmb_to_mxn`` are consumed, see
# ``preview_comprehensive_cost_data`` and ``fee_policy``) -- so hashing it pinned
# every AI-trial batch on ``RESULT_STALE`` even when both sides agreed on both
# rates.  Projecting to this contract keeps the fingerprint dependent only on the
# declared fx inputs, exactly like the item/fee projections above.
COST_HASH_FX_FIELDS = ("fx_usd_to_rmb", "fx_rmb_to_mxn")


def cost_hash_fx_projection(fx_context) -> dict:
    """Return only the declared calculation inputs of the fx context."""

    context = fx_context if isinstance(fx_context, dict) else {}
    return {field: context.get(field) for field in COST_HASH_FX_FIELDS}


def _canonical_item_value(field, value):
    """Normalise one item value so every loader fingerprints the same bytes."""

    if field not in COST_HASH_JSON_ITEM_FIELDS:
        return value
    if isinstance(value, str):
        # Already persisted form; keep it byte-for-byte so existing snapshots and
        # the read path stay comparable.
        return value
    if value is None:
        return None
    return _json(value)


def cost_hash_item_projection(row) -> dict:
    """Return only the declared calculation inputs of one item row.

    Mirrors :func:`cost_hash_fee_projection`, and exists for the same reason:
    the different call paths into the fingerprint hand in dicts built by
    different loaders.  ``cost_trial_ai_service.load_trial_inputs`` merges the
    decision columns (``category``, ``transport_mode``) into every item, while
    the read side (``batch_service._load_erp_push_context``) does not.  Those
    keys are not calculation inputs and are not in ``COST_INPUT_FIELDS``, but
    the raw dict was hashed verbatim, so the save path and the read path could
    never agree -- the batch stayed pinned on ``RESULT_STALE`` even though both
    sides computed from the same business data.

    Projecting to ``COST_INPUT_FIELDS`` makes the fingerprint depend only on the
    declared item contract, no matter which loader produced the row, and
    :func:`_canonical_item_value` removes the remaining representation differences
    for fields the loaders hand over in different types.
    """

    item = row if isinstance(row, dict) else {}
    return {
        field: _canonical_item_value(field, item.get(field))
        for field in COST_INPUT_FIELDS
        if field not in COST_HASH_ITEM_SKIP_FIELDS
    }


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), ".2f")


def _result_money(value: Decimal, precision: int) -> str:
    if precision == 2:
        return _money(value)
    return format(value.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP), '.6f')


def _unit_money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), ".6f")


def enrich_saved_unit_prices(snapshot: dict, item_rows: list[dict]) -> dict:
    """Project missing unit prices for legacy snapshots without rewriting them."""

    enriched = deepcopy(snapshot or {})
    comprehensive = enriched.get("comprehensive_cost")
    if not isinstance(comprehensive, dict):
        return enriched
    items = comprehensive.get("items")
    if not isinstance(items, list):
        return enriched

    rows_by_name = {
        str(row.get("name") or ""): row
        for row in (item_rows or [])
        if isinstance(row, dict) and row.get("name")
    }
    for item in items:
        if not isinstance(item, dict) or item.get("shipping_unit_price"):
            continue
        row = rows_by_name.get(str(item.get("name") or ""))
        if not row:
            continue
        derived = object_json(row.get("derived_json"))
        quantity = _decimal(derived.get("shipping_quantity"))
        goods_value = _decimal(item.get("goods_value_rmb"))
        valuation = item.get("valuation_source")
        if isinstance(valuation, dict) and (
            valuation.get("error") or str(valuation.get("status") or "").lower() == "missing"
        ):
            continue
        if goods_value == 0 and not is_explicit_shipment_zero(valuation):
            continue
        saved_cost = item.get("shipping_unit_cost")
        derived_cost = derived.get("shipping_unit_cost")
        cost = saved_cost if isinstance(saved_cost, dict) else derived_cost
        uom = str((cost or {}).get("uom") or "").strip() if isinstance(cost, dict) else ""
        if quantity is None or quantity <= 0 or goods_value is None or goods_value < 0 or not uom:
            continue
        item["shipping_unit_price"] = {
            "amount_rmb": _unit_money(goods_value / quantity),
            "uom": uom,
        }
    return enriched


def _item_key(item: dict) -> str:
    return str(item.get("stable_line_key") or item.get("name") or item.get("row_no") or "")


def _fee_key(fee: dict) -> str:
    return str(fee.get("logical_fee_key") or fee.get("rule_code") or fee.get("name") or "")


def convert_fee_amount_to_rmb(fee: dict, fx_context: dict) -> dict:
    final = is_final(fee)
    if final:
        validate_final(fee, fx_context or {})
    amount = _decimal(fee.get("amount"))
    if amount is None or (amount < 0 and not final):
        return {"ok": False, "reason_code": "FEE_AMOUNT_INVALID"}
    currency = str(fee.get("currency") or "RMB").strip().upper()
    if currency not in {"RMB", "CNY", "USD", "MXN"}:
        return {"ok": False, "reason_code": "CURRENCY_UNSUPPORTED", "currency": currency}
    if amount == 0:
        return {"ok": True, "amount_rmb": amount, "rate": None}
    if currency in {"RMB", "CNY"}:
        return {"ok": True, "amount_rmb": amount, "rate": Decimal("1")}
    if currency == "USD":
        rate = _decimal((fx_context or {}).get("fx_usd_to_rmb"))
        if rate is not None and rate > 0:
            converted = amount * rate
            return {"ok": True, "amount_rmb": converted.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP) if final else converted, "rate": rate}
    elif currency == "MXN":
        rmb_to_mxn = _decimal((fx_context or {}).get("fx_rmb_to_mxn"))
        if rmb_to_mxn is not None and rmb_to_mxn > 0:
            converted = amount / rmb_to_mxn
            return {"ok": True, "amount_rmb": converted.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP) if final else converted, "rate": Decimal("1") / rmb_to_mxn}
    return {"ok": False, "reason_code": "FX_RATE_MISSING", "currency": currency}


def _fee_component_rows(fee: dict, fee_components: list[dict]) -> list[dict]:
    rule_name = str(fee.get("name") or "")
    logical_key = _fee_key(fee)
    return [
        row for row in (fee_components or [])
        if bool(row.get("is_active", 1))
        and str(row.get("status") or "").upper() == "CONFIRMED"
        and str(row.get("cost_effect") or "COST").upper() == "COST"
        and (
            str(row.get("fee_rule") or "") == rule_name
            or str(row.get("logical_fee_key") or row.get("fee_logical_key") or "") == logical_key
        )
    ]


def _legacy_fee_component_rows(
    fee: dict, items: list[dict], fx_context: dict
) -> list[dict]:
    logical_key = _fee_key(fee)
    if logical_key not in {"import_tax", "customs_clearance_fee"}:
        return []
    rows = []
    for item in items:
        if logical_key == "import_tax":
            original_amount = _decimal(item.get("import_tax_total"))
            if original_amount is None or original_amount == 0:
                original_amount = sum(
                    (
                        _decimal(item.get(fieldname)) or Decimal("0")
                        for fieldname in LEGACY_TAX_COMPONENT_FIELDS
                    ),
                    Decimal("0"),
                )
        else:
            original_amount = sum(
                (
                    _decimal(item.get(fieldname)) or Decimal("0")
                    for fieldname in LEGACY_CUSTOMS_SERVICE_FIELDS
                ),
                Decimal("0"),
            )
        if not original_amount:
            continue
        converted = convert_fee_amount_to_rmb(
            {"amount": original_amount, "currency": "MXN"}, fx_context
        )
        rows.append(
            {
                "item": item.get("name"),
                "stable_line_key": _item_key(item),
                "currency": "MXN",
                "original_amount": original_amount,
                "amount_rmb": converted.get("amount_rmb") if converted.get("ok") else None,
                "status": "CONFIRMED",
                "is_active": 1,
                "cost_effect": "COST",
            }
        )
    return rows


def _allocation_with_components(
    fee: dict,
    items: list[dict],
    fx_context: dict,
    fee_components: list[dict],
) -> dict:
    if is_final(fee):
        # The immutable final source already chose total OR fee details. Prior
        # evidence splits cannot become a second charge or change its allocation.
        return allocate_fee_in_rmb(fee, items, fx_context)
    from overseas_costing.services.project_freight_service import policy_from_fee
    if policy_from_fee(fee):
        # The confirmed internal project policy governs the entire freight.
        # Invoice SKU splits remain evidence, not a second allocation rule.
        return {**allocate_fee_in_rmb(fee, items, fx_context),
                'component_source':'PROJECT_POLICY_PRIORITY'}
    components = _fee_component_rows(fee, fee_components)
    component_source = "EVIDENCE_SKU_COMPONENT" if components else ""
    if components and any(_decimal(row.get("amount_rmb")) is None for row in components):
        return {
            "status": "BLOCKED",
            "code": "EVIDENCE_COMPONENT_FX_MISSING",
            "allocations": {},
        }
    full = None
    if not components:
        full = allocate_fee_in_rmb(fee, items, fx_context)
        if full.get("status") != "ALLOCATED":
            return full
        components = _legacy_fee_component_rows(fee, items, fx_context)
        component_source = "LEGACY_ITEM_FIELDS" if components else ""
    if not components:
        return full

    item_name_to_key = {str(row.get("name") or ""): _item_key(row) for row in items}
    valid_keys = {_item_key(row) for row in items}
    component_allocations: dict[str, Decimal] = {}
    missing_amount = False
    for row in components:
        amount = _decimal(row.get("amount_rmb"))
        key = str(row.get("stable_line_key") or "")
        if key not in valid_keys:
            key = item_name_to_key.get(str(row.get("item") or ""), "")
        if amount is None:
            missing_amount = True
            continue
        if amount < 0:
            return {
                "status": "BLOCKED",
                "code": "EVIDENCE_COMPONENT_AMOUNT_INVALID",
                "allocations": {},
            }
        if not key or key not in valid_keys:
            return {"status": "BLOCKED", "code": "EVIDENCE_COMPONENT_SKU_INVALID", "allocations": {}}
        component_allocations[key] = component_allocations.get(key, Decimal("0")) + amount
    if missing_amount:
        return {
            "status": "BLOCKED",
            "code": (
                "EVIDENCE_COMPONENT_FX_MISSING"
                if component_source == "EVIDENCE_SKU_COMPONENT"
                else "LEGACY_COMPONENT_FX_MISSING"
            ),
            "allocations": {},
        }

    converted = convert_fee_amount_to_rmb(fee, fx_context)
    if not converted.get("ok"):
        return {
            "status": "BLOCKED",
            "code": converted.get("reason_code") or "FEE_AMOUNT_INVALID",
            "allocations": {},
        }
    fee_total = Decimal(str(converted["amount_rmb"]))
    component_total = sum(component_allocations.values(), Decimal("0"))
    residual = fee_total - component_total
    if residual < Decimal("-0.005"):
        return {"status": "BLOCKED", "code": "EVIDENCE_COMPONENT_TOTAL_EXCEEDS_FEE", "allocations": {}}
    residual = max(residual, Decimal("0"))
    if residual:
        residual_fee = {**fee, "amount": format(residual, "f"), "currency": "RMB"}
        residual_result = allocate_fee_in_rmb(residual_fee, items, {})
        if residual_result.get("status") != "ALLOCATED":
            return residual_result
        residual_allocations = {
            key: Decimal(str(value or "0"))
            for key, value in (residual_result.get("allocations") or {}).items()
        }
    else:
        residual_result = {
            "status": "ALLOCATED",
            "amount": format(fee_total, "f"),
            "allocated_total": _money(fee_total),
            "basis": "evidence_component",
            "preferred_basis": str(fee.get("allocation_basis") or ""),
            "fallback_reason": "",
        }
        residual_allocations = {key: Decimal("0") for key in valid_keys}
    full = full or residual_result
    allocations = {
        key: _money(component_allocations.get(key, Decimal("0")) + residual_allocations.get(key, Decimal("0")))
        for key in valid_keys
    }
    return {
        **full,
        "amount": format(fee_total, "f"),
        "allocated_total": _money(fee_total),
        "allocations": allocations,
        "component_allocations": {key: _money(value) for key, value in component_allocations.items()},
        "residual_amount_rmb": _money(residual),
        "component_count": len(components),
        "component_source": component_source,
        "basis": residual_result.get("basis") or full.get("basis") or "evidence_component",
    }


def preview_comprehensive_cost_data(
    items: list[dict],
    fees: list[dict],
    fx_context: dict,
    *,
    fee_components: list[dict] | None = None,
) -> dict:
    """Calculate a transparent preview from caller-provided snapshots only."""

    source_issues=[]
    try:
        fees = supplement_legacy_fees(items, select_fees(fees, fx_context or {}, source_context=source_context_from_items(items)))
    except ValueError as exc:
        # Read-only previews remain navigable so the user can correct the source.
        # Formal calculation still uses strict select_fees and cannot consume this subtotal.
        fees=[]
        source_issues=[{'reason_code':'FINAL_FEE_REVIEW_REQUIRED','message':str(exc)}]
    precision = 6 if any(is_final(fee) for fee in fees) else 2
    money = lambda value: _result_money(value, precision)
    presented_items = [present_material_row(dict(row or {})) for row in (items or [])]
    item_costs: dict[str, dict] = {}
    incomplete_reasons = list(source_issues)
    if not presented_items:
        incomplete_reasons.append({"reason_code": "MATERIAL_ITEMS_REQUIRED", "message": "当前批次没有物料，请先补充物料数据。"})
    goods_total = Decimal("0")
    for row in presented_items:
        key = _item_key(row)
        goods_value = _decimal(row.get("shipment_value_rmb"))
        explicit_zero = is_explicit_shipment_zero(row.get("shipment_valuation"))
        goods_value_available = goods_value is not None and (goods_value > 0 or explicit_zero)
        if goods_value is None or (goods_value <= 0 and not explicit_zero):
            incomplete_reasons.append(
                {
                    "reason_code": row.get('shipment_valuation', {}).get('error') or "GOODS_VALUE_MISSING",
                    "item_key": key,
                    "field": "goods_value",
                    "message": "本次发货货值缺失或已失效，该行试算不完整。",
                }
            )
            goods_value = Decimal("0")
        goods_total += goods_value
        item_costs[key] = {
            "goods_value_rmb": goods_value,
            "goods_value_available": goods_value_available,
            "direct_fees_rmb": Decimal("0"),
            "allocated_fees_rmb": Decimal("0"),
        }

    direct_total = Decimal("0")
    allocated_total = Decimal("0")
    included_fees = []
    excluded_fees = []
    ignored_fees = []
    estimated_fee_count = 0
    for raw_fee in mark_duplicate_fees(fees):
        fee = dict(raw_fee or {})
        if not fee_is_active(fee):
            continue
        status = fee_allocation_service.amount_status(fee)
        identity = _fee_key(fee)
        common = {
            "rule_name": str(fee.get("name") or ""),
            "fee_key": identity,
            "expense_category": str(fee.get("expense_category") or ""),
            "amount_status": status,
            "currency": str(fee.get("currency") or "RMB").upper(),
            "amount": "" if fee.get("amount") in (None, "") else str(fee.get("amount")),
            **({field: fee.get(field) for field in METADATA_FIELDS} if is_final(fee) else {}),
        }
        if fee.get("duplicate_rule_names"):
            excluded_fees.append({**common, "reason_code": "DUPLICATE_LOGICAL_FEE",
                                  "duplicate_rule_names": fee["duplicate_rule_names"]})
            continue
        if status == "MISSING":
            excluded_fees.append({**common, "reason_code": "AMOUNT_MISSING"})
            continue
        if status in {"NOT_INCURRED", "INCLUDED"}:
            ignored_fees.append({**common, "reason_code": status})
            continue
        if status not in fee_allocation_service.COUNTED_AMOUNT_STATUSES:
            excluded_fees.append({**common, "reason_code": "AMOUNT_STATUS_INVALID"})
            continue

        allocation = _allocation_with_components(
            fee,
            presented_items,
            fx_context or {},
            fee_components or [],
        )
        if allocation.get("status") != "ALLOCATED":
            excluded_fees.append(
                {
                    **common,
                    "reason_code": allocation.get("code") or "ALLOCATION_REQUIRED",
                    "allocation": allocation,
                }
            )
            continue

        amount_rmb = Decimal(allocation.get("allocated_total", allocation["amount"]))
        is_direct = str(fee.get("scope_type") or "ALL_ITEMS").upper() == "DIRECT_ITEM"
        bucket = "direct_fees_rmb" if is_direct else "allocated_fees_rmb"
        for item_key, amount_text in (allocation.get("allocations") or {}).items():
            if item_key in item_costs:
                item_costs[item_key][bucket] += Decimal(str(amount_text or "0"))
        if is_direct:
            direct_total += amount_rmb
        else:
            allocated_total += amount_rmb
        if status == "ESTIMATED":
            estimated_fee_count += 1
            incomplete_reasons.append(
                {
                    "reason_code": "ESTIMATED_AMOUNT",
                    "fee_key": identity,
                    "message": "费用仍为暂估，已计入预览但不是完整成本。",
                }
            )
        included_fees.append(
            {
                **common,
                "amount_rmb": money(amount_rmb),
                "scope_type": str(fee.get("scope_type") or "ALL_ITEMS").upper(),
                "allocation_basis": allocation.get("basis") or "",
                "preferred_basis": allocation.get("preferred_basis") or "",
                "fallback_reason": allocation.get("fallback_reason") or "",
                "allocations": allocation.get("allocations") or {},
                "project_allocations": allocation.get("project_allocations") or {},
                "project_weights_kg": allocation.get("project_weights_kg") or {},
                "component_allocations": allocation.get("component_allocations") or {},
                "component_source": allocation.get("component_source") or "",
                "residual_amount_rmb": allocation.get("residual_amount_rmb", money(amount_rmb)),
            }
        )
        if is_final(fee):
            # Each target pool comes from original currency, before rounding another pool.
            original = Decimal(str(fee['amount']))
            currency = str(fee['currency']).strip().upper()
            mxn_amount = original if currency == 'MXN' else original * Decimal(str(fx_context['fx_rmb_to_mxn']))
            if currency == 'USD':
                mxn_amount *= Decimal(str(fx_context['fx_usd_to_rmb']))
            mxn = fee_allocation_service.allocate_fee(
                {**fee, 'amount': str(mxn_amount), 'currency': 'RMB'}, presented_items)
            included_fees[-1].update(allocations_mxn=mxn['allocations'], amount_mxn=mxn['allocated_total'])

    reason_messages = {
        "DUPLICATE_LOGICAL_FEE": "存在同名费用重复记录，请核对并停用重复记录后重新试算。",
        "EVIDENCE_COMPONENT_FX_MISSING": "凭证 SKU 分项缺少汇率，请补充汇率后重新试算。",
        "LEGACY_COMPONENT_FX_MISSING": "历史 SKU 税费分项缺少汇率，本次试算未计入。",
        "PROJECT_MEMBERSHIP_REQUIRED": "项目归属不完整或与确认规则不一致，请重新分析资料。",
        "PROJECT_WEIGHT_REQUIRED": "项目毛重缺失或总重为零，不能按已确认规则分摊。",
    }
    for row in excluded_fees:
        incomplete_reasons.append(
            {
                "reason_code": row["reason_code"],
                "fee_key": row.get("fee_key") or "",
                "message": reason_messages.get(
                    row["reason_code"], "该费用未计入本次试算。"
                ),
            }
        )

    # Reconcile sub-cent purchase values once so persisted SKU totals conserve
    # the rounded batch amount. Source goods values remain untouched.
    rounded_goods = {key: Decimal(money(costs["goods_value_rmb"])) for key, costs in item_costs.items()}
    remainder = Decimal(money(goods_total)) - sum(rounded_goods.values(), Decimal("0"))
    quantum = Decimal(1).scaleb(-precision)
    direction = quantum if remainder > 0 else -quantum
    ordered_keys = sorted(item_costs, key=lambda key: (
        -(item_costs[key]["goods_value_rmb"] - rounded_goods[key]) if remainder > 0
        else item_costs[key]["goods_value_rmb"] - rounded_goods[key], key))
    for index in range(int(abs(remainder) / quantum)):
        rounded_goods[ordered_keys[index % len(ordered_keys)]] += direction
    preview_items = []
    for row in presented_items:
        key = _item_key(row)
        costs = item_costs[key]
        total = rounded_goods[key] + costs["direct_fees_rmb"] + costs["allocated_fees_rmb"]
        calculation_total = costs["goods_value_rmb"] + costs["direct_fees_rmb"] + costs["allocated_fees_rmb"]
        effective = row.get("effective_shipping") or {}
        shipped_quantity = _decimal(effective.get("quantity"))
        shipped_uom = str(effective.get("uom") or "").strip()
        shipping_unit_price = None
        shipping_unit_cost = None
        if shipped_quantity is not None and shipped_quantity > 0 and shipped_uom:
            if costs["goods_value_available"]:
                shipping_unit_price = {
                    "amount_rmb": _unit_money(costs["goods_value_rmb"] / shipped_quantity),
                    "uom": shipped_uom,
                }
            shipping_unit_cost = {"amount_rmb": _unit_money(calculation_total / shipped_quantity), "uom": shipped_uom}
        else:
            incomplete_reasons.append(
                {
                    "reason_code": "SHIPPING_UNIT_REQUIRED",
                    "item_key": key,
                    "field": "actual_shipped_qty",
                    "message": "发货数量或单位缺失，无法计算单价和综合单价。",
                }
            )

        purchase_quantity = _decimal(row.get("quantity"))
        purchase_uom = str(row.get("purchase_uom") or row.get("unit") or "").strip()
        pricing_uom = str(row.get("unit_price_uom") or "").strip()
        pricing_quantity = purchase_quantity
        if row.get('shipment_valuation', {}).get('method') != 'LEGACY_PURCHASE':
            pricing_quantity = shipped_quantity if pricing_uom == shipped_uom else None
        purchase_pricing_unit_cost = None
        if (
            pricing_quantity is not None
            and pricing_quantity > 0
            and pricing_uom
            and purchase_uom
            and pricing_uom == purchase_uom
        ):
            purchase_pricing_unit_cost = {
                "amount_rmb": _unit_money(calculation_total / pricing_quantity),
                "uom": pricing_uom,
            }
        preview_items.append(
            {
                "name": row.get("name") or "",
                "stable_line_key": key,
                "material_code": row.get("material_code") or "",
                "product_name": row.get("product_name") or "",
                "goods_value_rmb": money(rounded_goods[key]),
                "shipment_value_rmb": money(rounded_goods[key]),
                "valuation_source": row.get('shipment_valuation'),
                "project_collection": row.get('project_collection') or '',
                "gross_weight_kg": row.get('gross_weight_kg'),
                "direct_fees_rmb": money(costs["direct_fees_rmb"]),
                "allocated_fees_rmb": money(costs["allocated_fees_rmb"]),
                "total_cost_rmb": money(total),
                "shipping_unit_price": shipping_unit_price,
                "shipping_unit_cost": shipping_unit_cost,
                "purchase_pricing_unit_cost": purchase_pricing_unit_cost,
                "quantity_source_badge": row.get("quantity_source_badge") or "",
                "shipping_quantity_difference": str(shipped_quantity - purchase_quantity) if shipped_quantity is not None and purchase_quantity is not None and shipped_uom == purchase_uom else None,
            }
        )

    summary = {
        "purchase_goods_value_rmb": money(goods_total),
        "direct_fees_rmb": money(direct_total),
        "allocated_fees_rmb": money(allocated_total),
        "total_cost_rmb": money(goods_total + direct_total + allocated_total),
        "included_fee_count": len(included_fees),
        "excluded_fee_count": len(excluded_fees),
        "is_complete": not incomplete_reasons,
    }
    if estimated_fee_count:
        summary["estimated_fee_count"] = estimated_fee_count
    from overseas_costing.services.shipment_cost_service import project_summaries
    return {
        "ok": True,
        "read_only": True,
        "summary": summary,
        "items": preview_items,
        "project_summary": project_summaries(preview_items, precision=precision),
        "included_fees": included_fees,
        "excluded_fees": excluded_fees,
        "ignored_fees": ignored_fees,
        "incomplete_reasons": incomplete_reasons,
    }


def allocate_fee_in_rmb(fee: dict, items: list[dict], fx_context: dict) -> dict:
    """Share currency validation and allocation between fee status and cost preview."""

    if not fee_allocation_service.is_counted_fee(fee):
        return fee_allocation_service.allocate_fee(fee, items)
    converted = convert_fee_amount_to_rmb(fee, fx_context)
    if not converted["ok"]:
        return {
            "status": "BLOCKED",
            "code": converted["reason_code"],
            "allocations": {},
            "allocated_total": "0.00",
        }
    allocation_fee = {**fee, "amount": format(converted["amount_rmb"], "f"), "currency": "RMB"}
    return {**fee_allocation_service.allocate_fee(allocation_fee, items), "currency": "RMB"}


def preview_comprehensive_cost(batch_name: str, version_name: str | None = None) -> dict:
    """Load snapshots and preview without calling insert, set_value, save or commit."""

    if frappe is None:
        return {"ok": True, "dry_run": True, "read_only": True, "batch_name": batch_name}
    version = version_name or frappe.db.get_value("Overseas Cost Batch", batch_name, "current_version")
    if not version:
        raise ValueError(_("当前批次没有可用成本版本。"))
    if frappe.db.get_value("Overseas Cost Version", version, "batch") != batch_name:
        raise ValueError(_("成本版本不属于当前批次。"))
    transport_mode = frappe.db.get_value("Overseas Cost Batch", batch_name, "transport_mode") or ""
    raw_items = frappe.get_all(
        "Overseas Cost Item",
        filters={"batch": batch_name, "version": version, "is_excluded": 0},
        fields=COST_INPUT_FIELDS,
        order_by="row_no asc, name asc",
        limit_page_length=10000,
    )
    raw_items, source_context = project_batch_items(raw_items,batch_name,version)
    rules = fee_service.compose_fee_worklist_rows(
        fee_service._query_rules(batch_name, version),
        transport_mode, source_context=source_context,
    )
    version_row = frappe.db.get_value(
        "Overseas Cost Version",
        version,
        ["fx_usd_to_rmb", "fx_rmb_to_mxn", "extra_json"],
        as_dict=True,
    ) or {}
    nowdate = getattr(getattr(frappe, "utils", None), "nowdate", None)
    fx_resolution = (
        fx_rate_service.resolve_costing_fx(
            version_fx=version_row,
            calculation_date=nowdate(),
        )
        if callable(nowdate)
        else {}
    )
    effective_fx = {
        **version_row,
        "fx_usd_to_rmb": fx_resolution.get("fx_usd_to_rmb", version_row.get("fx_usd_to_rmb")),
        "fx_rmb_to_mxn": fx_resolution.get("fx_rmb_to_mxn", version_row.get("fx_rmb_to_mxn")),
    }
    from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
    raw_items = project_packing_groups(raw_items, groups_from_version(version_row))['items']
    fee_components = frappe.get_all(
        "Overseas Cost Fee SKU Component",
        filters={"batch": batch_name, "version": version, "status": "CONFIRMED", "is_active": 1},
        fields=[
            "fee_rule", "logical_fee_key", "evidence", "attachment", "item", "stable_line_key",
            "component_type", "tax_code", "hs_code", "currency", "original_amount", "amount_rmb",
            "exchange_rate", "allocation_basis", "source_evidence_json", "accounting_role",
            "cost_effect", "reverses_component", "status", "is_active",
        ],
        limit_page_length=10000,
    )
    result = preview_comprehensive_cost_data(
        raw_items, rules, effective_fx, fee_components=fee_components
    )
    result.update(
        {
            "batch_name": batch_name,
            "version_name": version,
            "transport_mode": transport_mode,
            "fx_context": {
                "fx_usd_to_rmb": effective_fx.get("fx_usd_to_rmb"),
                "fx_rmb_to_mxn": effective_fx.get("fx_rmb_to_mxn"),
            },
            "fx_resolution": fx_resolution,
        }
    )
    return result


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _without_private_trial_fields(value):
    if isinstance(value, dict):
        return {
            key: _without_private_trial_fields(item)
            for key, item in value.items()
            if not str(key).startswith("trial_")
        }
    if isinstance(value, (list, tuple)):
        return [_without_private_trial_fields(item) for item in value]
    return value


def cost_input_hash(items, fees, fx_context, transport_mode, fee_components=None) -> str:
    # Only declared calculation inputs, never audit labels or loader-specific
    # columns: see COST_HASH_ITEM_SKIP_FIELDS, COST_HASH_FEE_FIELDS and
    # COST_HASH_FX_FIELDS.
    hash_items = [cost_hash_item_projection(row) for row in (items or [])]
    hash_fees = [cost_hash_fee_projection(row) for row in (fees or [])]
    components = sorted(
        ({field: (row or {}).get(field) for field in FEE_COMPONENT_INPUT_FIELDS}
         for row in (fee_components or [])),
        key=lambda row: (
            str(row.get("name") or ""),
            str(row.get("fee_rule") or ""),
            str(row.get("logical_fee_key") or ""),
            str(row.get("item") or row.get("stable_line_key") or ""),
            str(row.get("tax_code") or ""),
        ),
    )
    return hashlib.sha256(
        _json(_without_private_trial_fields(
            [hash_items, hash_fees, cost_hash_fx_projection(fx_context), transport_mode, components]
        )).encode()
    ).hexdigest()


def normalize_saved_cost_inputs(items, fees, fx_context, transport_mode):
    """Return the exact material and fee projection used by saved fingerprints."""

    mode = fee_service.resolve_transport_mode(transport_mode)
    normalized_items = [persist_calculated_item(row) for row in items]
    decorated = fee_service._decorate_historical_rules(fees, mode)
    selected = select_fees(
        decorated,
        fx_context,
        source_context=source_context_from_items(normalized_items),
    )
    normalized_fees = supplement_legacy_fees(normalized_items, selected)
    return normalized_items, normalized_fees


def build_saved_cost_data(
    items: list[dict],
    fees: list[dict],
    fx_context: dict,
    transport_mode: str,
    *,
    fee_components: list[dict] | None = None,
) -> dict:
    """Project one preview into stored result fields without mutating source facts."""
    transport_mode = fee_service.resolve_transport_mode(transport_mode)
    original_items, fees = normalize_saved_cost_inputs(
        items,
        fees,
        fx_context,
        transport_mode,
    )
    precision = 6 if any(is_final(fee) for fee in fees) else 2
    money = lambda value: _result_money(value, precision)
    assert_no_duplicate_fees(fees)
    result = preview_comprehensive_cost_data(items, fees, fx_context, fee_components=fee_components or [])
    items = [present_material_row(row) for row in original_items]
    raw_by_key = {_item_key(row): row for row in items}
    fx = _decimal(fx_context.get("fx_rmb_to_mxn"))
    fx = fx if fx is not None and fx > 0 else None
    goods_total = Decimal(result["summary"]["purchase_goods_value_rmb"])
    gross_total = sum((_decimal(row.get("gross_weight_kg")) or Decimal("0") for row in items), Decimal("0"))
    updates = []
    for row in result["items"]:
        key = row["stable_line_key"]
        raw = raw_by_key[key]
        allocations = []
        freight = customs = tax = other = Decimal("0")
        freight_mxn = customs_mxn = extras_mxn = Decimal('0')
        for fee in result["included_fees"]:
            amount = Decimal(fee.get("allocations", {}).get(key, "0"))
            amount_mxn = Decimal(fee['allocations_mxn'].get(key, '0')) if fee.get('allocations_mxn') else (
                Decimal(money(amount * fx)) if fx else Decimal('0'))
            extras_mxn += amount_mxn
            fee_key = fee["fee_key"]
            scopes = row_scopes(fee) if is_final(fee) else set()
            if fee_key == "customs_clearance_fee" or (scopes and 'freight' not in scopes and 'customs' in scopes):
                customs += amount
                customs_mxn += amount_mxn
            elif fee_key == "import_tax" or (scopes and scopes == {'tax'}):
                tax += amount
            elif 'freight' in scopes or fee_key.startswith("international_") or fee_key in {"sea_port_forwarder_surcharge", "air_forwarder_surcharge", "express_surcharge"}:
                freight += amount
                freight_mxn += amount_mxn
            else:
                other += amount
            allocations.append({
                "rule_code": fee_key, "expense_category": fee["expense_category"],
                "amount_rmb": fee["amount_rmb"], "allocated_rmb": money(amount),
                "component_allocated_rmb": fee.get("component_allocations", {}).get(key, "0.00"),
                "component_source": fee.get("component_source") or "",
                "residual_amount_rmb": fee.get("residual_amount_rmb", fee["amount_rmb"]),
                "allocated_mxn": money(amount_mxn) if fx else None,
                "basis": fee["allocation_basis"], "scope_type": fee["scope_type"],
                "fallback_reason": fee["fallback_reason"],
                **({field: fee.get(field) for field in METADATA_FIELDS} if is_final(fee) else {}),
            })
        extras = Decimal(row["direct_fees_rmb"]) + Decimal(row["allocated_fees_rmb"])
        shipping = row.get("shipping_unit_cost")
        effective = present_material_row(raw).get("effective_shipping") or {}
        qty = _decimal(effective.get("quantity"))
        derived = {
            "calculation_schema": 2, "allocated_rules": allocations,
            "fx_rmb_to_mxn": str(fx) if fx else None,
            "fx_usd_to_rmb": fx_context.get("fx_usd_to_rmb"),
            "mexico_customs_rmb": money(customs),
            "mexico_customs_mxn": money(customs_mxn if precision == 6 else customs * fx) if fx else None,
            "allocated_other_rmb": money(tax + other),
            "tax_allocated_rmb": money(tax),
            "direct_fees_rmb": row["direct_fees_rmb"],
            "allocated_fees_rmb": row["allocated_fees_rmb"],
            "shipping_unit_cost": shipping,
            "purchase_pricing_unit_cost": row.get("purchase_pricing_unit_cost"),
            "shipping_quantity": str(qty) if qty else None,
            "purchase_quantity": raw.get("quantity"),
        }
        updates.append({
            "name": raw["name"], "transport_mode": transport_mode,
            "goods_value_ratio": _unit_money(Decimal(row["goods_value_rmb"]) / goods_total * 100) if goods_total else "0",
            "weight_ratio": _unit_money((_decimal(raw.get("gross_weight_kg")) or Decimal("0")) / gross_total * 100) if gross_total else "0",
            "freight_alloc_rmb": money(freight),
            "freight_alloc_mxn": money(freight_mxn if precision == 6 else freight * fx) if fx else None,
            "total_logistics_mxn": money(extras_mxn if precision == 6 else extras * fx) if fx else None,
            "alloc_price_mxn": _unit_money((extras_mxn if precision == 6 else extras * fx) / qty) if fx and qty and qty > 0 else None,
            "total_cost_rmb": row["total_cost_rmb"],
            "total_unit_rmb": shipping["amount_rmb"] if shipping else None,
            "derived_json": _json(derived),
            "extra_json": persist_calculated_item(raw)["extra_json"],
        })
    summary = {
        **result["summary"], "calculation_schema": 2,
        "input_hash": cost_input_hash(original_items, fees, fx_context, transport_mode, fee_components or []),
        "total_goods_value": result["summary"]["purchase_goods_value_rmb"],
        "total_gross_weight_kg": str(gross_total),
        "total_volume_m3": str(sum((_decimal(row.get("volume_m3")) or Decimal("0") for row in items), Decimal("0"))),
        "fee_pool_rmb": money(Decimal(result["summary"]["direct_fees_rmb"]) + Decimal(result["summary"]["allocated_fees_rmb"])),
        "item_count": len(items), "rule_count": len(result["included_fees"]),
        "comprehensive_cost": result,
    }
    return {**result, "item_updates": updates, "summary_snapshot": summary}


def assert_persistable_item_updates(result: dict) -> None:
    """Reject missing derived MXN values before MariaDB can expose a NOT NULL error."""

    for row in result.get("item_updates") or []:
        if row.get("freight_alloc_mxn") is None or row.get("total_logistics_mxn") is None:
            raise ValueError(_("未取得可用的人民币兑比索汇率，本次试算未保存。"))
        if row.get("alloc_price_mxn") is None:
            raise ValueError(_("物料缺少可用数量，无法保存比索分摊单价。"))


class FrappeCostRepository:
    def prepare_fx_resolution(self, batch_name, version_name=None):
        from overseas_costing.services import batch_service

        name = batch_service._resolve_batch_name(batch_name)
        if not name:
            raise ValueError(_("未找到当前批次。"))
        version = version_name or frappe.db.get_value("Overseas Cost Batch", name, "current_version")
        if not version:
            raise ValueError(_("当前批次没有可用成本版本。"))
        version_row = frappe.db.get_value(
            "Overseas Cost Version",
            version,
            ["name", "batch", "status", "fx_usd_to_rmb", "fx_rmb_to_mxn", "extra_json"],
            as_dict=True,
        ) or {}
        if str(version_row.get("batch") or "") != str(name):
            raise ValueError(_("成本版本不属于当前批次。"))
        resolution = fx_rate_service.resolve_costing_fx(
            version_fx=version_row,
            calculation_date=frappe.utils.nowdate(),
        )
        return {**resolution, "batch_name": name, "version_name": version}

    def persist_fx_resolution(self, context, resolution):
        if str(resolution.get("batch_name") or "") != str(context.get("batch") or ""):
            raise ValueError(_("试算汇率所属批次已变化，请重新预览。"))
        if str(resolution.get("version_name") or "") != str(context.get("version") or ""):
            raise ValueError(_("试算汇率所属版本已变化，请重新预览。"))
        session_user = getattr(getattr(frappe, "session", None), "user", None)
        return fx_rate_service.persist_costing_fx_resolution(
            resolution=resolution,
            batch_name=context["batch"],
            version_name=context["version"],
            current_date=frappe.utils.nowdate(),
            repository=fx_rate_service.FrappeCurrencyExchangeRepository(),
            operator=str(session_user or ""),
        )

    def lock_and_load(self, batch_name, version_name, *, edit_token, expected_modified, trusted=False):
        from overseas_costing.services import batch_service, edit_session_service
        name = batch_service._resolve_batch_name(batch_name)
        if not name:
            raise ValueError(_("未找到当前批次。"))
        from overseas_costing.services.logistics_settlement.runtime import calculation_blockers
        blockers = calculation_blockers(name, version_name, for_calculation=True, lock=True)
        if blockers:
            raise ValueError('；'.join(blockers))
        if trusted:
            edit_session_service._lock_row(name)
        else:
            edit_session_service.assert_batch_write(name, edit_token=edit_token, expected_modified=expected_modified)
        batch = frappe.db.get_value("Overseas Cost Batch", name,
            ["name", "current_version", "modified", "status", "confirm_status", "is_locked", "transport_mode", "source_approval_status", "extra_json"], as_dict=True)
        version = version_name or batch.get("current_version")
        frappe.db.sql("SELECT name FROM `tabOverseas Cost Version` WHERE name=%s AND batch=%s FOR UPDATE", (version, name))
        version_row = frappe.db.get_value("Overseas Cost Version", version,
            ["name", "batch", "status", "modified", "fx_usd_to_rmb", "fx_rmb_to_mxn", "extra_json"], as_dict=True) or {}
        if version_row.get("batch") != name:
            raise ValueError(_("成本版本不属于当前批次。"))
        for doctype in ("Overseas Cost Item", "Overseas Cost Allocation Rule"):
            frappe.db.sql(f"SELECT name FROM `tab{doctype}` WHERE batch=%s AND version=%s ORDER BY name FOR UPDATE", (name, version))
        items = frappe.get_all("Overseas Cost Item", filters={"batch": name, "version": version, "is_excluded": 0},
            fields=COST_INPUT_FIELDS, order_by="row_no asc, name asc", limit_page_length=10000)
        invalid = batch_service._build_invalid_business_state(batch, items)
        if invalid.get("invalid"):
            raise ValueError(invalid.get("message") or "当前批次审批已被排除，不能计算。")
        mode = fee_service.resolve_transport_mode(batch.get("transport_mode"))
        if not mode:
            raise ValueError(_("请先确认批次运输方式。"))
        items, source_context = project_batch_items(items,name,version)
        from overseas_costing.services.material_packing_group_service import groups_from_version, project_packing_groups
        items = project_packing_groups(items, groups_from_version(version_row))['items']
        fees = fee_service.compose_fee_worklist_rows(fee_service._query_rules(name, version), mode,source_context=source_context)
        fx = {key: version_row.get(key) for key in ("fx_usd_to_rmb", "fx_rmb_to_mxn")}
        context = {**batch, "transport_mode": mode, "batch": name, "version": version, "batch_modified": str(batch["modified"]),
                   "version_modified": str(version_row["modified"]), "version_status": version_row["status"]}
        return context, items, fees, fx

    def load_fee_components(self, context):
        return frappe.get_all(
            "Overseas Cost Fee SKU Component",
            filters={
                "batch": context["batch"],
                "version": context["version"],
                "status": "CONFIRMED",
                "is_active": 1,
            },
            fields=[
                "name", "fee_rule", "evidence", "attachment", "item", "stable_line_key",
                "logical_fee_key", "component_type", "tax_code", "hs_code", "currency",
                "original_amount", "amount_rmb", "exchange_rate", "allocation_basis",
                "source_evidence_json", "accounting_role", "cost_effect",
                "reverses_component", "status", "is_active",
            ],
            limit_page_length=10000,
        )

    def assert_unchanged(self, context):
        current = frappe.db.get_value("Overseas Cost Batch", context["batch"], ["modified", "current_version"], as_dict=True)
        if str(current["modified"]) != context["batch_modified"] or current["current_version"] != context["version"]:
            raise RuntimeError("试算期间批次数据已变化，请重新试算。")

    def save(self, context, result):
        from overseas_costing.services import calculate_service
        now = frappe.utils.now()
        snapshot = result["summary_snapshot"]
        snapshot["calculated_at"] = now
        for row in result["item_updates"]:
            frappe.db.set_value("Overseas Cost Item", row["name"], {k: v for k, v in row.items() if k != "name"}, update_modified=False)
        frappe.db.set_value("Overseas Cost Version", context["version"], {
            "summary_snapshot_json": _json(snapshot), "rule_snapshot_json": _json(result["included_fees"]), "calculated_at": now,
            "extra_json": _json(prune_version_meta(object_json(
                frappe.db.get_value("Overseas Cost Version", context["version"], "extra_json")))),
        }, update_modified=True)
        frappe.db.set_value("Overseas Cost Batch", context["batch"], {
            "status": "Calculated", "estimated_total_cost_rmb": snapshot["total_cost_rmb"],
            "total_goods_value": snapshot["total_goods_value"], "total_gross_weight_kg": snapshot["total_gross_weight_kg"],
            "item_count": snapshot["item_count"],
        }, update_modified=True)
        calculate_service._insert_audit_log(batch_doc_name=context["batch"], version_name=context["version"],
            action_type="RECALCULATE", action_remark=f"统一试算已保存：RMB {snapshot['total_cost_rmb']}；输入 {snapshot['input_hash']}")
        return str(frappe.db.get_value("Overseas Cost Batch", context["batch"], "modified"))

    def commit(self):
        frappe.db.commit()

    def rollback(self):
        frappe.db.rollback()


COST_INPUT_FIELDS = [
    *LEGACY_POOL_CURRENCIES,
    'extra_json',
    "name", "row_no", "stable_line_key", "material_code", "product_name", "spec_model", "unit", "purchase_uom",
    "unit_price", "purchase_currency", "source_doc_no",
    "unit_price_uom", "quantity", "actual_shipped_qty", "actual_shipped_qty_mode",
    "actual_shipped_qty_source_revision", "shipped_uom", "goods_value", "net_weight_kg",
    "gross_weight_kg", "volume_m3",
    "volume_weight_kg", "chargeable_weight_kg", "project_collection", "supplier", "dingtalk_instance_id", "source_type",
    "igi_amount", "iva_amount", "dta", "prv_duty", "prv_iva", "import_tax_total",
    *LEGACY_CUSTOMS_SERVICE_FIELDS,
]


def calculate_comprehensive_cost(batch_name, version_name=None, *, edit_token=None, expected_modified=None,
                                 repository=None, trusted=False, commit_after_calculate=True):
    """Save a trial atomically; never confirms a version or sends an ERP request."""
    repo = repository or FrappeCostRepository()
    try:
        preparer = getattr(repo, "prepare_fx_resolution", None)
        explicitly_supports_fx = (
            "prepare_fx_resolution" in getattr(type(repo), "__dict__", {})
            or "prepare_fx_resolution" in getattr(repo, "__dict__", {})
        )
        fx_resolution = (
            dict(preparer(batch_name, version_name) or {})
            if callable(preparer) and explicitly_supports_fx
            else {}
        )
        context, items, fees, fx = repo.lock_and_load(batch_name, version_name, edit_token=edit_token,
            expected_modified=expected_modified, trusted=trusted)
        if context.get("current_version") != context["version"]:
            raise ValueError(_("只能试算当前版本，请刷新批次。"))
        if context.get("version_status") in {"Confirmed", "Archived"} or context.get("confirm_status") == "Confirmed" or context.get("is_locked"):
            raise PermissionError(_("已确认或归档版本不能覆盖，请先创建调整版本。"))
        from overseas_costing.services.material_input_service import assert_complete_purchase_values
        assert_complete_purchase_values(items)
        persister = getattr(repo, "persist_fx_resolution", None)
        if fx_resolution and callable(persister):
            persisted_fx = persister(context, fx_resolution) or {}
            fx = {**dict(fx or {}), **dict(persisted_fx.get("fx_context") or {})}
            fx_resolution = dict(persisted_fx.get("fx_resolution") or fx_resolution)
        elif fx_resolution:
            fx = {
                **dict(fx or {}),
                "fx_usd_to_rmb": fx_resolution.get("fx_usd_to_rmb"),
                "fx_rmb_to_mxn": fx_resolution.get("fx_rmb_to_mxn"),
            }
        fee_components = repo.load_fee_components(context) if hasattr(repo, "load_fee_components") else []
        result = build_saved_cost_data(
            items,
            fees,
            fx,
            context["transport_mode"],
            fee_components=fee_components,
        )
        result["fx_resolution"] = fx_resolution
        result["summary_snapshot"]["fx_resolution"] = fx_resolution
        assert_persistable_item_updates(result)
        repo.assert_unchanged(context)
        modified = repo.save(context, result)
        if commit_after_calculate:
            repo.commit()
        result.pop("item_updates", None)
        return {**result, "ok": True, "saved": True, "read_only": False, "batch_name": context["batch"],
                "version_name": context["version"], "batch_modified": modified,
                "transport_mode": context["transport_mode"], "message": "试算完成，已同步总览与 SKU 明细。"}
    except Exception:
        repo.rollback()
        raise
