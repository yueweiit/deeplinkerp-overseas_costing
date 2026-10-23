"""逐费用适用范围与稳定尾差分摊。

该模块保持纯计算，不读写 Frappe，便于在试算、ERP 推送和审计复算时
共用同一套金额守恒规则。
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from overseas_costing.services.transport_fee_service import HISTORICAL_SURCHARGES, OTHER_FEE_TYPES, PRIMARY_FREIGHT, is_other_fee_key


COUNTED_AMOUNT_STATUSES = frozenset({"ESTIMATED", "ACTUAL"})
NON_COUNTED_AMOUNT_STATUSES = frozenset({"MISSING", "NOT_INCURRED", "INCLUDED"})
SUPPORTED_SCOPES = frozenset({"ALL_ITEMS", "ITEMS", "DIRECT_ITEM"})
SYSTEM_ALLOCATION_BASES = {
    **{key: basis for key, _label, basis in PRIMARY_FREIGHT.values()},
    **{key: basis for key, _label, basis, _role in HISTORICAL_SURCHARGES},
    "customs_clearance_fee": "goods_value",
    "import_tax": "goods_value",
    "destination_delivery": "gross_weight",
    **{key: "gross_weight" for key, _label in OTHER_FEE_TYPES},
}


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _item_key(item: dict) -> str:
    return str(
        item.get("stable_line_key")
        or item.get("name")
        or item.get("row_no")
        or item.get("idx")
        or ""
    )


def _scope_keys(fee: dict) -> list[str]:
    direct = fee.get("scope_item_keys")
    if isinstance(direct, (list, tuple, set)):
        return [str(value) for value in direct if value not in (None, "")]

    raw = fee.get("scope_value_json")
    if isinstance(raw, list):
        loaded = raw
    elif isinstance(raw, dict):
        loaded = raw.get("item_keys") or raw.get("items") or []
    elif raw:
        try:
            loaded = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            loaded = []
        if isinstance(loaded, dict):
            loaded = loaded.get("item_keys") or loaded.get("items") or []
    else:
        loaded = []
    return [str(value) for value in loaded if value not in (None, "")]


def amount_status(fee: dict) -> str:
    """兼容历史规则：旧数据有金额时仅能按暂估参与，不推断为实际。"""

    explicit = str(fee.get("amount_status") or "").strip().upper()
    if explicit:
        return explicit
    return "ESTIMATED" if _decimal(fee.get("amount")) not in (None, Decimal("0")) else "MISSING"


def is_counted_fee(fee: dict) -> bool:
    return amount_status(fee) in COUNTED_AMOUNT_STATUSES


def resolve_eligible_items(fee: dict, items: list[dict]) -> list[dict]:
    scope_type = str(fee.get("scope_type") or "ALL_ITEMS").upper()
    if scope_type == "ALL_ITEMS":
        return list(items)
    if scope_type not in {"ITEMS", "DIRECT_ITEM"}:
        return []
    keys = set(_scope_keys(fee))
    return [row for row in items if _item_key(row) in keys]


def basis_decimal(item: dict, basis: str) -> Decimal | None:
    if basis == 'goods_value':
        from overseas_costing.services.shipment_cost_service import shipment_value
        return _decimal(shipment_value(item)['amount_rmb'])
    field = {
        "goods_value": "goods_value",
        "gross_weight": "gross_weight_kg",
        "volume": "volume_m3",
        "chargeable_weight": "chargeable_weight_kg",
        "chargeable_weight_kg": "chargeable_weight_kg",
    }.get(basis)
    if not field:
        return None
    return _decimal(item.get(field))


def preferred_allocation_basis(fee: dict) -> str:
    trial_basis = str(fee.get("trial_allocation_basis") or "").strip()
    if trial_basis in {"goods_value", "gross_weight", "volume", "chargeable_weight"}:
        return trial_basis
    from overseas_costing.services.project_freight_service import policy_from_fee
    if policy_from_fee(fee):
        return 'gross_weight'
    key = str(fee.get("logical_fee_key") or fee.get("rule_code") or "")
    saved_basis = str(fee.get("allocation_basis") or fee.get("basis_field") or "")
    manual_revision = any(str(fee.get(field) or "") and not str(fee.get(field)).startswith("oa:")
                          for field in ("amount_revision", "scope_revision"))
    if manual_revision and saved_basis in {"goods_value", "gross_weight", "volume", "chargeable_weight"}:
        return saved_basis
    if is_other_fee_key(key):
        return "gross_weight"
    return SYSTEM_ALLOCATION_BASES.get(key) or str(
        fee.get("allocation_basis") or fee.get("basis_field") or "goods_value"
    )


def select_allocation_basis(fee: dict, eligible: list[dict]) -> dict:
    """Select one complete basis for the entire scope, without mutating saved rules."""

    preferred = preferred_allocation_basis(fee)
    selection = {"preferred_basis": preferred, "basis": preferred, "fallback_reason": ""}
    if str(fee.get("scope_type") or "ALL_ITEMS").upper() == "DIRECT_ITEM":
        return {**selection, "basis": "direct"}
    if _decimal(fee.get("amount")) == 0:
        return {**selection, "basis": "zero_amount"}
    values = [basis_decimal(row, preferred) for row in eligible]
    if values and all(value is not None and value > 0 for value in values):
        return {**selection, "basis": preferred, "fallback_reason": ""}
    code = "ALLOCATION_DENOMINATOR_ZERO" if values and all(value == 0 for value in values) else "ALLOCATION_BASIS_INCOMPLETE"
    return {**selection, "code": code}


def _blocked(code: str, **details) -> dict:
    return {
        "status": "BLOCKED",
        "code": code,
        "allocations": {},
        "allocated_total": "0.00",
        **details,
    }


def _scope_hash(scope_type: str, keys: list[str]) -> str:
    payload = json.dumps(
        {"scope_type": scope_type, "item_keys": sorted(keys)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def allocate_with_stable_remainder(
    amount: Decimal,
    values: list[tuple[str, Decimal]],
    precision: int,
    *, largest_remainder: bool = False,
) -> dict:
    quantum = Decimal(1).scaleb(-precision)
    if largest_remainder:
        target = abs(amount).quantize(quantum, rounding=ROUND_HALF_UP)
        denominator = sum((value for _key, value in values), Decimal('0'))
        exact = {key: target * value / denominator for key, value in values}
        allocated = {key: value.quantize(quantum, rounding=ROUND_DOWN) for key, value in exact.items()}
        order = sorted(exact, key=lambda key: (-(exact[key] - allocated[key]), key))
        for key in order[:int((target - sum(allocated.values(), Decimal('0'))) / quantum)]:
            allocated[key] += quantum
        sign = -1 if amount < 0 else 1
        return {'status': 'ALLOCATED', 'allocations': {key: format(sign * allocated[key], f'.{precision}f') for key in sorted(allocated)},
                'allocated_total': format(sign * target, f'.{precision}f'), 'denominator': format(denominator, 'f')}
    rounded_amount = amount.quantize(quantum)
    denominator = sum((value for _key, value in values), Decimal("0"))
    rows: list[list[object]] = []
    distributed = Decimal("0")
    for key, value in sorted(values, key=lambda pair: pair[0]):
        rounded = (rounded_amount * value / denominator).quantize(quantum, rounding=ROUND_DOWN)
        rows.append([key, rounded])
        distributed += rounded
    remainder_units = int((rounded_amount - distributed) / quantum)
    for index in range(remainder_units):
        rows[index % len(rows)][1] += quantum
    allocations = {key: format(value, f".{precision}f") for key, value in rows}
    return {
        "status": "ALLOCATED",
        "allocations": allocations,
        "allocated_total": format(sum((value for _key, value in rows), Decimal("0")), f".{precision}f"),
        "denominator": format(denominator, "f"),
    }


def allocate_fee(fee: dict, items: list[dict], *, currency_precision: int = 2) -> dict:
    from overseas_costing.services.logistics_settlement.fee_policy import is_final, validate_final
    final = is_final(fee)
    if final:
        validate_final(fee)
        currency_precision = 6
    if fee.get("duplicate_rule_names") or fee.get("conflict_code") == "DUPLICATE_LOGICAL_FEE":
        return _blocked("DUPLICATE_LOGICAL_FEE", duplicate_rule_names=fee.get("duplicate_rule_names") or [])
    status = amount_status(fee)
    if status in NON_COUNTED_AMOUNT_STATUSES:
        return {
            "status": "NOT_COUNTED",
            "code": status,
            "allocations": {},
            "allocated_total": format(Decimal("0"), f".{currency_precision}f"),
        }
    if status not in COUNTED_AMOUNT_STATUSES:
        return _blocked("AMOUNT_STATUS_INVALID")

    amount = _decimal(fee.get("amount"))
    if amount is None or (amount < 0 and not final):
        return _blocked("FEE_AMOUNT_INVALID")

    scope_type = str(fee.get("scope_type") or "ALL_ITEMS").upper()
    if scope_type not in SUPPORTED_SCOPES:
        return _blocked("FEE_SCOPE_INVALID")
    eligible = resolve_eligible_items(fee, items)
    if scope_type == "DIRECT_ITEM" and len(eligible) != 1:
        return _blocked("DIRECT_ITEM_SCOPE_INVALID")
    if not eligible:
        return _blocked("FEE_SCOPE_EMPTY")

    from overseas_costing.services.project_freight_service import policy_from_fee, allocate_projects
    policy = policy_from_fee(fee)
    if policy:
        keys = [_item_key(row) for row in eligible]
        if not all(keys) or len(set(keys)) != len(keys):
            return _blocked('STABLE_ITEM_KEY_DUPLICATED')
        if scope_type != 'ALL_ITEMS':
            return _blocked('PROJECT_SCOPE_INVALID')
        return allocate_projects(policy, amount, eligible, currency_precision)

    selection = select_allocation_basis(fee, eligible)
    basis = selection["basis"]
    values = [
        (_item_key(row), Decimal("1") if basis in {"direct", "zero_amount"} else basis_decimal(row, basis))
        for row in eligible
    ]
    if any(not key for key, _value in values):
        return _blocked("STABLE_ITEM_KEY_REQUIRED")
    if len({key for key, _value in values}) != len(values):
        return _blocked("STABLE_ITEM_KEY_DUPLICATED")
    if selection.get("code"):
        return _blocked(**selection)
    if any(value is None or value < 0 for _key, value in values):
        return _blocked("ALLOCATION_BASIS_INCOMPLETE")
    denominator = sum((value for _key, value in values if value is not None), Decimal("0"))
    if denominator <= 0:
        return _blocked("ALLOCATION_DENOMINATOR_ZERO")

    allocated = allocate_with_stable_remainder(amount, values, precision=currency_precision, largest_remainder=final)
    zeros = format(Decimal("0"), f".{currency_precision}f")
    eligible_keys = [_item_key(row) for row in eligible]
    allocated["allocations"] = {
        _item_key(row): allocated["allocations"].get(_item_key(row), zeros)
        for row in items
    }
    allocated.update(
        {
            "amount": format(amount, "f"),
            "amount_status": status,
            "basis": basis,
            "preferred_basis": selection["preferred_basis"],
            "fallback_reason": selection["fallback_reason"],
            "scope_type": scope_type,
            "eligible_item_keys": sorted(eligible_keys),
            "scope_hash": _scope_hash(scope_type, eligible_keys),
        }
    )
    return allocated
